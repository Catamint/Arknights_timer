"""Strict V2 schemas for implemented content, with explicit extension metadata."""
import math
from collections.abc import Mapping
from .repository import ContentError


COMMON = {"id", "kind", "version", "extends", "metadata", "rules", "dependencies", "dynamicReferences"}
FIELDS = {
    "entity": {"tags", "components", "growth", "talents", "equipment", "progression"},
    "ability": {"activation", "selector", "timeline", "target_capture", "interrupt_policy", "success_definition", "duration_seconds", "cooldown_seconds", "events", "parameters"},
    "buff": {"duration_seconds", "interval_seconds", "stacking", "modifiers", "events", "effects", "parameters", "removal", "duration_rule", "interval_rule"},
    "selector": {"region", "filters", "ordering", "limit", "provider", "parameters"},
    "behavior": {"provider", "implementation", "states", "initial", "initial_state", "transitions", "parameters"},
    "policy": {"contract", "provider", "implementation", "parameters"},
    "calculation_rule": {"contract", "implementation", "parameters", "numeric", "contractVersion"},
    "rule": {"contract", "implementation", "parameters", "numeric", "contractVersion"},
    "ruleset": {"bindings", "quantum", "numeric_profile", "system_order", "attribute_layers", "parameters", "phase_order", "reaction_budget"},
    "preset": {"ruleset", "provides", "requires", "parameters"},
    "scenario": {"ruleset", "map", "initialEntities", "waves", "commands", "objectives", "resources", "parameters", "roster", "duration_seconds", "seed", "aliases", "description", "routes", "packages"},
}
COMPONENT_FIELDS = {
    "attributes": {"base", "modifiers", "rules", "growth", "parameters", "layers", "attribute_rules"},
    "resources": None,
    "abilities": None,
    "deployable": {"policy", "cost", "base_cost", "cooldown_seconds", "refund_ratio", "terrain", "capacity", "rules", "parameters", "deployed", "initial_state"},
    "behavior": {"machine", "state", "rules", "parameters"},
    "lifecycle": {"policy", "initial_state", "rules", "parameters", "leak_loss", "revive"},
    "spatial": {"coordinate_space", "position", "facing", "route", "route_id", "speed", "blocking", "occupancy", "radius", "projectile", "rules", "parameters", "wait_seconds", "movement", "terrain", "block_capacity", "block_cost"},
    "buffs": {"initial", "policy", "rules", "parameters"},
    "buff_container": {"initial", "policy", "rules", "parameters"},
}
RESOURCE_FIELDS = {"initial", "capacity", "capacity_attribute", "recovery_rule", "recovery_rate", "recovery", "rules", "parameters", "bounds_rule", "capacity_rule", "role", "events"}
EFFECT_FIELDS = {"op", "target", "rules", "read_mode", "damage_type", "scale", "additions", "amount", "delta", "resource", "buff", "definition", "position", "facing", "tags", "on_success", "on_failure", "effects", "condition", "parameters", "event", "payload", "type", "state", "machine", "distance", "offset", "direction", "kind", "at_seconds", "delay_seconds", "effect", "stacks", "selector", "lifetime_seconds", "remove_all", "metadata", "duration_seconds"}
DEFAULT_CAPABILITIES = {
    "effects": {"damage", "heal", "modify_resource", "apply_buff", "remove_buff", "spawn", "emit", "move", "state", "schedule", "transition"},
    "activations": {"manual", "automatic_attack", "on_deploy", "passive"},
    "read_modes": {"at_hit", "at_cast", "at_launch"},
    "stacking": {"refresh", "independent", "add", "extend", "max"},
}


def fields(value, allowed, path):
    if not isinstance(value, Mapping):
        raise ContentError(f"{path}: expected an object")
    unknown = set(value) - allowed
    if unknown:
        raise ContentError(f"{path}: unknown fields {sorted(unknown)}")


