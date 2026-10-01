"""Small battle orchestrator. Concrete content lives outside this module."""
import copy
import heapq
import math
import threading
from .clock import Clock, RATE, DT, ticks
from .events import EventLog
from .entities import UnitState
from .commands import Command, validate_deploy
from .movement import RouteCursor
from .buffs import BuffSystem
from .abilities import AbilitySystem
from . import blocking, projectiles
from ..content.schemas import UnsupportedContent, thaw
from ..map import GameMap, TileData
from ..waves import RuntimeWaveScheduler
from ..rng import SystemRandomClone

DEFAULT_SQUAD = [{"charId": "char_502_nblade", "phase": 0, "level": 1},
                 {"charId": "char_124_kroos", "phase": 0, "level": 1, "skillLevels": [1]}]


class Battle:
    mechanics_version = "core_v1"

    def __init__(self, repository, level_id, squad=None, seed=None):
        self.lock = threading.RLock()
        self.repository = repository
        self._squad = copy.deepcopy(DEFAULT_SQUAD if squad is None else squad)
        self.initial_squad = copy.deepcopy(self._squad)
        self.content = repository.load(level_id, self._squad)
        self.level_id = level_id
        self.clock = Clock()
        self.paused, self.finished, self.result = False, False, None
        self.events = EventLog()
        self.rng = SystemRandomClone(seed if seed is not None else self.content.level.get("randomSeed", 0))
        self.seed = seed if seed is not None else self.content.level.get("randomSeed", 0)
        self._next_inst_id = 1
        self._command_order = 0
        self._commands, self.command_results, self.command_history = [], {}, []
        self.enemies, self.operators, self.tokens, self.projectiles = [], [], [], []
        self._redeploy_until, self._deploy_counts = {}, {}
        level = self.content.level
        mm = level["map"]
        self.map = GameMap(mm["rows"], mm["cols"], [TileData(i, dict(t)) for i, t in enumerate(mm["tiles"])])
        self.routes = thaw(level["routes"])
        opt = level["options"]
        self.max_life_point = self.life_point = int(opt["maxLifePoint"])
        self.initial_cost = self.cost = float(opt["initialCost"])
        self.max_cost = float(opt["maxCost"])
        self.character_limit = int(opt["characterLimit"])
        self.cost_increase_time = float(opt["costIncreaseTime"])
        self.move_multiplier = float(opt["moveMultiplier"])
        self._cost_acc = 0.0
        self.stats = {"kills": 0, "leaks": 0, "lifeLost": 0, "deployments": 0,
                      "operatorDeaths": 0, "skillCasts": 0,
                      "playerDamageDealt": 0.0, "playerDamageTaken": 0.0}
        self.buffs = BuffSystem(self)
        self.abilities = AbilitySystem(self)
        self.waves = RuntimeWaveScheduler(thaw(level["waves"]), self, rng=self.rng)

    @property
    def tick(self):
        return self.clock.tick

    @property
    def squad(self):
        return copy.deepcopy(self._squad)

    @squad.setter
    def squad(self, value):
        self.configure_squad(value)

    def configure_squad(self, value):
        with self.lock:
            content = self.repository.load(self.level_id, value)
            self.content = content
            self._squad = copy.deepcopy(value)

    def emit(self, tick, event_type, data=None):
        return self.events.emit(tick, event_type, data)

    def _new_unit(self, definition, side, row, col, direction=1):
        unit = UnitState(definition, self._next_inst_id, side, row, col, direction, self.tick)
        self._next_inst_id += 1
        return unit

    def spawn_enemy(self, key, route_index, source_ev=None):
        definition = self.content.enemies.get(key)
        if definition is None:
            raise UnsupportedContent(self.level_id, f"undeclared enemy {key}")
        if not isinstance(route_index, int) or not 0 <= route_index < len(self.routes):
            raise UnsupportedContent(key, f"invalid route {route_index}")
        route = self.routes[route_index]
        start = route["startPosition"]
        unit = self._new_unit(definition, 0, start["row"], start["col"])
        unit.route, unit.route_index = route, route_index
        radius, offset = route.get("spawnRandomRange") or {}, route.get("spawnOffset") or {}
        for axis, attribute in (("x", "pos_x"), ("y", "pos_y")):
            r = float(radius.get(axis) or 0)
            shift = float(offset.get(axis) or 0)
            if r:
                draw = self.rng.next_float()
                shift += (2 * draw - 1) * r
                self.emit(self.tick, "rng_consumed", {"unit": unit.inst_id, "reason": "spawn_" + axis, "value": draw})
            setattr(unit, attribute, getattr(unit, attribute) + shift)
        unit.movement = RouteCursor(route, self.map)
        unit.behavior = self.repository.registry.require(definition.behavior, key)()
        ev = source_ev or {}
        unit._wave_index, unit._fragment_index = ev.get("wave"), ev.get("fragment")
        unit._dont_block_wave = bool(ev.get("dontBlockWave"))
        self.enemies.append(unit)
        self.emit(self.tick, "enemy_spawn", {"unit": unit.inst_id, "key": key,
                                            "routeIndex": route_index, "attributes": unit.attributes.to_dict()})
        return unit

    def deploy_cost(self, key):
        base = self.content.operators[key].attributes.get("cost", 0)
        factor = (1.0, 1.5, 2.0)[min(2, self._deploy_counts.get(key, 0))]
        return math.floor(base * factor)

    def deploy(self, key, row, col, direction=1, auto_summon=False, skill_index=None):
        with self.lock:
            if auto_summon:
                return False, "summons_not_supported"
            reason = validate_deploy(self, key, row, col, direction, skill_index)
            if reason:
                return False, reason
            cost = self.deploy_cost(key)
            if self.cost < cost:
                return False, "insufficient_cost"
            self.cost -= cost
            unit = self._new_unit(self.content.operators[key], 1, row, col, direction)
            unit.state = "deployed"
            unit.paid_cost = cost
            self.operators.append(unit)
            self._deploy_counts[key] = self._deploy_counts.get(key, 0) + 1
            self.stats["deployments"] += 1
            self.emit(self.tick, "deploy", {"charId": key, "instId": unit.inst_id,
                                           "row": row, "col": col, "direction": direction, "cost": cost})
            return True, unit.inst_id

    def retire(self, unit, reason):
        if unit.dead:
            return
        unit.dead, unit.state, unit.pending_attack = True, reason, None
        if unit.side:
            self._redeploy_until[unit.char_id] = self.tick + ticks(unit.attributes.get("respawnTime"))
            if reason == "death":
                self.stats["operatorDeaths"] += 1
            if unit in self.operators:
                self.operators.remove(unit)
            for enemy in unit.blocked_enemies:
                enemy.blocked_by = None
            unit.blocked_enemies.clear()
        else:
            if unit.blocked_by:
                unit.blocked_by.blocked_enemies.remove(unit)
                unit.blocked_by = None
            if reason == "death":
                self.stats["kills"] += 1
        for buff in list(unit.buffs):
            self.buffs.remove(buff)
        self.emit(self.tick, "operator_dead" if unit.side and reason == "death" else
                  "enemy_dead" if not unit.side and reason == "death" else reason,
                  {"unit": unit.inst_id, "key": unit.definition.key})

    def reach_exit(self, unit):
        loss = unit.definition.life_point_reduce
        self.life_point = max(0, self.life_point - loss)
        self.stats["leaks"] += 1
        self.stats["lifeLost"] += loss
        self.emit(self.tick, "enemy_reach_exit", {"unit": unit.inst_id, "key": unit.enemy_key,
                  "row": unit.row, "col": unit.col, "routeIndex": unit.route_index, "lifePoint": self.life_point})
        self.retire(unit, "exited")

    def withdraw(self, inst_id):
        with self.lock:
            if self.finished:
                return False, "battle_finished"
            unit = next((u for u in self.operators if u.inst_id == inst_id), None)
            if unit is None:
                return False, "not_deployed"
            self.battle_cost_add(math.floor(unit.paid_cost / 2))
            self.retire(unit, "withdraw")
            return True, inst_id

    def activate_skill(self, inst_id, skill_index=0):
        with self.lock:
            if self.finished:
                return False, "battle_finished"
            unit = next((u for u in self.operators if u.inst_id == inst_id), None)
            if unit is None:
                return False, "not_deployed"
            if skill_index != 0:
                return False, "invalid_skill"
            return self.abilities.activate_skill(unit)

    def battle_cost_add(self, amount):
        with self.lock:
            self.cost = min(self.max_cost, self.cost + float(amount))

    def execute(self, action):
        with self.lock:
            kind = action.get("type", action.get("action"))
            if kind == "deploy":
                result = self.deploy(action.get("charId"), action.get("row"), action.get("col"),
                                     action.get("direction", 1), skill_index=action.get("skillIndex"))
            elif kind == "withdraw":
                result = self.withdraw(action.get("instId"))
            elif kind == "skill":
                result = self.activate_skill(action.get("instId"), action.get("skillIndex", 0))
            elif kind == "configure_squad":
                self.configure_squad(action.get("squad", []))
                result = (True, "squad_updated")
            elif kind in ("pause", "resume"):
                self.paused = kind == "pause"
                result = (True, kind)
            else:
                result = (False, "unknown_action")
            self.command_history.append({"tick": self.tick, "action": copy.deepcopy(action),
                                         "ok": result[0], "result": result[1]})
            return result

    def schedule(self, action, tick):
        with self.lock:
            if self.finished:
                raise ValueError("cannot schedule commands after battle end")
            if not isinstance(action, dict):
                raise ValueError("command must be an object")
            if isinstance(tick, bool) or not isinstance(tick, int) or tick < self.tick:
                raise ValueError("command tick must be an integer at or after the current tick")
            self._command_order += 1
            cmd = Command(tick, self._command_order, action.get("type", action.get("action")), copy.deepcopy(action))
            heapq.heappush(self._commands, (tick, cmd.order, cmd))
            return cmd.order

    def tick_once(self, force=False):
        with self.lock:
            if self.finished or (self.paused and not force):
                return
            while self._commands and self._commands[0][0] <= self.tick:
                _, order, command = heapq.heappop(self._commands)
                self.command_results[order] = self.execute(command.payload)
            if self.paused and not force:
                return
            self.waves.update()
            self.buffs.tick()
            blocking.update(self)
            for unit in list(self.enemies):
                unit.behavior.tick(unit, self)
            blocking.update(self)
            for unit in list(self.operators):
                self.abilities.tick_unit(unit)
            projectiles.update(self)
            self.enemies = [u for u in self.enemies if not u.dead]
            if self.cost < self.max_cost and self.cost_increase_time > 0:
                self._cost_acc += DT
                while self._cost_acc + 1e-9 >= self.cost_increase_time and self.cost < self.max_cost:
                    self._cost_acc -= self.cost_increase_time
                    self.cost = min(self.max_cost, self.cost + 1)
            if self.life_point <= 0:
                self.finished, self.result = True, "defeat"
            elif self.waves.finished and not self.enemies:
                self.finished, self.result = True, "victory"
            if self.finished:
                self.emit(self.tick, "battle_end", {"result": self.result, "stats": dict(self.stats)})
            self.clock.tick += 1

    def advance(self, n, force=False):
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("tick count must be a nonnegative integer")
        with self.lock:
            for _ in range(n):
                if self.finished:
                    break
                self.tick_once(force=force)
            return self.snapshot()

    def legal_actions(self, include_directions=False, max_cells=64):
        with self.lock:
            actions = []
            if self.finished:
                return actions
            for key in self.content.operators:
                count = 0
                for row in range(self.map.rows):
                    for col in range(self.map.cols):
                        if count >= max_cells:
                            break
                        for direction in ((0, 1, 2, 3) if include_directions else (1,)):
                            if count >= max_cells:
                                break
                            if validate_deploy(self, key, row, col, direction) is None and self.cost >= self.deploy_cost(key):
                                actions.append({"type": "deploy", "charId": key, "row": row, "col": col, "direction": direction})
                                count += 1
            for unit in self.operators:
                actions.append({"type": "withdraw", "instId": unit.inst_id})
                skill = unit.definition.skill
                if skill and not skill["automatic"] and not unit.skill_until and unit.sp >= unit.sp_max:
                    actions.append({"type": "skill", "instId": unit.inst_id, "skillIndex": 0})
            return actions

    def snapshot(self, since_seq=0):
        with self.lock:
            return {"backend": "modular", "mechanicsVersion": self.mechanics_version,
                    "levelId": self.level_id, "tick": self.tick, "t": self.tick / RATE,
                    "paused": self.paused, "finished": self.finished, "result": self.result,
                    "lifePoint": self.life_point, "cost": round(self.cost, 6), "maxCost": self.max_cost,
                    "deployCosts": {key: self.deploy_cost(key) for key in self.content.operators},
                    "deployed": [u.to_dict(self.tick) for u in self.operators],
                    "enemies": [u.to_dict(self.tick) for u in self.enemies], "tokens": [], "summons": [],
                    "projectiles": [p.to_dict() for p in self.projectiles],
                    "stats": copy.deepcopy(self.stats), "map": self.map.to_dict(),
                    "routes": copy.deepcopy(self.routes), "routePaths": [self.map.route_path(r) for r in self.routes],
                    "waves": {"remaining": self.waves.remaining(), "spawned": self.waves.spawned,
                              "finished": self.waves.finished, "nextSpawnAt": self.waves.next_spawn_at(), **self.waves.status()},
                    "redeploys": [{"charId": k, "redeployIn": max(0, t-self.tick)/RATE} for k,t in self._redeploy_until.items()],
                    "support": thaw(self.content.report), "events": self.events.snapshot_events(since_seq),
                    "gainedTokens": {}, "predefines": {"tokens": 0, "characters": 0, "pending": 0}}

    def __deepcopy__(self, memo):
        result = type(self).__new__(type(self))
        memo[id(self)] = result
        for key, value in self.__dict__.items():
            if key == "lock":
                setattr(result, key, threading.RLock())
            elif key in ("content", "repository"):
                setattr(result, key, value)
                memo[id(value)] = value
            else:
                setattr(result, key, copy.deepcopy(value, memo))
        return result
