"""Definition-driven abilities and atomic resource/timeline activation."""
from ark_sim.contracts import Intent, thaw
from ark_sim.rules.expressions import evaluate_expression


class ActivationRejected(ValueError):
    """A valid ability is temporarily unable to activate."""


class AbilitySystem:
    def __init__(self, context):
        self.ctx = context

    def _definition(self, ability_id):
        definition = self.ctx.program.definitions[ability_id]
        if definition.get("kind") != "ability":
            raise ValueError(f"Not an ability: {ability_id}")
        return thaw(definition)

    def _params(self, ability):
        return {**ability.get("parameters", {}), **ability.get("activation", {}).get("parameters", {})}

    def _condition(self, condition, source, ability, payload=None):
        return not condition or bool(evaluate_expression(condition,
            {"source": self.ctx.entity(source), "payload": payload or {},
             "resources": self.ctx.get(source, ("resources",), {})}, self._params(ability),
            {"source": self.ctx.entity(source), "time": self.ctx.session.time, "ability": ability}))

    def _seconds(self, calculation, source, ability, parameter, seconds):
        attrs = self.ctx.attributes.values(source)
        return self.ctx.calc(calculation, {"attributes": attrs, "animation": {}, parameter: {"seconds": seconds}},
                             source=source, ability=ability)

    def start(self, source, ability_id, automatic=False, event_payload=None, cause=None):
        with self.ctx.session.atomic():
            source = self.ctx.session.world.resolve(source)
            ability = self._definition(ability_id)
            if ability_id not in self.ctx.get(source, ("abilities",), []):
                raise ValueError(f"Entity does not possess ability: {ability_id}")
            if not self.ctx.alive(source) or self.ctx.state().get("finished"):
                raise ActivationRejected("Ability source is not active")
            runtime = self.ctx.get(source, ("runtime",), {})
            now = self.ctx.session.time
            if runtime.get("cooldowns", {}).get(ability_id, 0) > now:
                raise ActivationRejected("Ability is on cooldown")
            params, activation = self._params(ability), ability.get("activation", {})
            if not self._condition(activation.get("condition"), source, ability, event_payload):
                raise ActivationRejected("Ability activation condition rejected")
            mode = activation.get("mode", "manual")
            if mode in {"passive", "automatic_attack", "on_deploy"} and not automatic:
                raise ActivationRejected("Automatic abilities activate only through their registered systems/events")
            policy = ability.get("interrupt_policy", {})
            if policy.get("refund", "none") != "none" or policy.get("on_source_death", "cancel_remaining") != "cancel_remaining":
                raise ValueError("Ability interrupt policy requires an implemented refund/continuation adapter")
            blocks = params.get("blocks_attacks", mode != "passive")
            casts = runtime.get("casts", {})
            if blocks and any(cast.get("blocks_attacks", True) for cast in casts.values()):
                raise ActivationRejected("Another blocking ability is active")
            if any(cast["ability"] == ability_id for cast in casts.values()):
                raise ActivationRejected("Ability is already active")
            targets = self.ctx.spatial.select(source, ability["selector"], ability=ability) if ability.get("selector") else [source]
            requires_targets = mode == "automatic_attack" or params.get("replace_attack") or params.get("requires_targets", False)
            if requires_targets and not targets:
                raise ActivationRejected("No legal ability target")
            # Payment planning validates all resource costs before touching state.
            resources_before = {cost["resource"]: self.ctx.resources.current(source, cost["resource"])
                                for cost in activation.get("costs", [])}
            try:
                payment = self.ctx.resources.payment_plan(source, activation.get("costs", []), ability=ability)
            except ValueError as exc:
                if str(exc).startswith("insufficient resource"):
                    raise ActivationRejected(str(exc)) from exc
                raise
            attrs = self.ctx.attributes.values(source)
            prepared = []
            pre_delay = activation.get("parameters", {}).get("windup_seconds", params.get("pre_delay_seconds", 0))
            last_delay = 0
            for entry in ability.get("timeline", ()):
                if "at" in entry and "at_seconds" in entry:
                    raise ValueError("Ability timeline must choose either at or at_seconds")
                if "at" in entry:
                    if type(entry["at"]) is not int or entry["at"] < 0:
                        raise ValueError("Ability timeline at must be a nonnegative integer logic offset")
                    offset_seconds = entry["at"] * self.ctx.session.quantum
                else:
                    offset_seconds = entry.get("at_seconds", 0)
                delay = self._seconds("ability.windup", source, ability, "timing_parameters", pre_delay + offset_seconds)
                if delay < 0:
                    raise ValueError("Ability windup rule returned a negative delay")
                repeat = entry.get("repeat", {})
                plan = self.ctx.calc("ability.repeat", {"attributes": attrs,
                        "repeat_parameters": {**thaw(repeat.get("parameters", {})), "count": repeat.get("count", 1),
                                              "interval_seconds": repeat.get("interval_seconds", 0)}},
                        source=source, ability=ability, rule_id=repeat.get("rule"))
                if type(plan.get("count")) is not int or not 0 <= plan["count"] <= 10000:
                    raise ValueError("Repeat rule must return a bounded nonnegative integer count")
                if plan["interval"] < 0:
                    raise ValueError("Repeat interval cannot be negative")
                delay_units = self.ctx.quantize(delay)
                repeat_units = self.ctx.quantize(plan["interval"])
                effects = entry.get("effects", ()) or ([entry["effect"]] if "effect" in entry else [])
                for index in range(plan["count"]):
                    relative = delay_units + index * repeat_units
                    last_delay = max(last_delay, relative)
                    for effect in effects:
                        prepared.append({"at": now + relative, "effect": thaw(effect), "condition": entry.get("condition")})
            duration = self.ctx.calc("ability.duration", {"attributes": attrs,
                    "duration_parameters": {"seconds": ability.get("duration_seconds", activation.get("duration_seconds", 0))}},
                    source=source, ability=ability)
            if duration < 0:
                raise ValueError("Ability duration cannot be negative")
            finish_at = now + max(last_delay, self.ctx.quantize(duration))
            recovery = self._seconds("ability.recovery", source, ability, "recovery_parameters",
                ability.get("cooldown_seconds", activation.get("cooldown_seconds", params.get("cooldown_seconds", 0))))
            if recovery < 0:
                raise ValueError("Ability recovery cannot be negative")
            sequence = runtime.get("next_cast_id", 1)
            cast_id = f"cast/{source}/{sequence}"
            next_task = self.ctx.session.scheduler.snapshot()["next_id"]
            cast = {"id": cast_id, "source": source, "ability": ability_id, "targets": targets,
                    "activation_mode": mode,
                    "source_snapshot": getattr(self.ctx, "capture_view", self.ctx.entity)(source),
                    "started_at": now, "finish_at": finish_at,
                    "target_snapshots": {str(target): getattr(self.ctx, "capture_view", self.ctx.entity)(target) for target in targets},
                    "tasks": [], "blocks_attacks": bool(blocks), "automatic": bool(automatic),
                    "parameters": params, "generation": sequence}
            schedule = []
            for prepared_effect in prepared:
                cast["tasks"].append(next_task)
                next_task += 1
                schedule.append(Intent("schedule", data={"kind": "domain.ability.effect", "at": prepared_effect["at"],
                    "phase": self.ctx.effect_phase, "payload": {"source": source, "cast": cast_id,
                        "effect": prepared_effect["effect"], "condition": prepared_effect["condition"]}}))
            cast["tasks"].append(next_task)
            schedule.append(Intent("schedule", data={"kind": "domain.ability.finish", "at": finish_at,
                "phase": self.ctx.effect_phase, "payload": {"source": source, "cast": cast_id}}))
            runtime.setdefault("casts", {})[cast_id] = cast
            runtime["next_cast_id"] = sequence + 1
            runtime.setdefault("cooldowns", {})[ability_id] = finish_at + self.ctx.quantize(recovery)
            if mode == "automatic_attack" or params.get("replace_attack"):
                interval = self.ctx.calc("time.interval", {"base_interval": activation.get("interval_seconds",
                    self.ctx.role_value(source, "attack_interval")), "speed": self.ctx.role_value(source, "attack_speed_ratio"),
                    "adjustments": []}, source=source, ability=ability, rule_id=activation.get("interval_rule"))
                interval_units = self.ctx.quantize(interval)
                if interval_units < 1:
                    raise ValueError("Automatic attack interval must advance logical time")
                runtime["next_attack"] = now + interval_units
            self.ctx.session.commit(payment + [Intent("set", source, ("runtime",), runtime)] + schedule)
            started_event = self.ctx.emit("ability.started", {"source": source, "ability": ability_id, "cast": cast_id, "targets": targets}, cause=cause)
            for resource, before in resources_before.items():
                current = self.ctx.resources.current(source, resource)
                event = {"source": source, "target": source, "resource": resource,
                         "delta": current - before, "value": current, "ability": ability_id, "cast": cast_id,
                         "reason": "ability_cost"}
                self.ctx.emit("resource.changed", event, cause=started_event)
                if self.ctx.lifecycle is not None:
                    self.ctx.lifecycle.check(source, event)
            return cast_id

    def _active(self, source, cast_id):
        try:
            return self.ctx.get(source, ("runtime", "casts"), {}).get(cast_id)
        except KeyError:
            return None

    def handle_effect(self, session, payload):
        source, cast_id = payload["source"], payload["cast"]
        cast = self._active(source, cast_id)
        if not cast or not self.ctx.alive(source):
            return
        ability = self._definition(cast["ability"])
        if not self._condition(payload.get("condition"), source, ability):
            return
        targets = cast["targets"]
        if ability.get("target_capture") == "each_hit" and ability.get("selector"):
            targets = self.ctx.spatial.select(source, ability["selector"], ability=ability, effect=payload["effect"])
        self.ctx.effects.execute(source, targets, payload["effect"], ability=ability, cast=cast)

    def finish(self, session, payload):
        source, cast_id = payload["source"], payload["cast"]
        cast = self._active(source, cast_id)
        if not cast:
            return
        casts = self.ctx.get(source, ("runtime", "casts"), {})
        del casts[cast_id]
        self.ctx.set(source, ("runtime", "casts"), casts)
        self.ctx.emit("ability.finished", {"source": source, "ability": cast["ability"], "cast": cast_id})

    def interrupt(self, source, reason):
        with self.ctx.session._lock:
            source = self.ctx.session.world.resolve(source)
            casts = self.ctx.get(source, ("runtime", "casts"), {})
            pending = {task["id"] for task in self.ctx.session.scheduler.pending}
            intents = [Intent("cancel", data={"task_id": task_id}) for cast in casts.values()
                       for task_id in cast["tasks"] if task_id in pending]
            intents.append(Intent("set", source, ("runtime", "casts"), {}))
            self.ctx.session.commit(intents)
            for cast in casts.values():
                self.ctx.emit("ability.interrupted", {"source": source, "ability": cast["ability"],
                                                      "cast": cast["id"], "reason": reason})
            return len(casts)

    def tick(self, session):
        if self.ctx.state().get("finished"):
            return
        for entity in session.world.entities():
            source = entity["id"]
            if not self.ctx.alive(source):
                continue
            runtime = self.ctx.get(source, ("runtime",), {})
            abilities = [self._definition(identifier) for identifier in entity["components"].get("abilities", ())]
            replace_ready = None
            for ability in abilities:
                mode = ability.get("activation", {}).get("mode")
                params = self._params(ability)
                if mode == "manual" and (params.get("auto_when_ready") or params.get("automatic")):
                    if params.get("replace_attack"):
                        try:
                            self.ctx.resources.payment_plan(source, ability["activation"].get("costs", []), ability=ability)
                        except ValueError as exc:
                            if str(exc).startswith("insufficient resource"):
                                continue
                            raise
                        replace_ready = ability
                    else:
                        try:
                            self.start(source, ability["id"], automatic=True)
                        except ActivationRejected:
                            pass
            runtime = self.ctx.get(source, ("runtime",), {})
            if runtime.get("behavior_decision", {}).get("attack") is False:
                continue
            if runtime.get("next_attack", 0) > session.time:
                continue
            for ability in abilities:
                if ability.get("activation", {}).get("mode") != "automatic_attack":
                    continue
                selected = replace_ready or ability
                try:
                    cast_id = self.start(source, selected["id"], automatic=True)
                except ActivationRejected:
                    if replace_ready:
                        try:
                            self.start(source, ability["id"], automatic=True)
                        except ActivationRejected:
                            continue
                        break
                    continue
                break

    def notify(self, event, payload, cause=None):
        for entity in self.ctx.session.world.entities():
            source = entity["id"]
            if not self.ctx.alive(source):
                continue
            for ability_id in entity["components"].get("abilities", ()):
                ability = self._definition(ability_id)
                for subscription in ability.get("events", ()):
                    if subscription["event"] != event:
                        continue
                    context = {"owner": self.ctx.entity(source), "source": self.ctx.entity(source),
                               "time": self.ctx.session.time, "ability": ability}
                    condition = subscription.get("condition")
                    if condition and not evaluate_expression(condition, {"event": event, "payload": payload},
                            {**self._params(ability), **subscription.get("parameters", {})}, context):
                        continue
                    target = payload.get("target")
                    try:
                        selected = [self.ctx.session.world.resolve(target)] if target is not None else [source]
                    except (KeyError, ValueError):
                        selected = [source]
                    for effect in subscription.get("effects", ()):
                        self.ctx.effects.execute(source, selected, thaw(effect), ability=ability, cause=cause)
                activation = ability.get("activation", {})
                events = activation.get("events", ()) or [activation.get("event")]
                mode = activation.get("mode")
                if mode == "on_deploy" and not activation.get("event") and not activation.get("events"):
                    events = ("entity.deployed", "unit.deployed", "deploy")
                if mode not in {"passive", "on_deploy"} or event not in events:
                    continue
                if mode == "on_deploy" and source not in (payload.get("source"), payload.get("target"), payload.get("entity")):
                    continue
                if not self._condition(activation.get("condition"), source, ability, payload):
                    continue
                try:
                    self.start(source, ability_id, automatic=True, event_payload=payload, cause=cause)
                except ActivationRejected:
                    continue
