"""Composable effects: collect rule results, then submit state intents."""
import math
from ark_sim.contracts import Intent, thaw
from ark_sim.rules import evaluate_expression


class EffectSystem:
    def __init__(self, context):
        self.ctx = context

    def targets(self, source, targets, mode):
        if mode in ("source", "self"):
            return [source]
        if mode in ("scenario", "battle"):
            return [self.ctx.session.world.resolve("system/battle")]
        if mode in ("selected", "target"):
            return list(targets)
        if isinstance(mode, int):
            return [self.ctx.session.world.resolve(mode)]
        raise ValueError(f"unsupported effect target {mode}")

    def execute(self, source, targets, effect, ability=None, cast=None, cause=None):
        ability, cast = ability or {}, cast or {}
        condition = effect.get("condition")
        if condition and not evaluate_expression(condition, {"source": self.ctx.entity(source),
                "targets": [self.ctx.entity(t) for t in targets], "time": self.ctx.session.time}, effect.get("parameters", {})):
            return
        if effect.get("selector"):
            targets = self.ctx.spatial.select(source, effect["selector"], ability=ability, effect=effect)
        selected = self.targets(source, targets, effect.get("target", "selected"))
        for target in selected:
            operation = effect["op"]
            if operation in ("damage", "heal"):
                if not self.ctx.alive(target):
                    continue
                if self._projectile(source, target, effect, ability, cast, cause):
                    continue
                actual = self._settle(source, target, effect, ability, cast, cause)
                if actual is None:
                    continue
                for child in effect.get("on_success", ()):
                    self.execute(source, [target], child, ability, cast, cause)
                sp = ability.get("activation", {}).get("parameters", {}).get("sp_resource")
                recovery = ability.get("activation", {}).get("parameters", {}).get("recovery_per_attack", 0)
                if operation == "damage" and sp and recovery and self.ctx.alive(source):
                    self.ctx.resources.adjust(source, sp, recovery, source=source, ability=ability, effect=effect)
            elif operation == "modify_resource":
                self.ctx.resources.adjust(target, effect["resource"], effect.get("delta", effect.get("amount", 0)),
                    source=source, ability=ability, effect=effect)
            elif operation == "apply_buff":
                self.ctx.buffs.apply(source, target, effect["buff"], effect.get("stacks", 1))
            elif operation == "remove_buff":
                if effect.get("remove_all"):
                    for item in self.ctx.get(target, ("buffs", "instances"), []):
                        self.ctx.buffs.remove(target, item["id"])
                else:
                    self.ctx.buffs.remove(target, effect.get("buff"))
            elif operation == "emit":
                self.ctx.emit(effect.get("event", effect.get("type", "custom")),
                    {**effect.get("payload", {}), "source": source, "target": target}, cause)
            elif operation in ("state", "transition"):
                self.ctx.behavior.transition(target, effect["state"], cause=cause)
            elif operation in ("move", "displace"):
                self.ctx.movement.displace(source, target, effect, ability)
            elif operation == "spawn":
                self.ctx.lifecycle.create(effect["definition"], effect.get("position"), effect.get("facing", "right"))
            elif operation == "schedule":
                self.ctx.session.schedule("domain.effect", {"source": source, "targets": [target],
                    "effect": thaw(effect["effect"]), "ability": thaw(ability), "cast": thaw(cast), "cause": cause},
                    self.ctx.session.time + self.ctx.quantize(effect.get("delay_seconds", effect.get("at_seconds", 0))),
                    phase=self.ctx.effect_phase)
            else:
                raise ValueError(f"unimplemented effect {operation}")
            for child in effect.get("effects", ()):
                self.execute(source, [target], child, ability, cast, cause)

    def handle(self, session, payload):
        self.execute(payload["source"], payload["targets"], payload["effect"],
                     payload.get("ability"), payload.get("cast"), payload.get("cause"))

    def _projectile(self, source, target, effect, ability, cast, cause):
        parameters = ability.get("parameters", {})
        speed = parameters.get("projectile_speed", 0)
        if not speed or cast.get("projectile_impact"):
            return False
        a = self.ctx.get(source, ("spatial", "position"))
        b = self.ctx.get(target, ("spatial", "position"))
        distance = math.hypot(a["row"]-b["row"], a["col"]-b["col"])
        velocity = self.ctx.calc("projectile.speed", {"projectile": parameters, "attributes": {},
                    "parameters": {"speed": speed}}, source=source, target=target, ability=ability, effect=effect)
        duration = self.ctx.calc("projectile.flight_time", {"distance": distance, "speed": velocity,
                    "timing_parameters": {}}, source=source, target=target, ability=ability, effect=effect)
        launched = dict(cast, projectile_impact=True)
        launched["launch_snapshot"] = self.ctx.capture_view(source)
        launched["launch_target_snapshots"] = {str(target): self.ctx.capture_view(target)}
        self.ctx.session.schedule("domain.effect", {"source": source, "targets": [target],
            "effect": thaw(effect), "ability": thaw(ability), "cast": launched, "cause": cause},
            self.ctx.session.time+self.ctx.quantize(duration), phase=self.ctx.effect_phase)
        self.ctx.emit("projectile.launched", {"source": source, "target": target,
                      "ability": ability.get("id"), "flight_seconds": duration}, cause)
        return True

    def _settle(self, source, target, effect, ability, cast, cause):
        mode = effect.get("read_mode", {}).get("source_attributes", "at_hit")
        snapshot = cast.get("source_snapshot") if mode == "at_cast" else cast.get("launch_snapshot") if mode == "at_launch" else None
        resource = effect.get("resource") or self.ctx.health_resource(target)
        if effect["op"] == "heal":
            attack_key = self.ctx.attribute_role("attack")
            attack = self.ctx.attributes.value(source, attack_key, ability=ability, effect=effect, snapshot=snapshot)
            amount = self.ctx.calc("healing.base", {"attack": attack, "scale": effect.get("scale", 1),
                 "additions": effect.get("additions", 0)}, source=source, target=target, ability=ability, effect=effect)
            actual = self.ctx.resources.adjust(target, resource, amount, source=source, ability=ability, effect=effect)
            self.ctx.emit("healing.accepted", {"source": source, "target": target, "amount": actual}, cause)
            return actual
        scope = {"scenario": self.ctx.program.scenario.get("rules", {}) if hasattr(self.ctx.program, "scenario") else {},
                 "ability": ability.get("rules", {}), "effect": effect.get("rules", {})}
        if hasattr(self.ctx, "definition_bindings"):
            scope.update(source=self.ctx.definition_bindings(source), target=self.ctx.definition_bindings(target))
        rule_id, _ = self.ctx.rules.resolver.resolve("damage.pipeline", scope)
        rule = self.ctx.rules.rules[rule_id]
        request = {**thaw(effect), "scale": effect.get("scale", 1),
                   "additions": effect.get("additions", 0),
                   "damage_type": effect.get("damage_type", "physical")}
        bindings = rule.get("metadata", {}).get("input_bindings", {})
        for name, binding in bindings.items():
            source_role = binding["entity"] == "source"
            ref = source if source_role else target
            mode_key = "source_attributes" if source_role else "target_attributes"
            read_mode = effect.get("read_mode", {}).get(mode_key, "at_hit")
            if source_role:
                view = snapshot
            else:
                field = "target_snapshots" if read_mode == "at_cast" else "launch_target_snapshots" if read_mode == "at_launch" else None
                view = cast.get(field, {}).get(str(target)) if field else None
            role = binding.get("attribute_role")
            key = binding.get("attribute") or self.ctx.attribute_role(role)
            base = (view or self.ctx.entity(ref))["components"].get("attributes", {}).get("base", {})
            if key in base:
                request[name] = self.ctx.attributes.value(ref, key, ability=ability, effect=effect, snapshot=view)
            else:
                defaults = self.ctx.program.ruleset.get("parameters", {}).get("attribute_defaults", {})
                if key not in defaults:
                    raise ValueError(f"required pipeline attribute {key} is missing")
                request[name] = defaults[key]
        settlement = self.ctx.calc("damage.pipeline", {"source": self.ctx.entity(source),
             "target": self.ctx.entity(target), "effect": request, "samples": [], "states": {}},
             source=source, target=target, ability=ability, effect=effect)
        if not settlement["accepted"]:
            for child in effect.get("on_failure", ()):
                self.execute(source, [target], child, ability, cast, cause)
            return None
        if settlement.get("allocations"):
            intents, notifications, actual = [], [], 0
            totals = {}
            for allocation in settlement["allocations"]:
                recipient = allocation.get("target", target)
                recipient = target if recipient == "target" else source if recipient == "source" else recipient
                key = allocation.get("resource", resource)
                change = allocation.get("delta", -allocation.get("amount", 0))
                group = (recipient, key)
                totals[group] = totals.get(group, 0)+change
            for (recipient, key), change in totals.items():
                plan, delta = self.ctx.resources.change_plan(recipient, key, change, source=source, ability=ability, effect=effect)
                intents.extend(plan)
                notifications.append((recipient, key, delta))
                actual -= delta
            self.ctx.session.commit(intents)
            for recipient, key, delta in notifications:
                self.ctx.emit("resource.changed", {"source": source, "target": recipient, "resource": key, "delta": delta})
                self.ctx.lifecycle.check(recipient, {"resource": key, "delta": delta})
        else:
            actual = -self.ctx.resources.adjust(target, resource, -settlement["amount"], source=source, ability=ability, effect=effect)
        state = self.ctx.state()
        state["damage_dealt"] = state.get("damage_dealt", 0)+actual
        self.ctx.state_update(**state)
        self.ctx.emit("damage.accepted", {"source": source, "target": target,
                     "amount": actual, "ability": ability.get("id"), "resource": resource}, cause)
        for event in settlement.get("events", ()):
            self.ctx.emit(event["type"], event.get("payload", {}), cause)
        return actual
