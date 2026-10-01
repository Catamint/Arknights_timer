"""Read-only evaluations and atomic write helpers shared by domain systems."""
from ark_sim.contracts import Intent, thaw, freeze
from ark_sim.rules import RuleRuntime
from .providers import BUILTIN_PROVIDERS
from collections.abc import Mapping


def compact_trace(value):
    """Keep operands and results while avoiding repeated causal snapshots."""
    if isinstance(value, Mapping):
        result = {}
        for key, child in value.items():
            if key in ("source_snapshot", "target_snapshots", "launch_snapshot", "launch_target_snapshots"):
                continue
            if key == "context" and isinstance(child, Mapping):
                result[key] = {name: compact_trace(item) for name, item in child.items()
                    if name not in ("source", "target", "owner", "entity_states")}
                for name in ("source", "target", "owner"):
                    if isinstance(child.get(name), Mapping):
                        result[key][name+"_id"] = child[name].get("id")
            else:
                result[key] = compact_trace(child)
        return result
    if isinstance(value, (list, tuple)):
        return [compact_trace(item) for item in value]
    return value


class DomainError(ValueError):
    pass


class RuntimeContext:
    def __init__(self, program, session, providers=None):
        self.program, self.session = program, session
        self.providers = dict(BUILTIN_PROVIDERS, **(providers or {}))
        required = set(program.metadata.get("providers", {}))
        if "providers" in program.metadata:
            self.providers = {key: self.providers[key] for key in required}
        self.rules = RuleRuntime(program.rules, program.ruleset.get("bindings", {}),
                                 catalog=program.metadata.get("catalog"),
                                 numeric_profile=program.ruleset.get("numeric_profile"),
                                 providers=self.providers)
        expected = program.metadata.get("rule_runtime_fingerprint")
        if expected and expected != self.rules.fingerprint:
            raise DomainError("compiled provider/rule identity differs from runtime; recompile with the chosen providers")
        self.effect_phase = len(program.ruleset.get("system_order", ())) + 1
        self.attributes = self.resources = self.effects = self.abilities = self.buffs = self.spatial = self.behavior = self.lifecycle = None
        self._notifying = 0
        self.last_calculation_event_id = None

    def entity(self, ref):
        return self.session.world.get(ref)

    def capture_view(self, ref):
        view = thaw(self.entity(ref))
        runtime = view["components"].get("runtime", {})
        for cast in runtime.get("casts", {}).values():
            for key in ("source_snapshot", "target_snapshots", "launch_snapshot", "launch_target_snapshots"):
                cast.pop(key, None)
        return view

    def definition(self, ref):
        return self.program.definitions.get(self.entity(ref)["definition_id"], {})

    def get(self, ref, path, default=None):
        value = self.entity(ref)["components"]
        for part in path:
            if not isinstance(value, Mapping) or part not in value:
                return default
            value = value[part]
        return thaw(value)

    def set(self, ref, path, value):
        return self.session.commit([Intent("set", self.session.world.resolve(ref), tuple(path), value)])

    def state(self):
        return self.get("system/battle", ("state",))

    def state_update(self, **values):
        state = self.state()
        state.update(values)
        self.set("system/battle", ("state",), state)

    def definition_bindings(self, ref):
        return {**dict(self.definition(ref).get("rules", {})),
                **self.get(ref, ("runtime", "rule_bindings"), {})} if ref is not None else {}

    def calc(self, calculation, inputs, *, source=None, target=None, owner=None,
             ability=None, effect=None, component=None, local=None, rule_id=None, extra=None, scope_extra=None):
        scope = {"scenario": self.program.scenario.get("rules", {}),
                 "source": self.definition_bindings(source),
                 "target": self.definition_bindings(target),
                 "owner": self.definition_bindings(owner),
                 "component": component or {}, "attribute_or_resource": local or {},
                 "ability": (ability or {}).get("rules", {}),
                 "effect": (effect or {}).get("rules", {})}
        scope.update(scope_extra or {})
        context = {"time": self.session.time, "seconds": self.session.time*self.session.quantum,
                   "source": self.entity(source) if source is not None else {},
                   "target": self.entity(target) if target is not None else {},
                   "owner": self.entity(owner) if owner is not None else {},
                   **(extra or {})}
        result = self.rules.evaluate(calculation, inputs, scope=scope, rule_id=rule_id, context=context)
        if self.program.ruleset.get("parameters", {}).get("trace_mode", "compact") == "full":
            trace = thaw(result.trace)
        else:
            trace = compact_trace(result.trace)
        self.last_calculation_event_id = self.session.emit("calculation", {"calculation_id": calculation, "rule_id": result.rule_id,
                         "source": source, "target": target, "value": thaw(result.value),
                         "trace": trace})
        return thaw(result.value)

    def quantize(self, seconds):
        return self.calc("time.quantize", {"seconds": seconds, "quantum": self.session.quantum,
                         "rounding": {"mode": "ceil"}})

    def provider(self, name, inputs, params=None, context=None):
        record = self.providers.get(name)
        if record is None:
            raise DomainError(f"unavailable provider {name}")
        function = record.get("callable") if isinstance(record, dict) else record
        output = function(freeze(inputs), freeze(params or {}), freeze(context or {}))
        trace = {"provider": name, "inputs": inputs, "output": output}
        if self.program.ruleset.get("parameters", {}).get("trace_mode", "compact") != "full":
            trace = compact_trace(trace)
        self.session.emit("policy", trace)
        return thaw(output)

    def emit(self, event, payload, cause=None):
        event_id = self.session.emit(event, payload, cause=cause)
        if self.buffs is not None:
            self.session.schedule("event_reaction", {"event": event, "payload": payload, "cause": event_id},
                                  self.session.time, phase=self.effect_phase)
        return event_id

    def react(self, session, payload):
        self.buffs.notify(payload["event"], payload["payload"], payload["cause"])
        self.abilities.notify(payload["event"], payload["payload"], payload["cause"])

    def alive(self, ref):
        return bool(self.get(ref, ("runtime", "alive"), True))

    def attribute_role(self, role):
        return self.program.ruleset.get("parameters", {}).get("attribute_roles", {}).get(role, role)

    def role_value(self, ref, role):
        key = self.attribute_role(role)
        base = self.get(ref, ("attributes", "base"), {})
        if key not in base:
            defaults = self.program.ruleset.get("parameters", {}).get("attribute_defaults", {})
            if key not in defaults:
                raise DomainError(f"{ref} has no attribute for {role}: {key}")
            return defaults[key]
        return self.attributes.value(ref, key)

    def health_resource(self, ref):
        specs = self.get(ref, ("resources",), {})
        for key, value in specs.items():
            if value.get("spec", {}).get("role") == "health":
                return key
        configured = self.program.ruleset.get("parameters", {}).get("health_resource")
        if configured in specs:
            return configured
        raise DomainError(f"{ref} has no declared health resource")
