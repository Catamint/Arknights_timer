"""Spatial plans and movement rates are separate, replaceable decisions."""
import math
from ark_sim.contracts import thaw
from .spatial import GridTopology


class SpatialSystem:
    def __init__(self, context):
        self.ctx = context
        self.map_definition = thaw(context.program.scenario.get("map", {"rows": 1, "cols": 1}))
        self.grid = GridTopology(self.map_definition)

    def blocked_by(self, ref):
        return self.ctx.get(ref, ("runtime", "blocked_by"))

    def select(self, source, selector_id, ability=None, effect=None):
        definition = self.ctx.program.definitions[selector_id]
        entity = self.ctx.entity(source)
        candidates = [thaw(e) for e in self.ctx.session.world.entities() if e["id"] != self.ctx.session.world.resolve("system/battle")]
        for restriction in definition.get("filters", ()):
            if "tag" in restriction:
                candidates = [e for e in candidates if restriction["tag"] in e["tags"]]
            if restriction.get("state") == "alive":
                candidates = [e for e in candidates if self.ctx.alive(e["id"])]
        if definition.get("region", {}).get("blocked_only"):
            candidates = [e for e in candidates if e["id"] == self.blocked_by(source)]
        provider = definition.get("provider", "ark.selector.grid")
        ids = self.ctx.provider(provider, {"source": entity, "candidates": candidates,
                "region": thaw(definition.get("region", {}))}, definition.get("parameters", {}))
        allowed = {e["id"] for e in candidates}
        if not isinstance(ids, (list, tuple)) or any(x not in allowed for x in ids):
            raise ValueError("selector returned an undeclared candidate")
        healing = bool((ability or {}).get("parameters", {}).get("healing"))
        rows = []
        position = entity["components"]["spatial"]["position"]
        for ref in ids:
            candidate = self.ctx.entity(ref)
            target_pos = candidate["components"]["spatial"]["position"]
            ratio = 1.0
            if healing:
                resource = self.ctx.health_resource(ref)
                current, capacity = self.ctx.resources.current(ref, resource), self.ctx.resources.capacity(ref, resource)
                if current >= capacity:
                    continue
                ratio = current / capacity
            score = self.ctx.calc("targeting.score", {"candidate": candidate, "source": entity,
                    "distance": math.hypot(position["row"]-target_pos["row"], position["col"]-target_pos["col"]),
                    "tags": [{"tag": tag} for tag in candidate["tags"]], "states": {}},
                    source=source, target=ref, ability=ability, effect=effect,
                    extra={"healing": healing, "health_ratio": ratio})
            rows.append((score, ref))
        limit = definition.get("limit")
        snapshots = [self.ctx.entity(ref) for _, ref in rows]
        return self.ctx.calc("targeting.selection", {"candidates": snapshots,
              "scores": {str(ref): score for score, ref in rows}, "samples": [], "limits": {"count": limit}},
              source=source, ability=ability, effect=effect, extra={"ordering": definition.get("ordering")})

    def blocking(self):
        entities = [thaw(e) for e in self.ctx.session.world.entities() if self.ctx.alive(e["id"])]
        movers = [e for e in entities if e["components"].get("spatial", {}).get("route")]
        if not movers:
            return
        blockers = []
        for entity in entities:
            spatial = entity["components"].get("spatial", {})
            if "position" not in spatial or not entity["components"].get("deployable"):
                continue
            value = self.ctx.calc("blocking.capacity", {"attributes": self.ctx.attributes.values(entity["id"]),
                  "states": {}, "capacity_parameters": {"capacity": self.ctx.role_value(entity["id"], "block_capacity")}}, source=entity["id"])
            if value > 0:
                blockers.append((entity["id"], value))
        used = {ref: 0 for ref, _ in blockers}
        alive_ids = {e["id"] for e in entities}
        for entity in movers:
            ref = entity["id"]
            spatial = entity["components"].get("spatial", {})
            if not spatial.get("route"):
                continue
            current = self.blocked_by(ref)
            volume = self.ctx.calc("blocking.occupancy", {"attributes": self.ctx.attributes.values(ref),
                "states": {}, "occupancy_parameters": {"number": self.ctx.role_value(ref, "block_occupancy")}}, target=ref)
            if current not in used or current not in alive_ids:
                current = None
            position = spatial["position"]
            for blocker, capacity in blockers:
                if current is not None:
                    break
                other = self.ctx.get(blocker, ("spatial", "position"))
                plan = self.ctx.calc("blocking.eligibility", {"blocker": self.ctx.entity(blocker),
                    "target": entity, "positions": [{"blocker": other}, {"target": position}],
                    "paths": {"remaining": list(spatial.get("movement_path", ()))}, "states": {}}, source=blocker, target=ref)
                path = spatial.get("movement_path", ())
                on_path = any(round(p["row"]) == round(other["row"]) and round(p["col"]) == round(other["col"]) for p in path) or (round(position["row"]),round(position["col"])) == (round(other["row"]),round(other["col"]))
                if plan["accepted"] and on_path and used[blocker]+volume <= capacity:
                    current = blocker
                    used[blocker] += volume
                    break
            if current is not None and current == self.blocked_by(ref):
                used[current] += volume
            if current != self.blocked_by(ref):
                self.ctx.set(ref, ("runtime", "blocked_by"), current)
                self.ctx.emit("blocking.changed", {"source": current, "target": ref})


