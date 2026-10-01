class StatBuffEffect:
    @staticmethod
    def apply(battle, unit, key, stat, multiplier, duration_ticks):
        return battle.buffs.apply(unit, key, {"stat": stat, "mul": multiplier},
                                  duration_ticks, unit)
