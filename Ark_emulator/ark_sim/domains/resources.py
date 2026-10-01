"""Generic named resources and atomic multi-resource payment plans."""
import math
from collections.abc import Mapping
from ark_sim.contracts import Intent


class ResourceSystem:
    def __init__(self, context):
        self.ctx = context

    def current(self, ref, resource):
        value = self.ctx.get(ref, ("resources", resource))
        if value is None:
            raise ValueError(f"resource {resource!r} is absent on {ref}")
        return value["current"]

    def _spec(self, ref, resource):
        data = self.ctx.get(ref, ("resources", resource))
        if data is None:
            raise ValueError(f"resource {resource!r} is absent on {ref}")
        return data["spec"]

    def capacity(self, ref, resource, *, source=None, ability=None, effect=None):
        spec = self._spec(ref, resource)
        attributes = self.ctx.attributes.values(ref, ability=ability, effect=effect)
        parameters = dict(spec.get("parameters", {}))
        if "capacity_attribute" in spec:
            parameters["capacity"] = attributes[spec["capacity_attribute"]]
        elif "capacity" in spec:
            parameters["capacity"] = spec["capacity"]
        # A custom capacity formula may use attributes alone. Missing capacity
        # remains missing, so a preset needing it produces an explicit error.
        return self.ctx.calc("resource.capacity", {"attributes": attributes, "capacity_parameters": parameters},
                            source=source, target=ref, owner=ref, ability=ability, effect=effect,
                            local=spec.get("rules", {}), rule_id=spec.get("capacity_rule"),
                            extra={"resource": resource})

    def change_plan(self, ref, resource, delta=None, *, value=None, source=None, ability=None, effect=None):
        current = self.current(ref, resource)
        if value is None and delta is None:
            raise ValueError("resource change requires value or delta")
        if value is not None and delta is not None:
            raise ValueError("resource change must specify only value or delta")
        candidate = value if value is not None else current + delta
        spec = self._spec(ref, resource)
        settlement = self.ctx.calc("resource.bounds", {"candidate": candidate,
                    "capacity": self.capacity(ref, resource, source=source, ability=ability, effect=effect),
                    "bounds_parameters": spec.get("parameters", {})},
                    source=source, target=ref, owner=ref, local=spec.get("rules", {}),
                    ability=ability, effect=effect, rule_id=spec.get("bounds_rule"), extra={"resource": resource})
        if not settlement["accepted"]:
            raise ValueError(f"resource update rejected: {ref}.{resource}")
        actual = settlement["value"] - current
        intent = Intent("set", self.ctx.session.world.resolve(ref), ("resources", resource, "current"), settlement["value"])
        return [intent], actual

    def adjust(self, ref, resource, delta=None, *, value=None, source=None, ability=None, effect=None):
        intents, actual = self.change_plan(ref, resource, delta, value=value, source=source, ability=ability, effect=effect)
        self._commit_change(ref, resource, intents, actual, source)
        return actual

    def _commit_change(self, ref, resource, intents, actual, source):
        self.ctx.session.commit(intents)
        self.ctx.emit("resource.changed", {"source": source, "target": ref, "resource": resource,
                                         "delta": actual, "value": self.current(ref, resource)})
        if self.ctx.lifecycle:
            self.ctx.lifecycle.check(ref, {"resource": resource, "delta": actual})

    def payment_plan(self, ref, costs, *, ability=None, effect=None):
        costs = tuple(costs)
        if not costs:
            return []
        totals = {}
        attributes = self.ctx.attributes.values(ref, ability=ability, effect=effect)
        for cost in costs:
            resource = cost["resource"]
            spec = self._spec(ref, resource)
            parameters = {**spec.get("parameters", {}), **cost.get("parameters", {})}
            if "amount" in cost:
                parameters["amount"] = cost["amount"]
            amount = self.ctx.calc("resource.cost", {"ability": dict(ability or {}), "attributes": attributes,
                 "cost_parameters": parameters}, source=ref, owner=ref, local=spec.get("rules", {}),
                 ability=ability, effect=effect, rule_id=cost.get("rule"), extra={"resource": resource})
            totals[resource] = totals.get(resource, 0) + amount
        for key, amount in totals.items():
            if self.current(ref, key) < amount:
                raise ValueError(f"insufficient resource {key}")
        intents = []
        for key, amount in totals.items():
            planned, _ = self.change_plan(ref, key, -amount, source=ref, ability=ability, effect=effect)
            intents.extend(planned)
        return intents

    def _recovery_frozen(self, entity, spec):
        parameters = spec.get("parameters", {})
        enabled = parameters.get("freeze_while_cast", False)
        if not isinstance(enabled, bool):
            raise ValueError("freeze_while_cast must be boolean")
        if not enabled:
            return False
        casts = entity["components"].get("runtime", {}).get("casts", {})
        if not casts:
            return False
        modes = parameters.get("freeze_cast_modes")
        if modes is None:
            return True
        if not isinstance(modes, (list, tuple)) or not all(isinstance(mode, str) for mode in modes):
            raise ValueError("freeze_cast_modes must be a list of activation modes")
        for cast in casts.values():
            mode = cast.get("activation_mode")
            if mode is None:
                definition = self.ctx.program.definitions.get(cast.get("ability"), {})
                mode = definition.get("activation", {}).get("mode")
            if mode is None:
                raise ValueError(f"Cannot determine active cast mode: {cast.get('id', cast.get('ability'))}")
            if mode in modes:
                return True
        return False

    def _recovery_model(self, spec):
        configuration = spec.get("recovery", {})
        if not isinstance(configuration, Mapping):
            raise ValueError("recovery must be a model definition")
        mode = configuration.get("mode", "continuous")
        allowed = {"mode"} if mode == "continuous" else {"mode", "interval_seconds"}
        if mode not in ("continuous", "periodic"):
            raise ValueError(f"unimplemented recovery mode: {mode}")
        if set(configuration) - allowed:
            raise ValueError(f"unsupported {mode} recovery fields: {sorted(set(configuration) - allowed)}")
        if mode == "continuous":
            return mode, None, None
        interval = configuration.get("interval_seconds")
        if (isinstance(interval, bool) or not isinstance(interval, (int, float))
                or not math.isfinite(interval) or interval <= 0):
            raise ValueError("periodic recovery interval_seconds must be a positive finite number")
        units = self.ctx.quantize(interval)
        if type(units) is not int or units < 1:
            raise ValueError("periodic recovery interval must advance logical time")
        return mode, interval, units

    def _recover(self, ref, resource, spec, delta_seconds):
        parameters = dict(spec.get("parameters", {}))
        if "recovery_rate" in spec:
            parameters["rate"] = spec["recovery_rate"]
        return self.ctx.calc("resource.recovery", {"current": self.current(ref, resource),
            "delta_seconds": delta_seconds, "attributes": self.ctx.attributes.values(ref),
            "parameters": parameters}, source=ref, owner=ref, local=spec.get("rules", {}),
            rule_id=spec.get("recovery_rule"), extra={"resource": resource})

    def tick(self, session):
        if self.ctx.state().get("finished"):
            return
        for entity in session.world.entities():
            ref = entity["id"]
            if not self.ctx.alive(ref):
                continue
            for key, data in entity["components"].get("resources", {}).items():
                spec = data["spec"]
                rule = spec.get("recovery_rule")
                if (rule is None and "resource.recovery" not in spec.get("rules", {})
                        and "recovery_rate" not in spec and "recovery" not in spec):
                    continue
                if self._recovery_frozen(entity, spec):
                    continue
                pause = spec.get("parameters", {}).get("pause_at_full", False)
                if not isinstance(pause, bool):
                    raise ValueError("pause_at_full must be boolean")
                if pause and self.current(ref, key) >= self.capacity(ref, key):
                    continue
                mode, interval, interval_units = self._recovery_model(spec)
                if mode == "continuous":
                    self.adjust(ref, key, value=self._recover(ref, key, spec, session.quantum), source=ref)
                    continue
                timing = dict(data.get("timing", {}))
                elapsed = timing.get("elapsed_units", 0)
                if type(elapsed) is not int or elapsed < 0:
                    raise ValueError("periodic elapsed_units must be a nonnegative integer")
                elapsed += 1
                cycles, remainder = divmod(elapsed, interval_units)
                timing["elapsed_units"] = remainder
                timer_intent = Intent("set", ref, ("resources", key, "timing"), timing)
                if not cycles:
                    session.commit([timer_intent])
                    continue
                if cycles > session.reaction_budget:
                    raise ValueError("periodic recovery catch-up exceeds reaction budget")
                for _ in range(cycles):
                    if not self.ctx.alive(ref):
                        break
                    recovered = self._recover(ref, key, spec, interval)
                    plan, actual = self.change_plan(ref, key, value=recovered, source=ref)
                    # A failed calculation or settlement cannot advance this
                    # period's timer without applying its resource change.
                    self._commit_change(ref, key, plan + [timer_intent], actual, ref)
