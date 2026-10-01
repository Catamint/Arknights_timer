"""A small lazy registry, with no import of unrelated content handlers."""
from importlib import import_module
from .schemas import UnsupportedContent

ENTRIES = {
    "ground_melee_v1": ("ark_emulator.content.behaviors.ground", "GroundMelee"),
    "stat_buff_v1": ("ark_emulator.content.effects.attributes", "StatBuffEffect"),
    "attack_burst_v1": ("ark_emulator.content.effects.skills", "AttackBurst"),
    "cost_skill_v1": ("ark_emulator.content.effects.skills", "CostSkill"),
    "healing_skill_v1": ("ark_emulator.content.effects.skills", "HealingSkill"),
}


class Registry:
    def __init__(self, entries=None):
        self.entries = dict(ENTRIES if entries is None else entries)
        self.loaded = {}

    def require(self, key, source):
        if key not in self.entries:
            raise UnsupportedContent(source, f"missing handler {key}")
        if key not in self.loaded:
            module, name = self.entries[key]
            self.loaded[key] = getattr(import_module(module), name)
        return self.loaded[key]
