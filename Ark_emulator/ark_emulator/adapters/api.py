"""Stable facade selecting migrated content or an explicit legacy boundary."""
import copy
import threading
from ..content.repository import ContentRepository
from ..content.schemas import UnsupportedContent, thaw


class Simulator:
    def __init__(self, level_id="level_main_01-01", stage_id=None,
                 data_dir=None, squad=None, seed=None, custom_enemies=None,
                 custom_level=None, rune_difficulty=1, backend="auto", pack_dir=None):
        if backend not in ("auto", "modular", "legacy"):
            raise ValueError("backend must be auto, modular or legacy")
        if stage_id == "main_00-01" and level_id is None:
            level_id = "level_main_00-01"
        elif stage_id is not None and level_id is None:
            from ..loader import DataStore
            level_id = DataStore(data_dir).stage_to_level(stage_id)
        self.level_id, self.seed = level_id, seed
        self.requested_backend = backend
        self._init_lock = threading.RLock()
        self.data_dir, self.pack_dir = data_dir, pack_dir
        self.custom_enemies, self.custom_level = custom_enemies or [], custom_level
        self.rune_difficulty = rune_difficulty
        self.repository = ContentRepository(pack_dir)
        self.backend = ("modular" if self.repository.has_level(level_id) else "legacy") if backend == "auto" else backend
        self._battle, self._store, self._legacy = None, None, None
        self._squad = copy.deepcopy(squad)
        if self.backend == "modular":
            if data_dir or custom_enemies or custom_level is not None or rune_difficulty != 1:
                raise UnsupportedContent(level_id, "custom data, custom enemies and non-normal runes require a migrated profile or explicit legacy backend")
        else:
            from .legacy import LegacySimulator
            self._legacy = LegacySimulator(level_id, stage_id, data_dir, squad, seed,
                                            custom_enemies, custom_level, rune_difficulty)

    @property
    def battle(self):
        if self._legacy:
            return self._legacy.battle
        if self._battle is None:
            with self._init_lock:
                if self._battle is None:
                    from ..core.battle import Battle
                    self._battle = Battle(self.repository, self.level_id, self._squad, self.seed)
        return self._battle

    @property
    def store(self):
        if self._legacy:
            return self._legacy.store
        if self._store is None:
            from ..loader import DataStore
            self._store = DataStore()
        return self._store

    @property
    def squad(self):
        if self._legacy:
            return self._legacy.squad
        if self._battle:
            return self._battle.squad
        if self._squad is None:
            from ..core.battle import DEFAULT_SQUAD
            return copy.deepcopy(DEFAULT_SQUAD)
        return copy.deepcopy(self._squad)

    @squad.setter
    def squad(self, value):
        if self._legacy:
            self._legacy.squad = value
            if self._legacy._battle is not None:
                self._legacy.battle.squad = value
        else:
            if self._battle:
                self.battle.execute({"type": "configure_squad", "squad": value})
            else:
                self.repository.load(self.level_id, value)
            self._squad = copy.deepcopy(value)

    def snapshot(self, since_seq=0):
        if self._legacy:
            snap = self._legacy.snapshot(since_seq)
            snap["backend"] = "legacy"
            snap["support"] = {"status": "legacy_unverified", "levelId": self.level_id}
            return snap
        return self.battle.snapshot(since_seq)

    def run(self, seconds=None, ticks=None):
        return self.run_ticks(ticks if ticks is not None else int(round((1.0 if seconds is None else seconds) * 30)))

    def run_ticks(self, n):
        if self._legacy:
            self._legacy.run_ticks(n)
            return self.snapshot()
        return self.battle.advance(n)

    def tick_once(self):
        return self.run_ticks(1)

    def pause(self):
        if self._legacy:
            self._legacy.pause()
        else:
            self.battle.execute({"type": "pause"})

    def resume(self):
        if self._legacy:
            self._legacy.resume()
        else:
            self.battle.execute({"type": "resume"})

    def step(self, n=1):
        if self._legacy:
            self._legacy.step(n)
            return self.snapshot()
        return self.battle.advance(n, force=True)

    def deploy(self, char_id, row, col, direction=1, auto_summon=False, skill_index=None):
        if self._legacy:
            return self._legacy.deploy(char_id, row, col, direction, auto_summon, skill_index)
        if auto_summon:
            return False, "summons_not_supported"
        return self.battle.execute({"type": "deploy", "charId": char_id, "row": row,
                                   "col": col, "direction": direction, "skillIndex": skill_index})

    def withdraw(self, inst_id):
        if self._legacy:
            return self._legacy.withdraw(inst_id)
        return self.battle.execute({"type": "withdraw", "instId": inst_id})

    def activate_skill(self, inst_id, skill_index=0):
        if self._legacy:
            return self._legacy.activate_skill(inst_id, skill_index)
        return self.battle.execute({"type": "skill", "instId": inst_id, "skillIndex": skill_index})

    def deploy_summon(self, *args, **kwargs):
        return self._legacy.deploy_summon(*args, **kwargs) if self._legacy else (False, "summons_not_supported")

    def deploy_token(self, *args, **kwargs):
        return self._legacy.deploy_token(*args, **kwargs) if self._legacy else (False, "tokens_not_supported")

    def withdraw_token(self, *args, **kwargs):
        return self._legacy.withdraw_token(*args, **kwargs) if self._legacy else (False, "tokens_not_supported")

    def deploy_gained_token(self, *args, **kwargs):
        return self._legacy.deploy_gained_token(*args, **kwargs) if self._legacy else (False, "tokens_not_supported")

    def schedule(self, action, tick):
        if self._legacy:
            raise UnsupportedContent(self.level_id, "queued commands require modular backend")
        return self.battle.schedule(action, tick)

    def export_replay(self):
        if self._legacy:
            raise UnsupportedContent(self.level_id, "replay export requires modular backend")
        b = self.battle
        with b.lock:
            return {"schemaVersion": 1, "levelId": self.level_id, "contentDigest": b.content.digest,
                    "mechanicsVersion": b.mechanics_version, "seed": b.seed,
                    "squad": copy.deepcopy(b.initial_squad), "untilTick": b.tick,
                    "commands": copy.deepcopy(b.command_history)}

    def available_operators(self):
        pack, _ = self.repository.pack(self.level_id)
        return thaw(pack["operators"])

    def __deepcopy__(self, memo):
        result = type(self).__new__(type(self))
        memo[id(self)] = result
        for key, value in self.__dict__.items():
            if key in ("repository", "_store"):
                setattr(result, key, value)
            elif key == "_init_lock":
                setattr(result, key, threading.RLock())
            else:
                setattr(result, key, copy.deepcopy(value, memo))
        return result

    @property
    def tick(self):
        return self.battle.tick

    @property
    def finished(self):
        return self.battle.finished
