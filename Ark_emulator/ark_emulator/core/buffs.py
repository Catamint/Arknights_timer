"""Battle-owned buff instances with keyed modifiers and precise expiry."""
from dataclasses import dataclass


@dataclass
class BuffInstance:
    uid: int
    key: str
    source: int
    target: object
    expiry_tick: int
    modifier: dict

    def to_dict(self):
        return {"id": self.uid, "key": self.key, "source": self.source,
                "expiryTick": self.expiry_tick, "modifier": dict(self.modifier)}


class BuffSystem:
    def __init__(self, battle):
        self.battle = battle
        self.instances = {}
        self._next_id = 1

    def apply(self, unit, key, modifier, duration_ticks, source):
        # Same key and source refresh; distinct sources have independent lifetimes.
        existing = next((b for b in unit.buffs if b.key == key and b.source == source.inst_id), None)
        if existing:
            existing.expiry_tick = self.battle.tick + duration_ticks
            existing.modifier = dict(modifier)
            unit.attributes.modifiers[existing.uid] = dict(modifier)
            return existing
        buff = BuffInstance(self._next_id, key, source.inst_id, unit,
                            self.battle.tick + duration_ticks, dict(modifier))
        self._next_id += 1
        self.instances[buff.uid] = buff
        unit.buffs.append(buff)
        unit.attributes.modifiers[buff.uid] = dict(modifier)
        self.battle.emit(self.battle.tick, "buff_applied", buff.to_dict())
        return buff

    def remove(self, buff):
        self.instances.pop(buff.uid, None)
        buff.target.attributes.modifiers.pop(buff.uid, None)
        if buff in buff.target.buffs:
            buff.target.buffs.remove(buff)
        self.battle.emit(self.battle.tick, "buff_expired", buff.to_dict())

    def tick(self):
        for buff in list(self.instances.values()):
            if buff.target.dead or self.battle.tick >= buff.expiry_tick:
                self.remove(buff)
