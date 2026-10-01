"""Route cursor uses the existing independent flow-field implementation."""
import math
from .clock import DT, ticks


class RouteCursor:
    def __init__(self, route, game_map):
        self.route, self.map = route, game_map
        self.index = 0
        self.wait_until = None

    def target(self):
        cps = self.route.get("checkpoints") or []
        cp = next((cp for cp in cps[self.index:] if self.map.checkpoint_type(cp) == 0), None)
        if cp is not None:
            pos = dict(cp.get("position") or {})
            off = cp.get("reachOffset") or {}
            pos["row"] = pos.get("row", 0) + (off.get("y") or 0)
            pos["col"] = pos.get("col", 0) + (off.get("x") or 0)
            return pos
        return self.route["endPosition"]

    def next_node(self, unit):
        pos = self.target()
        nxt, _, _ = self.map.build_route_field(self.route, pos)
        index = self.map.idx(unit.row, unit.col)
        return nxt[index] if nxt is not None and index >= 0 else -1

    def distance(self, unit):
        return self.map.route_distance_to_final(self.route, self.index, unit.row, unit.col)

    def tick(self, unit, battle):
        if unit.blocked_by or unit.dead:
            return
        cps = self.route.get("checkpoints") or []
        for _ in range(len(cps) + 1):
            if self.index < len(cps) and self.map.checkpoint_type(cps[self.index]) == 1:
                if self.wait_until is None:
                    self.wait_until = battle.tick + ticks(cps[self.index].get("time") or 0)
                    battle.emit(battle.tick, "checkpoint_wait", {"unit": unit.inst_id, "until": self.wait_until})
                if battle.tick < self.wait_until:
                    return
                self.wait_until = None
                self.index += 1
                continue
            target = self.target()
            dx, dy = target["col"] - unit.pos_x, target["row"] - unit.pos_y
            if math.hypot(dx, dy) > 0.05:
                break
            if self.index >= len(cps):
                battle.reach_exit(unit)
                return
            self.index += 1
        nxt = self.next_node(unit)
        if nxt < 0:
            raise RuntimeError(f"unreachable route {unit.route_index} at {unit.row},{unit.col}")
        r, c = self.map.rc(nxt)
        target = self.target()
        if (r, c) == (round(target["row"]), round(target["col"])):
            r, c = target["row"], target["col"]
        dx, dy = c - unit.pos_x, r - unit.pos_y
        distance = math.hypot(dx, dy)
        step = max(0, unit.attributes.get("moveSpeed")) * battle.move_multiplier * DT
        if distance <= step:
            unit.pos_x, unit.pos_y = float(c), float(r)
        elif distance:
            unit.pos_x += dx / distance * step
            unit.pos_y += dy / distance * step
        unit.row, unit.col = round(unit.pos_y), round(unit.pos_x)
