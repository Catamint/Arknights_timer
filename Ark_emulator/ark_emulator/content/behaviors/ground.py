"""Ground melee lifecycle shared by the first two enemy definitions."""


class GroundMelee:
    def transition(self, unit, state, battle, reason):
        if unit.state != state:
            old, unit.state = unit.state, state
            battle.emit(battle.tick, "enemy_state", {"unit": unit.inst_id, "from": old,
                                                   "to": state, "reason": reason})

    def tick(self, unit, battle):
        if unit.dead:
            return
        if unit.blocked_by:
            self.transition(unit, "blocked", battle, "blocker_present")
        else:
            self.transition(unit, "moving", battle, "unblocked")
            unit.movement.tick(unit, battle)
        if not unit.dead:
            battle.abilities.tick_unit(unit)