class MovementSystem:
    def __init__(self, context):
        self.ctx = context

    @staticmethod
    def checkpoint_type(cp):
        value = cp.get("type") or 0
        return value.get("value", 0) if isinstance(value, dict) else value

    def tick(self, session):
        if self.ctx.state().get("finished"):
            return
        for entity in session.world.entities():
            ref = entity["id"]
            if not self.ctx.alive(ref) or self.ctx.spatial.blocked_by(ref):
                continue
            spatial = self.ctx.get(ref, ("spatial",), {})
            route = spatial.get("route")
            if not route:
                continue
            state = spatial.setdefault("movement", {"checkpoint": 0, "path_index": 0})
            checkpoints = route.get("checkpoints") or []
            cursor = state["checkpoint"]
            if cursor < len(checkpoints) and self.checkpoint_type(checkpoints[cursor]) == 1:
                if "wait_until" not in state:
                    state["wait_until"] = session.time+self.ctx.quantize(checkpoints[cursor].get("time") or 0)
                    self.ctx.emit("movement.wait", {"source": ref, "until": state["wait_until"]})
                if session.time < state["wait_until"]:
                    self.ctx.set(ref, ("spatial",), spatial)
                    continue
                state.pop("wait_until")
                state["checkpoint"] += 1
                state["path_index"] = 0
                spatial.pop("movement_path", None)
                cursor += 1
            point = checkpoints[cursor].get("position") if cursor < len(checkpoints) else route["endPosition"]
            if not spatial.get("movement_path"):
                spatial["movement_path"] = self.ctx.calc("movement.path", {"map": self.ctx.spatial.map_definition,
                        "origin": spatial["position"], "destination": point, "checkpoints": checkpoints}, source=ref,
                        component=spatial.get("rules", {}))
                state["path_index"] = 0
            path = spatial["movement_path"]
            if state["path_index"] >= len(path):
                if cursor >= len(checkpoints):
                    self.ctx.lifecycle.exit(ref)
                    continue
                state["checkpoint"] += 1
                state["path_index"] = 0
                spatial.pop("movement_path")
                self.ctx.set(ref, ("spatial",), spatial)
                continue
            target = path[state["path_index"]]
            origin = spatial["position"]
            dx, dy = target["col"]-origin["col"], target["row"]-origin["row"]
            distance = math.hypot(dx, dy)
            speed = self.ctx.calc("movement.speed", {"attributes": {}, "terrain": {},
                    "movement_parameters": {"base_speed": self.ctx.role_value(ref, "movement_speed")}}, source=ref,
                    component=spatial.get("rules", {}))
            step = self.ctx.calc("movement.distance", {"speed": speed, "delta_seconds": session.quantum,
                    "modifiers": []}, source=ref, component=spatial.get("rules", {}))
            if distance <= step:
                spatial["position"] = dict(target)
                state["path_index"] += 1
            elif distance:
                spatial["position"] = {"row": origin["row"]+dy/distance*step,
                                       "col": origin["col"]+dx/distance*step}
            self.ctx.set(ref, ("spatial",), spatial)
        self.ctx.spatial.blocking()

    def displace(self, source, target, effect, ability):
        position = self.ctx.get(target, ("spatial", "position"))
        if "position" in effect:
            destination = effect["position"]
        else:
            offset = effect.get("offset", {"row": 0, "col": effect.get("distance", 0)})
            destination = {"row": position["row"]+offset.get("row", 0), "col": position["col"]+offset.get("col", 0)}
        if not self.ctx.spatial.grid.inside(round(destination["row"]), round(destination["col"])):
            raise ValueError("displacement exits the declared map")
        self.ctx.set(target, ("spatial", "position"), destination)
        self.ctx.set(target, ("runtime", "blocked_by"), None)
        self.ctx.emit("movement.displaced", {"source": source, "target": target, "position": destination})
