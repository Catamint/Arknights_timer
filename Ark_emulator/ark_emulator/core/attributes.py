"""Four explicit layers; direct percentages add, final factors multiply."""


class Attributes:
    DEFAULTS = {"attackSpeed": 100.0, "baseAttackTime": 1.0,
                "damageHitratePhysical": 100.0, "damageHitrateMagical": 100.0}

    def __init__(self, base):
        self.base = dict(self.DEFAULTS, **dict(base))
        self.modifiers = {}

    def get(self, stat):
        v = self.base.get(stat, 0.0)
        add = mul = final_add = 0.0
        final_mul = 1.0
        for record in self.modifiers.values():
            if record["stat"] != stat:
                continue
            add += record.get("add", 0)
            mul += record.get("mul", 0)
            final_add += record.get("final_add", 0)
            final_mul *= record.get("final_mul", 1)
        return ((v + add) * (1 + mul) + final_add) * final_mul

    def attack_interval(self):
        return self.get("baseAttackTime") * 100 / max(1, self.get("attackSpeed"))

    def to_dict(self):
        return {key: round(self.get(key), 5) for key in self.base}