def number(value, path, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContentError(f"{path}: expected a finite number")
    if minimum is not None and value < minimum:
        raise ContentError(f"{path}: must be >= {minimum}")


def validate_timing(value, path):
    if "at" in value and "at_seconds" in value:
        raise ContentError(f"{path}: at and at_seconds are mutually exclusive")
    if "at" in value and (type(value["at"]) is not int or value["at"] < 0):
        raise ContentError(f"{path}.at: expected a nonnegative integer logical time")
    if "at_seconds" in value:
        number(value["at_seconds"], f"{path}.at_seconds", 0)


def validate_effect(effect, path, capabilities):
    fields(effect, EFFECT_FIELDS, path)
    if effect.get("op") not in capabilities["effects"]:
        raise ContentError(f"{path}: unsupported effect op {effect.get('op')!r}")
    required = {"modify_resource": ("resource",), "apply_buff": ("buff",), "remove_buff": ("buff",),
                "spawn": ("definition",), "schedule": ("effect",)}
    for field in required.get(effect["op"], ()):
        if field not in effect:
            raise ContentError(f"{path}.{field}: required by {effect['op']} effect")
    if effect["op"] == "modify_resource" and "delta" not in effect and "amount" not in effect:
        raise ContentError(f"{path}: modify_resource requires delta or amount")
    mode = effect.get("read_mode", {})
    if not isinstance(mode, Mapping):
        raise ContentError(f"{path}.read_mode must be an object")
    for key, value in mode.items():
        if key not in {"source_attributes", "target_attributes", "targets"} or value not in capabilities["read_modes"]:
            raise ContentError(f"{path}.read_mode.{key}: unsupported mode {value!r}")
    for key in ("on_success", "on_failure", "effects"):
        for index, child in enumerate(effect.get(key, [])):
            validate_effect(child, f"{path}.{key}[{index}]", capabilities)
    if "effect" in effect:
        validate_effect(effect["effect"], f"{path}.effect", capabilities)
    for key in ("amount", "delta", "scale", "additions", "distance", "delay_seconds"):
        if key in effect:
            number(effect[key], f"{path}.{key}")


def validate_events(events, path, capabilities):
    if not isinstance(events, (list, tuple)):
        raise ContentError(f"{path}: expected a list of event subscriptions")
    for index, subscription in enumerate(events):
        location = f"{path}[{index}]"
        fields(subscription, {"event", "condition", "effects", "parameters"}, location)
        if not isinstance(subscription.get("event"), str):
            raise ContentError(f"{location}.event must be an explicit event name")
        if "condition" in subscription and not isinstance(subscription["condition"], str):
            raise ContentError(f"{location}.condition must be an expression string")
        if not isinstance(subscription.get("effects"), (list, tuple)) or not subscription["effects"]:
            raise ContentError(f"{location}.effects requires an implemented nonempty effect list")
        for effect in subscription["effects"]:
            validate_effect(effect, f"{location}.effects", capabilities)


def validate_modifier(modifier, path):
    fields(modifier, {"attribute", "layer", "value", "stacks", "rule", "operation", "parameters"}, path)
    for field in ("rule", "operation"):
        if field in modifier:
            raise ContentError(f"{path}.{field}: per-modifier execution is unsupported; bind attributes.modifier_layer or attributes.effective")
    if not isinstance(modifier.get("attribute"), str):
        raise ContentError(f"{path}.attribute: expected an attribute name")
    number(modifier.get("value"), f"{path}.value")
    if "stacks" in modifier:
        number(modifier["stacks"], f"{path}.stacks", 0)


def validate_definition(definition, capabilities=None):
    capabilities = capabilities or DEFAULT_CAPABILITIES
    identifier = definition["id"]
    kind = definition.get("kind")
    if kind not in FIELDS:
        raise ContentError(f"{identifier}: unsupported definition kind {kind!r}")
    fields(definition, COMMON | FIELDS[kind], identifier)
    if kind == "entity":
        for field in ("equipment", "progression", "talents"):
            if definition.get(field):
                raise ContentError(f"{identifier}.{field}: execution is unsupported in this runtime")
        components = definition.get("components")
        if not isinstance(components, Mapping):
            raise ContentError(f"{identifier}.components: expected an object")
        for key, value in components.items():
            if key not in COMPONENT_FIELDS:
                raise ContentError(f"{identifier}.components: unsupported component {key!r}")
            allowed = COMPONENT_FIELDS[key]
            if allowed is not None:
                fields(value, allowed, f"{identifier}.components.{key}")
        if "buffs" in components and "buff_container" in components:
            raise ContentError(f"{identifier}.components: buffs and buff_container are mutually exclusive aliases")
        for key in ("buffs", "buff_container"):
            if key in components:
                initial = components[key].get("initial", [])
                if not isinstance(initial, (list, tuple)) or not all(isinstance(ref, str) and ref.strip() for ref in initial):
                    raise ContentError(f"{identifier}.components.{key}.initial: expected a list of Buff ID strings")
        if "route_id" in components.get("spatial", {}):
            raise ContentError(f"{identifier}.spatial.route_id: route ID resolution is unsupported; use an inline route")
        for attr, value in components.get("attributes", {}).get("base", {}).items():
            number(value, f"{identifier}.attributes.base.{attr}")
        for index, modifier in enumerate(components.get("attributes", {}).get("modifiers", [])):
            validate_modifier(modifier, f"{identifier}.attributes.modifiers[{index}]")
        growth_sources = [definition.get("growth", {}), components.get("attributes", {}).get("growth", {})]
        for growth in growth_sources:
            if not isinstance(growth, Mapping):
                raise ContentError(f"{identifier}.growth: expected an attribute-to-growth map")
            for attr, spec in growth.items():
                path = f"{identifier}.growth.{attr}"
                fields(spec, {"rule", "level", "parameters"}, path)
                if "rule" in spec and (not isinstance(spec["rule"], str) or not spec["rule"].strip()):
                    raise ContentError(f"{path}.rule must be a nonempty attributes.growth rule reference when specified")
                number(spec.get("level", 1), f"{path}.level", 0)
                if attr not in components.get("attributes", {}).get("base", {}):
                    raise ContentError(f"{path}: growth refers to an undefined base attribute")
        attribute_rules = components.get("attributes", {}).get("attribute_rules", {})
        if not isinstance(attribute_rules, Mapping):
            raise ContentError(f"{identifier}.attribute_rules: expected an attribute-to-bindings map")
        for attribute, bindings in attribute_rules.items():
            if attribute not in components.get("attributes", {}).get("base", {}):
                raise ContentError(f"{identifier}.attribute_rules.{attribute}: unknown base attribute")
            if not isinstance(bindings, Mapping) or not all(isinstance(ref, str) for ref in bindings.values()):
                raise ContentError(f"{identifier}.attribute_rules.{attribute}: expected calculation-to-rule bindings")
        resources = components.get("resources", {})
        if not isinstance(resources, Mapping):
            raise ContentError(f"{identifier}.resources must be an object")
        for resource, values in resources.items():
            path = f"{identifier}.resources.{resource}"
            fields(values, RESOURCE_FIELDS, path)
            for name in ("initial", "capacity", "recovery_rate"):
                if name in values:
                    number(values[name], f"{path}.{name}", 0 if name == "capacity" else None)
            if "capacity_attribute" in values and values["capacity_attribute"] not in components.get("attributes", {}).get("base", {}):
                raise ContentError(f"{path}.capacity_attribute: unknown attribute {values['capacity_attribute']}")
            validate_recovery(values, path)
        abilities = components.get("abilities", [])
        if not isinstance(abilities, (list, tuple)) or not all(isinstance(v, str) for v in abilities):
            raise ContentError(f"{identifier}.abilities must be an ID list")
    elif kind == "ability":
        activation = definition.get("activation", {})
        fields(activation, {"mode", "costs", "interval_rule", "interval_seconds", "event", "events", "condition", "rules", "parameters", "cooldown_seconds", "duration_seconds"}, f"{identifier}.activation")
        if activation.get("mode") not in capabilities["activations"]:
            raise ContentError(f"{identifier}: unsupported activation {activation.get('mode')!r}")
        if activation.get("mode") == "passive" and not (activation.get("event") or activation.get("events")):
            raise ContentError(f"{identifier}: passive activation requires an explicit event")
        interruption = definition.get("interrupt_policy", {})
        fields(interruption, {"on_source_death", "refund"}, f"{identifier}.interrupt_policy")
        if interruption.get("on_source_death", "cancel_remaining") != "cancel_remaining" or interruption.get("refund", "none") != "none":
            raise ContentError(f"{identifier}.interrupt_policy: continue and refund policies are unsupported")
        validate_events(definition.get("events", []), f"{identifier}.events", capabilities)
        for index, cost in enumerate(activation.get("costs", [])):
            fields(cost, {"resource", "amount", "rule", "parameters", "rules"}, f"{identifier}.activation.costs[{index}]")
            if not isinstance(cost.get("resource"), str) or ("amount" not in cost and "rule" not in cost):
                raise ContentError(f"{identifier}.activation.costs[{index}]: cost needs resource and amount or rule")
            if "amount" in cost:
                number(cost["amount"], f"{identifier}.cost.amount", 0)
        for index, entry in enumerate(definition.get("timeline", [])):
            path = f"{identifier}.timeline[{index}]"
            fields(entry, {"at_seconds", "at", "effect", "effects", "repeat", "condition", "rules"}, path)
            validate_timing(entry, path)
            if "repeat" in entry:
                repeat = entry["repeat"]
                fields(repeat, {"count", "interval_seconds", "rule", "parameters"}, f"{path}.repeat")
                count = repeat.get("count", 1)
                if not isinstance(count, int) or isinstance(count, bool) or not 0 < count <= 10000:
                    raise ContentError(f"{path}.repeat.count must be an integer in 1..10000")
                number(repeat.get("interval_seconds", 0), f"{path}.repeat.interval_seconds", 0)
            if "effect" not in entry and not entry.get("effects"):
                raise ContentError(f"{path}: timeline entry requires an implemented effect")
            if "effect" in entry:
                validate_effect(entry["effect"], f"{path}.effect", capabilities)
            for effect in entry.get("effects", []):
                validate_effect(effect, f"{path}.effects", capabilities)
    elif kind == "buff":
        for key in ("duration_seconds", "interval_seconds"):
            if key in definition:
                number(definition[key], f"{identifier}.{key}", 0)
        stacking = definition.get("stacking", {"mode": "refresh"})
        fields(stacking, {"mode", "identity", "max_stacks", "policy", "parameters", "rule"}, f"{identifier}.stacking")
        if "policy" in stacking:
            raise ContentError(f"{identifier}.stacking.policy: custom policy execution is unsupported")
        if stacking.get("mode") not in capabilities["stacking"] and "policy" not in stacking:
            raise ContentError(f"{identifier}: unsupported stacking mode {stacking.get('mode')!r}")
        for index, modifier in enumerate(definition.get("modifiers", [])):
            validate_modifier(modifier, f"{identifier}.modifiers[{index}]")
        for effect in definition.get("effects", []):
            validate_effect(effect, f"{identifier}.effects", capabilities)
        validate_events(definition.get("events", []), f"{identifier}.events", capabilities)
    elif kind == "selector":
        region = definition.get("region", {})
        fields(region, {"type", "offsets", "rotate_with_facing", "radius", "range", "provider", "parameters", "blocked_only"}, f"{identifier}.region")
        if definition.get("provider", "ark.selector.grid") == "ark.selector.grid":
            if region.get("type", "grid_offsets") not in {"grid_offsets", "all", "radius", "circle"}:
                raise ContentError(f"{identifier}.region.type: unsupported built-in region {region.get('type')!r}")
            if region.get("type") in {"radius", "circle"}:
                number(region.get("radius"), f"{identifier}.region.radius", 0)
        for index, restriction in enumerate(definition.get("filters", [])):
            fields(restriction, {"tag", "state"}, f"{identifier}.filters[{index}]")
            if "state" in restriction and restriction["state"] != "alive":
                raise ContentError(f"{identifier}.filters[{index}].state: only alive filtering is implemented")
        for offset in region.get("offsets", []):
            if not isinstance(offset, (list, tuple)) or len(offset) != 2 or not all(isinstance(x, (int, float)) for x in offset):
                raise ContentError(f"{identifier}.region.offsets must contain coordinate pairs")
    elif kind == "ruleset":
        if "quantum" not in definition:
            raise ContentError(f"{identifier}.quantum must be explicitly declared or inherited from a preset")
        number(definition["quantum"], f"{identifier}.quantum", 0)
        if definition["quantum"] == 0:
            raise ContentError(f"{identifier}.quantum must be positive")
        if not isinstance(definition.get("bindings", {}), Mapping):
            raise ContentError(f"{identifier}.bindings must be an object")
    elif kind in {"calculation_rule", "rule"}:
        bindings = definition.get("metadata", {}).get("input_bindings", {})
        if not isinstance(bindings, Mapping):
            raise ContentError(f"{identifier}.metadata.input_bindings must be a map")
        for name, binding in bindings.items():
            path = f"{identifier}.metadata.input_bindings.{name}"
            fields(binding, {"entity", "attribute_role", "attribute"}, path)
            if binding.get("entity") not in {"source", "target"}:
                raise ContentError(f"{path}.entity must be source or target")
            if not isinstance(binding.get("attribute", binding.get("attribute_role")), str):
                raise ContentError(f"{path}: declare an attribute or attribute_role")
    elif kind == "behavior":
        if not definition.get("provider") and not definition.get("implementation"):
            states = definition.get("states")
            if not isinstance(states, Mapping) or not states:
                raise ContentError(f"{identifier}: behavior requires an implemented provider or nonempty state graph")
            initial = definition.get("initial_state", definition.get("initial"))
            if initial not in states:
                raise ContentError(f"{identifier}: initial state is absent from graph")
            for name, state in states.items():
                fields(state, {"on_enter", "on_exit"}, f"{identifier}.states.{name}")
                for key in ("on_enter", "on_exit"):
                    if not isinstance(state.get(key, []), (list, tuple)):
                        raise ContentError(f"{identifier}.states.{name}.{key} must be an effect list")
                    for effect in state.get(key, []):
                        validate_effect(effect, f"{identifier}.states.{name}.{key}", capabilities)
            transitions = definition.get("transitions", [])
            if not isinstance(transitions, (list, tuple)):
                raise ContentError(f"{identifier}.transitions must be a list")
            for index, transition in enumerate(transitions):
                path = f"{identifier}.transitions[{index}]"
                fields(transition, {"from", "to", "condition", "condition_rule", "priority", "effects", "parameters"}, path)
                if transition.get("from") not in states and transition.get("from") != "*":
                    raise ContentError(f"{path}.from references an unknown state")
                if transition.get("to") not in states:
                    raise ContentError(f"{path}.to references an unknown state")
                if "condition" in transition and not isinstance(transition["condition"], str):
                    raise ContentError(f"{path}.condition must be an expression string")
                number(transition.get("priority", 0), f"{path}.priority")
                for effect in transition.get("effects", []):
                    validate_effect(effect, f"{path}.effects", capabilities)
    elif kind == "scenario":
        aliases = set()
        for index, entity in enumerate(definition.get("initialEntities", [])):
            path = f"{identifier}.initialEntities[{index}]"
            fields(entity, {"definition", "instanceAlias", "position", "facing", "components", "rules", "tags", "deployed", "route"}, path)
            if not isinstance(entity.get("definition"), (str, Mapping)):
                raise ContentError(f"{path}.definition is required")
            alias = entity.get("instanceAlias")
            if alias is not None:
                if alias in aliases:
                    raise ContentError(f"{identifier}: duplicate instance alias {alias}")
                aliases.add(alias)
        for command in definition.get("commands", []):
            fields(command, {"at_seconds", "at", "action", "type", "source", "entity", "ability", "definition", "position", "row", "col", "facing", "instanceAlias", "alias", "payload", "resource", "delta", "target", "parameters", "components", "tags"}, f"{identifier}.commands")
            validate_timing(command, f"{identifier}.commands")
        for name, spec in definition.get("resources", {}).items():
            fields(spec, RESOURCE_FIELDS, f"{identifier}.resources.{name}")
            validate_recovery(spec, f"{identifier}.resources.{name}")
        for index, wave in enumerate(definition.get("waves", [])):
            path = f"{identifier}.waves[{index}]"
            fields(wave, {"at_seconds", "at", "definition", "position", "route", "route_id", "instanceAlias", "facing", "components", "tags", "rules", "parameters", "count", "interval_seconds"}, path)
            validate_timing(wave, path)
            if "route_id" in wave:
                raise ContentError(f"{path}.route_id: route ID resolution is unsupported; use an inline route")
            if "count" in wave and (type(wave["count"]) is not int or wave["count"] != 1):
                raise ContentError(f"{path}.count: one absolute wave entry supports exactly one instance; expand entries explicitly")
            if "interval_seconds" in wave:
                number(wave["interval_seconds"], f"{path}.interval_seconds", 0)
                if wave["interval_seconds"] != 0:
                    raise ContentError(f"{path}.interval_seconds: repeated wave scheduling is unsupported; expand entries explicitly")
            if "definition" not in wave:
                raise ContentError(f"{path}.definition is required for an absolute spawn wave")
    return definition


def validate_recovery(spec, path):
    if "recovery" in spec:
        driver = spec["recovery"]
        fields(driver, {"mode", "interval_seconds"}, f"{path}.recovery")
        mode = driver.get("mode", "continuous")
        if mode not in {"continuous", "periodic"}:
            raise ContentError(f"{path}.recovery.mode: unsupported recovery driver {mode!r}")
        if mode == "periodic":
            number(driver.get("interval_seconds"), f"{path}.recovery.interval_seconds", 0)
            if driver["interval_seconds"] <= 0:
                raise ContentError(f"{path}.recovery.interval_seconds must be positive")
        elif "interval_seconds" in driver:
            raise ContentError(f"{path}.recovery: continuous driver does not accept interval_seconds")
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise ContentError(f"{path}.parameters: expected an object")
    for field in ("pause_at_full", "freeze_while_cast"):
        if field in parameters and not isinstance(parameters[field], bool):
            raise ContentError(f"{path}.parameters.{field}: expected a boolean")
