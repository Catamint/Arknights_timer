"""Creation and lifecycle changes follow definitions and rule decisions."""
from ark_sim.contracts import Intent, thaw


class LifecycleSystem:
    def __init__(self, context):
        self.ctx = context

    def create(self, definition_id, position=None, facing="right", route=None, alias=None, parameters=None,
               deployed=False, component_overrides=None, rule_overrides=None, tags=None):
        definition = self.ctx.program.definitions[definition_id]
        if definition.get("kind") != "entity":
            raise ValueError(f"{definition_id} is not an entity definition")
        components = thaw(definition.get("components", {}))
        def merge(base, overrides):
            for key, value in overrides.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    merge(base[key], value)
                else:
                    base[key] = thaw(value)
        merge(components, thaw(component_overrides or {}))
        self.ctx.attributes.initialize(definition, components, instance_rules=rule_overrides)
        resources = components.get("resources", {})
        components["resources"] = {key: {"current": spec.get("initial", 0), "spec": spec} for key, spec in resources.items()}
        spatial = components.setdefault("spatial", {})
        spatial["position"] = dict(position or spatial.get("position", {"row": 0, "col": 0}))
        spatial["facing"] = facing
        if route is not None:
            spatial["route"] = thaw(route)
        components["runtime"] = {"alive": True, "state": "alive", "deployed": deployed,
                                 "casts": {}, "cooldowns": {}, "next_attack": 0, "blocked_by": None,
                                 "rule_bindings": thaw(rule_overrides or {})}
        buff_config = components.get("buffs", components.pop("buff_container", {}))
        components["buffs"] = {**buff_config, "instances": [], "next_instance_id": 1}
        ref = self.ctx.session.commit([Intent("create", data={"definition_id": definition_id,
              "components": components, "tags": list(definition.get("tags", ()) if tags is None else tags), "alias": alias})])[0]
        for key in resources:
            self.ctx.resources.adjust(ref, key, value=components["resources"][key]["current"])
        for identifier in buff_config.get("initial", ()):
            self.ctx.buffs.apply(ref, ref, identifier)
        behavior = components.get("behavior", {})
        machine = self.ctx.program.definitions.get(behavior.get("machine"), {})
        if machine.get("states"):
            initial = machine.get("initial", machine.get("initial_state"))
            behavior["state"] = initial
            self.ctx.set(ref, ("behavior",), behavior)
            for effect in machine["states"][initial].get("on_enter", ()):
                self.ctx.effects.execute(ref, [ref], effect)
        self.ctx.emit("entity.created", {"source": ref, "target": ref, "definition": definition_id})
        if deployed:
            self.ctx.emit("entity.deployed", {"source": ref, "target": ref})
        return ref

    def check(self, ref, event):
        lifecycle = self.ctx.get(ref, ("lifecycle",), {})
        if not lifecycle.get("policy"):
            return
        policy = self.ctx.program.definitions[lifecycle["policy"]]
        parameters = {**thaw(policy.get("parameters", {})), **lifecycle.get("parameters", {})}
        plan = self.ctx.calc("lifecycle.death", {"resources": self.ctx.get(ref, ("resources",), {}),
               "damage_event": event, "states": self.ctx.get(ref, ("runtime",), {})}, target=ref,
               component=lifecycle.get("rules", {}), extra={"lifecycle_parameters": parameters})
        if plan["action"] == "death" and self.ctx.alive(ref):
            self.retire(ref, "dead")
        elif plan["action"] == "revive":
            self.ctx.set(ref, ("runtime", "alive"), True)
            self.ctx.set(ref, ("runtime", "state"), plan.get("state", "alive"))
            resource = plan["resource"]
            self.ctx.set(ref, ("resources", resource, "current"), plan["value"])
            self.ctx.emit("entity.revived", {"source": ref, "target": ref})

    def retire(self, ref, reason):
        if not self.ctx.alive(ref):
            return
        self.ctx.set(ref, ("runtime", "alive"), False)
        self.ctx.set(ref, ("runtime", "state"), reason)
        self.ctx.abilities.interrupt(ref, reason)
        self.ctx.set(ref, ("runtime", "blocked_by"), None)
        event = "entity.died" if reason == "dead" else "entity."+reason
        self.ctx.emit(event, {"source": ref, "target": ref})
        state = self.ctx.state()
        if reason == "dead" and "enemy" in self.ctx.entity(ref)["tags"]:
            state["kills"] += 1
        self.ctx.state_update(**state)
        deployable = self.ctx.get(ref, ("deployable",))
        if deployable:
            cooldown = deployable["cooldown_seconds"] if "cooldown_seconds" in deployable else self.ctx.role_value(ref, "redeploy_time")
            seconds = self.ctx.calc("deploy.cooldown", {"attributes": self.ctx.attributes.values(ref), "reason": {"type": reason},
                       "cooldown_parameters": {"seconds": cooldown}}, source=ref,
                       component=deployable.get("rules", {}))
            history = self.ctx.state()["deployments"]
            entry = history.setdefault(self.ctx.entity(ref)["definition_id"], {"count": 0})
            entry["ready_at"] = self.ctx.session.time+self.ctx.quantize(seconds)
            self.ctx.state_update(deployments=history)

    def exit(self, ref):
        params = self.ctx.get(ref, ("lifecycle",), {})
        loss = self.ctx.calc("lifecycle.leak_loss", {"entity": self.ctx.entity(ref), "exit": {},
                "leak_parameters": {"loss": params.get("leak_loss", 1)}})
        resource = self.ctx.program.scenario.get("objectives", {}).get("life_resource")
        if resource:
            self.ctx.resources.adjust("system/battle", resource, -loss)
        state = self.ctx.state()
        state["leaks"] += 1
        self.ctx.state_update(**state)
        self.retire(ref, "exited")

    def tick(self, session):
        state = self.ctx.state()
        if state.get("finished") or not self.ctx.program.scenario.get("objectives"):
            return
        result = self.ctx.calc("lifecycle.result", {"waves": {"pending": state["pending_waves"]},
             "entities": [e["id"] for e in session.world.entities()],
             "resources": self.ctx.get("system/battle", ("resources",), {}), "objectives": thaw(self.ctx.program.scenario["objectives"])},
             extra={"objectives": thaw(self.ctx.program.scenario["objectives"]),
                    "entity_states": [thaw(e) for e in session.world.entities()]})
        if result["finished"]:
            self.ctx.state_update(finished=True, result=result["result"], finished_at=session.time)
            self.ctx.emit("scenario.finished", result)

    def spawn_wave(self, session, wave):
        if self.ctx.state().get("finished"):
            return
        self.create(wave["definition"], wave.get("position"), wave.get("facing", "left"),
                    wave.get("route"), wave.get("instanceAlias"), wave.get("parameters"),
                    component_overrides=wave.get("components"), rule_overrides=wave.get("rules"), tags=wave.get("tags"))
        self.ctx.state_update(pending_waves=self.ctx.state()["pending_waves"]-1)
