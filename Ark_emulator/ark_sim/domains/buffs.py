"""Generic Buff instances, timers, modifiers and queued event reactions."""
from ark_sim.contracts import Intent, thaw
from ark_sim.rules.expressions import evaluate_expression


class BuffSystem:
    def __init__(self, context):
        self.ctx = context

    def _instances(self, target):
        return self.ctx.get(target, ("buffs", "instances"), [])

    def _pending(self):
        return {task["id"] for task in self.ctx.session.scheduler.pending}

    def _cancel_intents(self, instances):
        pending = self._pending()
        return [Intent("cancel", data={"task_id": task}) for item in instances
                for task in item.get("tasks", {}).values() if task in pending]

    def _modifiers(self, target, instances):
        modifiers = [item for item in self.ctx.get(target, ("attributes", "modifiers"), [])
                     if "buff_instance" not in item]
        for instance in instances:
            definition = self.ctx.program.definitions[instance["definition"]]
            for modifier in definition.get("modifiers", ()):
                modifiers.append({**thaw(modifier), "buff_instance": instance["id"],
                                  "stacks": instance["stacks"], "source": instance["source"]})
        return modifiers

    def apply(self, source, target, buff_id, stacks=1):
        with self.ctx.session._lock:
            source, target = self.ctx.session.world.resolve(source), self.ctx.session.world.resolve(target)
            if type(stacks) is not int or stacks < 1:
                raise ValueError("Incoming Buff stacks must be a positive integer")
            definition = self.ctx.program.definitions[buff_id]
            if definition.get("kind") != "buff":
                raise ValueError(f"Not a Buff definition: {buff_id}")
            policy = definition.get("stacking", {})
            if policy.get("policy"):
                raise ValueError("Custom Buff stacking policy requires an implemented policy adapter")
            mode = policy.get("mode", "refresh")
            if mode not in {"refresh", "independent", "add", "extend", "max"}:
                raise ValueError(f"Unsupported Buff stacking mode: {mode}")
            instances = self._instances(target)
            identity = policy.get("identity", ("definition", "source", "target"))
            incoming = {"definition": buff_id, "source": source, "target": target}
            if set(identity) - set(incoming):
                raise ValueError("Buff identity supports definition/source/target only")
            existing = None if mode == "independent" else next((item for item in instances
                if all(item.get(key) == incoming[key] for key in identity)), None)
            current = existing["stacks"] if existing else 0
            amount = self.ctx.calc("buff.stack_amount", {"current_stacks": current,
                "incoming": {"count": stacks, "mode": mode},
                "stack_parameters": {"max_stacks": policy.get("max_stacks", current + stacks), "mode": mode}},
                source=source, target=target, owner=target, local=definition.get("rules", {}), rule_id=policy.get("rule"))
            if type(amount) not in (int, float) or isinstance(amount, bool) or int(amount) != amount or amount < 1:
                raise ValueError("Buff stacking rule must return a positive integer stack amount")
            attrs = self.ctx.attributes.values(source)
            params = {**thaw(definition.get("parameters", {})), "duration_seconds": definition.get("duration_seconds", 0),
                      "interval_seconds": definition.get("interval_seconds", 0)}
            duration = self.ctx.calc("buff.duration", {"attributes": attrs, "buff_parameters": params},
                    source=source, target=target, local=definition.get("rules", {}), rule_id=definition.get("duration_rule"))
            interval = self.ctx.calc("buff.interval", {"attributes": attrs, "buff_parameters": params},
                    source=source, target=target, local=definition.get("rules", {}), rule_id=definition.get("interval_rule"))
            if duration < 0 or interval < 0:
                raise ValueError("Buff duration and interval must be nonnegative")
            duration_units, interval_units = self.ctx.quantize(duration), self.ctx.quantize(interval)
            next_instance = self.ctx.get(target, ("buffs", "next_instance_id"), 1)
            uid = existing["id"] if existing else f"buff/{target}/{next_instance}"
            generation = existing.get("generation", 0) + 1 if existing else 1
            expires = self.ctx.session.time + duration_units
            permanent = duration_units == 0 and "duration_seconds" not in definition and not definition.get("duration_rule")
            if permanent:
                expires = None
            elif existing and mode == "extend" and existing.get("expires_at") is not None:
                expires = existing["expires_at"] + duration_units
            elif existing and mode == "max" and existing.get("expires_at") is not None:
                expires = max(existing["expires_at"], expires)
            instance = {**incoming, "id": uid, "stacks": int(amount), "started_at": self.ctx.session.time,
                        "expires_at": expires, "interval_units": interval_units, "generation": generation,
                        "tasks": {}, "blackboard": thaw(existing.get("blackboard", {})) if existing else {}}
            next_task = self.ctx.session.scheduler.snapshot()["next_id"]
            scheduled = []
            for kind, at in (("expire", expires), ("periodic", self.ctx.session.time + interval_units if interval_units else None)):
                if at is None or (kind == "periodic" and expires is not None and at >= expires):
                    continue
                instance["tasks"][kind] = next_task
                next_task += 1
                scheduled.append(Intent("schedule", data={"kind": f"domain.buff.{kind}", "at": at,
                    "phase": self.ctx.effect_phase, "payload": {"target": target, "instance": uid, "generation": generation}}))
            replacement = [item for item in instances if existing is None or item["id"] != existing["id"]] + [instance]
            intents = self._cancel_intents([existing] if existing else [])
            intents.extend([Intent("set", target, ("buffs", "instances"), replacement),
                            Intent("set", target, ("attributes", "modifiers"), self._modifiers(target, replacement))])
            if existing is None:
                intents.append(Intent("set", target, ("buffs", "next_instance_id"), next_instance + 1))
            self.ctx.session.commit(intents + scheduled)
            self.ctx.emit("buff.applied", {"source": source, "target": target, "buff": buff_id,
                                          "instance": uid, "stacks": int(amount)})
            if interval_units == 0:
                for effect in definition.get("effects", ()):
                    self.ctx.effects.execute(source, [target], thaw(effect))
            return uid

    def remove(self, target, buff_or_instance):
        with self.ctx.session._lock:
            target = self.ctx.session.world.resolve(target)
            instances = self._instances(target)
            removed = [item for item in instances if item["id"] == buff_or_instance or item["definition"] == buff_or_instance]
            if not removed:
                return 0
            remaining = [item for item in instances if item not in removed]
            intents = self._cancel_intents(removed) + [Intent("set", target, ("buffs", "instances"), remaining),
                Intent("set", target, ("attributes", "modifiers"), self._modifiers(target, remaining))]
            self.ctx.session.commit(intents)
            for item in removed:
                self.ctx.emit("buff.removed", {"source": item["source"], "target": target,
                    "buff": item["definition"], "instance": item["id"]})
            return len(removed)

    def _active(self, payload):
        try:
            instances = self._instances(payload["target"])
        except KeyError:
            return None
        return next((item for item in instances if item["id"] == payload["instance"]
                     and item["generation"] == payload["generation"]), None)

    def expire(self, session, payload):
        if self._active(payload):
            self.remove(payload["target"], payload["instance"])

    def periodic(self, session, payload):
        instance = self._active(payload)
        if not instance:
            return
        if instance["interval_units"] < 1:
            return
        definition = self.ctx.program.definitions[instance["definition"]]
        try:
            session.world.resolve(instance["source"])
        except KeyError:
            self.remove(instance["target"], instance["id"])
            return
        if not self.ctx.alive(instance["target"]) and definition.get("removal", {}).get("on_target_death", "remove") == "remove":
            self.remove(instance["target"], instance["id"])
            return
        cause = self.ctx.emit("buff.periodic", {"source": instance["source"], "target": instance["target"],
                              "buff": instance["definition"], "instance": instance["id"]})
        for effect in definition.get("effects", ()):
            self.ctx.effects.execute(instance["source"], [instance["target"]], thaw(effect), cause=cause)
        instance = self._active(payload)
        if not instance:
            return
        at = session.time + instance["interval_units"]
        if instance["expires_at"] is not None and at >= instance["expires_at"]:
            return
        instances = self._instances(instance["target"])
        for item in instances:
            if item["id"] == instance["id"]:
                item["tasks"]["periodic"] = session.scheduler.snapshot()["next_id"]
        session.commit([Intent("set", instance["target"], ("buffs", "instances"), instances),
                        Intent("schedule", data={"kind": "domain.buff.periodic", "at": at,
                            "phase": self.ctx.effect_phase, "payload": payload})])

    def notify(self, event, payload, cause=None):
        for entity in self.ctx.session.world.entities():
            target = entity["id"]
            for instance in self._instances(target):
                definition = self.ctx.program.definitions[instance["definition"]]
                removal = definition.get("removal", {})
                try:
                    source_alive = self.ctx.alive(instance["source"])
                except KeyError:
                    self.remove(target, instance["id"])
                    continue
                if not self.ctx.alive(target) and removal.get("on_target_death", "remove") == "remove":
                    self.remove(target, instance["id"])
                    continue
                if not source_alive and removal.get("on_source_death", "retain") == "remove":
                    self.remove(target, instance["id"])
                    continue
                for subscription in definition.get("events", ()):
                    if subscription["event"] != event:
                        continue
                    condition = subscription.get("condition")
                    context = {"time": self.ctx.session.time, "owner": self.ctx.entity(target),
                               "source": self.ctx.entity(instance["source"]), "target": self.ctx.entity(target)}
                    if condition and not evaluate_expression(condition, {"event": event, "payload": payload, "buff": instance},
                            subscription.get("parameters", {}), context):
                        continue
                    for effect in subscription.get("effects", ()):
                        self.ctx.effects.execute(instance["source"], [target], thaw(effect), cause=cause)
