"""Pure Ark preset decisions. User providers can replace every entry."""
import math


def attribute_layers(inputs, params, context):
    value = inputs["base"]
    modifiers = inputs["modifier_layers"]
    operations = params.get("operations", {})
    aggregator = params.get("aggregator", {}).get("provider", "ark.attributes.aggregate")
    for record in inputs["order"]:
        layer = record["layer"]
        selected = [m for m in modifiers if m.get("layer", "flat") == layer]
        if not selected:
            continue
        parameters = context.invoke_provider(aggregator, {"layer": layer, "modifiers": selected},
                                             {"operation": operations.get(layer, "custom")})
        result = context.calculate("attributes.modifier_layer", {"value": value,
                                   "modifiers": selected, "layer_parameters": parameters})
        value = result.value
    return value


def aggregate_layer(inputs, params, context):
    values = [m["value"] * m.get("stacks", 1) for m in inputs["modifiers"]]
    operation = params["operation"]
    result = {"additive": 0, "ratio": 0, "factor": 1}
    if operation == "add":
        result["additive"] = sum(values)
    elif operation == "ratio_sum":
        result["ratio"] = sum(values)
    elif operation == "ratio_product":
        result["factor"] = math.prod(1 + v for v in values)
    elif operation == "factor_product":
        result["factor"] = math.prod(values)
    elif operation != "custom":
        raise ValueError(f"unknown preset aggregation operation {operation}")
    return result


def resource_bounds(inputs, params, context):
    value, capacity = inputs["candidate"], inputs["capacity"]
    options = dict(params, **dict(inputs["bounds_parameters"]))
    low = options.get("minimum", 0)
    high = options.get("maximum", capacity)
    if options.get("mode", "clamp") == "reject" and not low <= value <= high:
        return {"value": value, "overflow": 0, "accepted": False}
    bounded = min(high, max(low, value))
    return {"value": bounded, "overflow": value - bounded, "accepted": True}


def ground_deploy(inputs, params, context):
    terrain, states = inputs["terrain"], inputs["states"]
    restrictions = inputs["entity"].get("components", {}).get("deployable", {})
    allowed = restrictions.get("terrain", "ground")
    mask = 2 if allowed == "high" else 1 if allowed == "ground" else 3
    checks = [(states.get("finished", False), "battle_finished"),
              (not terrain.get("inside", False), "out_of_map"),
              (not int(terrain.get("buildable", 0) or 0) & mask, "not_buildable"),
              (terrain.get("occupied", False), "occupied"),
              (states.get("instances", 0) >= restrictions.get("parameters", {}).get("max_instances", 1), "already_deployed"),
              (states.get("cooldown", False), "on_cooldown"),
              (states.get("at_capacity", False), "capacity"),
              (not inputs["resources"].get("affordable", True), "insufficient_resource")]
    reason = next((message for failed, message in checks if failed), "ok")
    return {"accepted": reason == "ok", "reason": reason}


def lifecycle(inputs, params, context):
    params = dict(context.get("lifecycle_parameters", {}), **dict(params))
    resource = params.get("resource", "hp")
    amount = inputs["resources"].get(resource, {}).get("current")
    died = amount is not None and amount <= params.get("threshold", 0)
    revive = params.get("revive", False) and died
    return {"action": "revive" if revive else "death" if died else "none",
            "state": "alive" if revive or not died else "dead",
            "resource": resource, "reason": "resource_threshold",
            "value": params.get("revive_value", 1) if revive else 0}


def battle_result(inputs, params, context):
    objective = context.get("objectives", {})
    resource = objective.get("life_resource", "life")
    value = inputs["resources"].get(resource, {}).get("current")
    if objective.get("type") == "waves":
        if value is not None and value <= objective.get("defeat_threshold", 0):
            return {"finished": True, "result": "defeat", "reason": "life_exhausted"}
        alive = [e for e in context["entity_states"] if "enemy" in e["tags"] and e["components"].get("runtime", {}).get("alive", False)]
        if inputs["waves"].get("pending", 0) == 0 and not alive:
            return {"finished": True, "result": "victory", "reason": "waves_cleared"}
    return {"finished": False, "result": "running", "reason": "objectives_pending"}


def targeting_score(inputs, params, context):
    candidate = inputs["candidate"]
    runtime = candidate.get("components", {}).get("runtime", {})
    if context.get("healing", False):
        return context.get("health_ratio", 1)
    return (inputs["distance"] - (params.get("blocked_priority", 100000) if runtime.get("blocked_by") == inputs["source"].get("id") else 0))


def targeting_selection(inputs, params, context):
    values = [(candidate, inputs["scores"].get(str(candidate["id"]), 0)) for candidate in inputs["candidates"]]
    if context.get("ordering") != "provider":
        values.sort(key=lambda pair: (pair[1], pair[0]["id"]))
    limit = inputs["limits"].get("count")
    return [x["id"] for x, _ in values[:limit] if limit is not None] if limit is not None else [x["id"] for x, _ in values]


def player_behavior(inputs, params, context):
    return {"move": False, "attack": True, "state": "alive"}


def ground_behavior(inputs, params, context):
    return {"move": not inputs.get("blocked_by"), "attack": bool(inputs.get("blocked_by")),
            "state": "alive"}


def selector_grid(inputs, params, context):
    source = inputs["source"]
    region = inputs["region"]
    position = source["components"]["spatial"]["position"]
    facing = source["components"]["spatial"].get("facing", "right")
    cells = set()
    for row, col in region.get("offsets", ()):
        if region.get("rotate_with_facing", True):
            row, col = {"right": (row, col), "up": (-col, row),
                        "left": (-row, -col), "down": (col, -row)}[facing]
        cells.add((round(position["row"] + row), round(position["col"] + col)))
    accepted = []
    for entity in inputs["candidates"]:
        pos = entity["components"].get("spatial", {}).get("position")
        if pos is None:
            continue
        if region.get("type") == "all":
            inside = True
        elif region.get("type") in ("radius", "circle"):
            inside = math.hypot(pos["row"]-position["row"], pos["col"]-position["col"]) <= region["radius"]
        else:
            inside = (round(pos["row"]), round(pos["col"])) in cells
        if params.get("include_blocked") and entity["components"].get("runtime", {}).get("blocked_by") == source["id"]:
            inside = True
        if inside:
            accepted.append(entity["id"])
    return accepted


def spatial_route(inputs, params, context):
    from ark_sim.domains.spatial import GridTopology
    return GridTopology(inputs["map"]).path(inputs["origin"], inputs["destination"])


def spatial_blocking(inputs, params, context):
    blocker = inputs["blocker"]["components"]["spatial"]["position"]
    target = inputs["target"]["components"]["spatial"]["position"]
    distance = math.hypot(blocker["row"]-target["row"], blocker["col"]-target["col"])
    return {"accepted": distance <= params.get("radius", 1), "reason": "nearby" if distance <= params.get("radius", 1) else "distant"}


def damage_pipeline(inputs, params, context):
    power = context.calculate("damage.base", {"attack": inputs["effect"]["attack"],
                              "scale": inputs["effect"]["scale"], "additions": inputs["effect"]["additions"]}).value
    amount = context.calculate("damage.mitigation", {"power": power, "defense": inputs["effect"]["defense"],
                               "resistance": inputs["effect"]["resistance"], "damage_type": inputs["effect"]["damage_type"]}).value
    return {"accepted": True, "amount": amount,
            "allocations": [], "events": []}
