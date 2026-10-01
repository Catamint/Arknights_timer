"""Required calculations follow active content and its explicit rule scopes."""
from collections.abc import Mapping
from .repository import ContentError


def capability_preflight(scenario, definitions, ruleset, rules, catalog=None):
    global_scopes = [ruleset.get("bindings", {}), scenario.get("rules", {})]
    requirements = []
    entity_binding_ids = {id(definition["rules"]) for definition in definitions.values()
                          if definition.get("kind") == "entity" and "rules" in definition}

    def require(calculation, path, scopes=(), explicit=None):
        chosen = explicit
        if chosen is None:
            candidates = global_scopes if calculation == "time.quantize" else [*global_scopes, *scopes]
            owner = (catalog or {}).get("contracts", {}).get(calculation, {}).get("owner", "owner")
            for scope in candidates:
                if id(scope) in entity_binding_ids and owner in {"scenario", "ability", "effect"}:
                    continue
                if calculation in scope:
                    chosen = scope[calculation]
        if chosen is None:
            raise ContentError(f"{path}: required calculation {calculation} has no rule binding")
        if chosen not in rules or rules[chosen].get("contract") != calculation:
            raise ContentError(f"{path}: required calculation {calculation} is bound to incompatible rule {chosen}")
        requirements.append({"calculation": calculation, "rule": chosen, "required_by": path})

    def resources(values, path, scopes):
        for name, spec in values.items():
            local = [*scopes, spec.get("rules", {})]
            for calculation, field in (("resource.capacity", "capacity_rule"), ("resource.bounds", "bounds_rule")):
                require(calculation, f"{path}.{name}", local, spec.get(field))
            if "recovery_rule" in spec or "recovery_rate" in spec or "recovery" in spec or "resource.recovery" in spec.get("rules", {}):
                require("resource.recovery", f"{path}.{name}", local, spec.get("recovery_rule"))
            if spec.get("recovery", {}).get("mode") == "periodic":
                require("time.quantize", f"{path}.{name}.recovery")

    selectors_seen = set()
    def selector(identifier, path, scopes):
        if not identifier:
            return
        selectors_seen.add(identifier)
        require("targeting.score", path, scopes)
        require("targeting.selection", path, scopes)

    def effect(item, path, scopes):
        local = [*scopes, item.get("rules", {})]
        op = item.get("op")
        if op == "damage":
            require("damage.pipeline", path, local)
        elif op == "heal":
            require("healing.base", path, local)
        elif op == "schedule":
            require("time.quantize", path, local)
        if item.get("selector"):
            selector(item["selector"], path, local)
        for key in ("on_success", "on_failure", "effects"):
            for index, child in enumerate(item.get(key, [])):
                effect(child, f"{path}.{key}[{index}]", local)
        if "effect" in item:
            effect(item["effect"], f"{path}.effect", local)

    def events(subscriptions, path, scopes):
        for index, subscription in enumerate(subscriptions):
            for child_index, child in enumerate(subscription.get("effects", [])):
                effect(child, f"{path}[{index}].effects[{child_index}]", scopes)

    abilities_seen = set()
    def ability(identifier, path, scopes):
        abilities_seen.add(identifier)
        definition = definitions[identifier]
        local = [*scopes, definition.get("rules", {})]
        for calculation in ("time.quantize", "ability.windup", "ability.repeat", "ability.duration", "ability.recovery"):
            require(calculation, path, local)
        activation = definition.get("activation", {})
        parameters = {**definition.get("parameters", {}), **activation.get("parameters", {})}
        if activation.get("mode") == "automatic_attack" or parameters.get("replace_attack"):
            require("time.interval", path, local, activation.get("interval_rule"))
        for cost in activation.get("costs", []):
            require("resource.cost", path, local, cost.get("rule"))
        selector(definition.get("selector"), path, local)
        for index, entry in enumerate(definition.get("timeline", [])):
            for child in ([entry["effect"]] if "effect" in entry else entry.get("effects", [])):
                effect(child, f"{path}.timeline[{index}]", local)
        events(definition.get("events", []), f"{path}.events", local)

    def route(path, scopes):
        for calculation in ("time.quantize", "movement.path", "movement.speed", "movement.distance",
                            "blocking.eligibility", "blocking.capacity", "blocking.occupancy", "lifecycle.leak_loss"):
            require(calculation, path, scopes)

    def deploy(path, scopes):
        for calculation in ("time.quantize", "deploy.cost", "deploy.refund", "deploy.cooldown", "deploy.capacity", "deploy.eligibility"):
            require(calculation, path, scopes)

    entity_scopes = {}
    for identifier, definition in definitions.items():
        if definition.get("kind") != "entity":
            continue
        components = definition.get("components", {})
        scopes = [definition.get("rules", {})]
        entity_scopes[identifier] = scopes
        attributes = components.get("attributes", {})
        for stat in attributes.get("base", {}):
            require("attributes.effective", f"{identifier}.attributes.{stat}",
                    [*scopes, attributes.get("rules", {}), attributes.get("attribute_rules", {}).get(stat, {})])
        growth = definition.get("growth", attributes.get("growth", {}))
        for stat, spec in growth.items():
            require("attributes.growth", f"{identifier}.growth.{stat}",
                    [*scopes, attributes.get("rules", {}), attributes.get("attribute_rules", {}).get(stat, {})], spec.get("rule"))
        resources(components.get("resources", {}), f"{identifier}.resources", scopes)
        for ability_id in components.get("abilities", []):
            ability(ability_id, f"{identifier} -> {ability_id}", scopes)
        lifecycle = components.get("lifecycle", {})
        if lifecycle.get("policy"):
            require("lifecycle.death", f"{identifier}.lifecycle", [*scopes, lifecycle.get("rules", {})])
        spatial = components.get("spatial", {})
        if spatial.get("route"):
            route(f"{identifier}.spatial.route", [*scopes, spatial.get("rules", {})])
        if "deployable" in components:
            deploy(f"{identifier}.deployable", [*scopes, components["deployable"].get("rules", {})])

    for identifier, definition in definitions.items():
        kind, scopes = definition.get("kind"), [definition.get("rules", {})]
        if kind == "ability" and identifier not in abilities_seen:
            ability(identifier, identifier, [])
        elif kind == "selector" and identifier not in selectors_seen:
            selector(identifier, identifier, [])
        elif kind == "buff":
            for calculation, explicit in (("buff.duration", definition.get("duration_rule")),
                                           ("buff.interval", definition.get("interval_rule")),
                                           ("buff.stack_amount", definition.get("stacking", {}).get("rule")), ("time.quantize", None)):
                require(calculation, identifier, scopes, explicit)
            if definition.get("modifiers"):
                for modifier in definition["modifiers"]:
                    candidates = [entity for entity in definitions.values() if entity.get("kind") == "entity"
                                  and modifier["attribute"] in entity.get("components", {}).get("attributes", {}).get("base", {})]
                    if not candidates:
                        require("attributes.effective", identifier, scopes)
                    for entity in candidates:
                        attributes = entity["components"]["attributes"]
                        require("attributes.effective", identifier, [entity.get("rules", {}), attributes.get("rules", {}),
                            attributes.get("attribute_rules", {}).get(modifier["attribute"], {})])
            for index, item in enumerate(definition.get("effects", [])):
                effect(item, f"{identifier}.effects[{index}]", scopes)
            events(definition.get("events", []), f"{identifier}.events", scopes)
        elif kind == "behavior" and definition.get("states"):
            for name, state in definition["states"].items():
                for key in ("on_enter", "on_exit"):
                    for index, item in enumerate(state.get(key, [])):
                        effect(item, f"{identifier}.states.{name}.{key}[{index}]", scopes)
            for index, transition in enumerate(definition.get("transitions", [])):
                for child in transition.get("effects", []):
                    effect(child, f"{identifier}.transitions[{index}]", scopes)

    resources(scenario.get("resources", {}), f"{scenario['id']}.resources", [])
    for index, wave in enumerate(scenario.get("waves", [])):
        require("time.quantize", f"{scenario['id']}.waves[{index}]")
        if wave.get("route"):
            scopes = entity_scopes.get(wave["definition"], [])
            spatial = definitions[wave["definition"]].get("components", {}).get("spatial", {})
            route(f"{scenario['id']}.waves[{index}].route", [*scopes, spatial.get("rules", {})])
    for index, item in enumerate(scenario.get("initialEntities", [])):
        if item.get("route"):
            route(f"{scenario['id']}.initialEntities[{index}].route", entity_scopes.get(item["definition"], []))
    if scenario.get("objectives"):
        require("lifecycle.result", f"{scenario['id']}.objectives")
    for command in scenario.get("commands", []):
        require("time.quantize", f"{scenario['id']}.commands")
        if command.get("action", command.get("type")) == "deploy":
            deploy(f"{scenario['id']}.commands.deploy", entity_scopes.get(command.get("entity", command.get("definition")), []))
    return requirements
