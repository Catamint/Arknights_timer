"""Public V2 runtime: immutable program plus independently owned session."""
import copy
import hashlib
from pathlib import Path
from ark_sim.contracts import Intent, digest, thaw
from ark_sim.kernel import Session
from ark_sim.domains.context import RuntimeContext
from ark_sim.domains.attributes import AttributeSystem
from ark_sim.domains.resources import ResourceSystem
from ark_sim.domains.effects import EffectSystem
from ark_sim.domains.buffs import BuffSystem
from ark_sim.domains.abilities import AbilitySystem
from ark_sim.domains.behavior import BehaviorSystem
from ark_sim.domains.movement import SpatialSystem, MovementSystem
from ark_sim.domains.lifecycle import LifecycleSystem


def implementation_digest():
    root = Path(__file__).resolve().parents[1]
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob("*.py"))})


class Simulation:
    def __init__(self, program, seed=0, providers=None, random_factory=None, random_registry=None, random_algorithm=None):
        self.program, self.seed = program, seed
        self.session = Session(quantum=program.ruleset["quantum"], seed=seed,
                               reaction_budget=program.ruleset.get("reaction_budget", 10000),
                               random_factory=random_factory, random_registry=random_registry, random_algorithm=random_algorithm)
        self.ctx = RuntimeContext(program, self.session, providers)
        self.ctx.attributes = AttributeSystem(self.ctx)
        self.ctx.resources = ResourceSystem(self.ctx)
        self.ctx.effects = EffectSystem(self.ctx)
        self.ctx.buffs = BuffSystem(self.ctx)
        self.ctx.abilities = AbilitySystem(self.ctx)
        self.ctx.behavior = BehaviorSystem(self.ctx)
        self.ctx.spatial = SpatialSystem(self.ctx)
        self.ctx.movement = MovementSystem(self.ctx)
        self.ctx.lifecycle = LifecycleSystem(self.ctx)
        self._commands = []
        self._register()
        self.runtime_fingerprint = digest({"implementation": implementation_digest(), "rules": self.ctx.rules.fingerprint,
                                          "random": self.session.random.fingerprint})
        resources = thaw(program.scenario.get("resources", {}))
        self.session.world.create("system/battle", {
            "attributes": {"base": {}, "modifiers": []},
            "resources": {key: {"current": spec.get("initial", 0), "spec": spec} for key, spec in resources.items()},
            "state": {"finished": False, "result": "running", "pending_waves": len(program.scenario.get("waves", ())),
                      "kills": 0, "leaks": 0, "damage_dealt": 0, "deployments": {}, "command_results": {}},
            "runtime": {"alive": True, "casts": {}}, "buffs": {"instances": []}}, tags=("system",), alias="system/battle")
        for item in program.scenario.get("initialEntities", ()):
            self.ctx.lifecycle.create(item["definition"], item.get("position"), item.get("facing", "right"),
                                      item.get("route"), item.get("instanceAlias"), deployed=item.get("deployed", False),
                                      component_overrides=item.get("components"), rule_overrides=item.get("rules"), tags=item.get("tags"))
        for wave in program.scenario.get("waves", ()):
            at = wave.get("at")
            if at is not None and "at_seconds" in wave:
                raise ValueError("a spawn wave must choose at or at_seconds")
            if at is None:
                at = self.ctx.quantize(wave.get("at_seconds", 0))
            self.session.schedule("domain.wave", thaw(wave), at, phase=0)
        for command in program.scenario.get("commands", ()):
            action = thaw(command)
            at = action.pop("at", None)
            seconds = action.pop("at_seconds", None)
            if at is not None and seconds is not None:
                raise ValueError("a scenario command must choose at or at_seconds")
            if at is None:
                at = self.ctx.quantize(0 if seconds is None else seconds)
            self._submit(action, at, record=False)

    def _register(self):
        handlers = {"domain.command": self._command, "domain.wave": self.ctx.lifecycle.spawn_wave,
                    "domain.effect": self.ctx.effects.handle, "event_reaction": self.ctx.react,
                    "domain.ability.effect": self.ctx.abilities.handle_effect,
                    "domain.ability.finish": self.ctx.abilities.finish,
                    "domain.buff.expire": self.ctx.buffs.expire, "domain.buff.periodic": self.ctx.buffs.periodic}
        for name, handler in handlers.items():
            self.session.register_handler(name, handler)
        systems = {"commands": lambda s: None, "resources": self.ctx.resources.tick,
                   "behavior": self.ctx.behavior.tick, "abilities": self.ctx.abilities.tick,
                   "movement": self.ctx.movement.tick, "lifecycle": self.ctx.lifecycle.tick}
        for index, name in enumerate(self.program.ruleset.get("system_order", ())):
            if name not in systems:
                raise ValueError(f"unsupported system {name}")
            if name != "commands":
                self.session.add_system(systems[name], phase=index+1)

    def _submit(self, action, at, record=True):
        if not isinstance(action, dict):
            raise ValueError("command must be an object")
        action = copy.deepcopy(action)
        with self.session._lock:
            task_id = self.session.schedule("domain.command", {"action": action}, at, phase=0)
            if record:
                self._commands.append({"order": len(self._commands)+1, "submitted_at": self.session.time,
                                       "at": at, "action": action})
        return task_id

    def submit(self, action, at=None):
        return self._submit(action, self.session.time if at is None else at)

    def _command(self, session, payload):
        action = payload["action"]
        try:
            with session.atomic():
                result = self._execute_command(action)
            session.emit("command.accepted", {"action": action, "result": result})
        except (ValueError, KeyError) as exc:
            session.emit("command.rejected", {"action": action, "reason": str(exc)})

    def _execute_command(self, action):
        session = self.session
        if self.ctx.state()["finished"]:
            raise ValueError("scenario already finished")
        kind = action.get("action", action.get("type"))
        if kind in ("activate_ability", "skill"):
            ref = session.world.resolve(action["source"])
            result = self.ctx.abilities.start(ref, action["ability"])
        elif kind == "deploy":
            result = self._deploy(action)
        elif kind in ("withdraw", "retreat"):
            ref = session.world.resolve(action["source"])
            if not self.ctx.alive(ref):
                raise ValueError("entity is not deployed")
            spec = self.ctx.get(ref, ("deployable",), {})
            refund = self.ctx.calc("deploy.refund", {"paid_cost": spec.get("paid_cost", 0), "reason": {"type": "withdraw"},
                "refund_parameters": {"ratio": spec.get("refund_ratio", 0.5)}}, source=ref,
                component=spec.get("rules", {}))
            resource = self.program.ruleset.get("parameters", {}).get("deployment_resource")
            if resource:
                self.ctx.resources.adjust("system/battle", resource, refund)
            self.ctx.lifecycle.retire(ref, "withdrawn")
            result = ref
        else:
            raise ValueError(f"unsupported command {kind}")
        return result

    def _deploy(self, action):
        definition_id = action.get("entity", action.get("definition"))
        definition = self.program.definitions.get(definition_id)
        if definition is None or definition.get("kind") != "entity":
            raise ValueError("unknown entity definition")
        components = definition.get("components", {})
        deployable = thaw(components.get("deployable", {}))
        prototype = {"definition_id": definition_id, "components": thaw(components), "tags": list(definition.get("tags", ()))}
        pending_scope = {"source": definition.get("rules", {})}
        position = action.get("position") or {"row": action.get("row"), "col": action.get("col")}
        row, col = position["row"], position["col"]
        if type(row) is not int or type(col) is not int:
            raise ValueError("deployment requires integer cell coordinates")
        inside = self.ctx.spatial.grid.inside(row, col)
        tile = self.ctx.spatial.grid.tile(row, col) if inside else {}
        actors = [thaw(e) for e in self.session.world.entities() if self.ctx.alive(e["id"]) and e["components"].get("deployable")]
        state = self.ctx.state()
        history = state["deployments"].get(definition_id, {"count": 0, "ready_at": 0})
        base = thaw(components.get("attributes", {}).get("base", {}))
        roles = self.program.ruleset.get("parameters", {}).get("attribute_roles", {})
        cost = self.ctx.calc("deploy.cost", {"base_cost": deployable.get("base_cost", deployable.get("cost", base.get(roles.get("deploy_cost"), 0))),
                     "deployment_history": history, "modifiers": []}, component=deployable.get("rules", {}),
                     scope_extra=pending_scope, extra={"source": prototype})
        capacity = self.ctx.calc("deploy.capacity", {"units": [e["id"] for e in actors],
                   "capacity_parameters": {"capacity": self.program.scenario.get("parameters", {}).get("deploy_capacity", 100)}})
        resource = self.program.ruleset.get("parameters", {}).get("deployment_resource")
        affordable = not resource or self.ctx.resources.current("system/battle", resource) >= cost
        decision = self.ctx.calc("deploy.eligibility", {"entity": {"definition_id": definition_id, "components": thaw(components)},
                  "location": position, "terrain": {"inside": inside, "buildable": tile.get("buildableType", 0),
                  "occupied": any(e["components"]["spatial"]["position"] == position for e in actors)},
                  "resources": {"affordable": affordable}, "states": {"finished": state["finished"],
                  "instances": sum(e["definition_id"] == definition_id for e in actors),
                  "cooldown": self.session.time < history.get("ready_at", 0), "at_capacity": len(actors) >= capacity}},
                  component=deployable.get("rules", {}),scope_extra=pending_scope,extra={"source": prototype})
        if not decision["accepted"]:
            raise ValueError(decision["reason"])
        if resource:
            self.ctx.resources.adjust("system/battle", resource, -cost)
        ref = self.ctx.lifecycle.create(definition_id, position, action.get("facing", "right"), alias=action.get("alias"), deployed=True)
        deployable["paid_cost"] = cost
        self.ctx.set(ref, ("deployable",), deployable)
        history["count"] += 1
        state = self.ctx.state()
        state["deployments"][definition_id] = history
        self.ctx.state_update(**state)
        return ref

    def advance(self, ticks):
        self.session.advance(ticks)
        return self.snapshot()

    def snapshot(self):
        return {"time": self.session.time, "seconds": self.session.time*self.session.quantum,
                "scenario": self.program.scenario["id"], "program_fingerprint": self.program.fingerprint,
                "runtime_fingerprint": self.runtime_fingerprint,
                "entities": [thaw(e) for e in self.session.world.entities()], "state": self.ctx.state(),
                "events": thaw(self.session.events)}

    def checkpoint(self):
        return {"schema": "ark-sim/session-checkpoint/v2", "program_fingerprint": self.program.fingerprint,
                "runtime_fingerprint": self.runtime_fingerprint, "kernel": self.session.checkpoint(),
                "commands": copy.deepcopy(self._commands)}

    def export_replay(self):
        return {"schema": "ark-sim/replay/v2", "program_fingerprint": self.program.fingerprint,
                "runtime_fingerprint": self.runtime_fingerprint, "seed": self.seed,
                "random_algorithm": self.session.random.algorithm,
                "until": self.session.time, "commands": copy.deepcopy(self._commands)}

    def explain(self, event_id):
        return next((thaw(e) for e in self.session.events if e["id"] == event_id), None)


class Engine:
    @staticmethod
    def create(program, seed=0, **kwargs):
        return Simulation(program, seed, **kwargs)

    @staticmethod
    def restore(program, checkpoint, **kwargs):
        if checkpoint.get("schema") != "ark-sim/session-checkpoint/v2" or checkpoint.get("program_fingerprint") != program.fingerprint:
            raise ValueError("checkpoint program identity mismatch")
        kwargs.setdefault("random_algorithm", checkpoint["kernel"]["random"]["algorithm"])
        simulation = Simulation(program, seed=checkpoint["kernel"]["random"]["seed"], **kwargs)
        if checkpoint["runtime_fingerprint"] != simulation.runtime_fingerprint:
            raise ValueError("checkpoint runtime identity mismatch")
        simulation.session.restore(checkpoint["kernel"])
        simulation._commands = copy.deepcopy(checkpoint.get("commands", []))
        return simulation
