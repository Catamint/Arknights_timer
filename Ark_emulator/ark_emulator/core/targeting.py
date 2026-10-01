"""Exact facing-aware range, deterministic priorities and search cadence."""


def choose(unit, battle):
    if not unit.side:
        return unit.blocked_by if unit.blocked_by and not unit.blocked_by.dead else None
    healing = unit.definition.ability.healing
    candidates = battle.operators if healing else battle.enemies
    cells = unit.range_cells()
    candidates = [u for u in candidates if not u.dead and (u.row, u.col) in cells
                  and (not healing or u.hp < u.max_hp - 1e-9)]
    if not candidates:
        return None
    if healing:
        return min(candidates, key=lambda u: (u.hp / u.max_hp, u.inst_id))
    return min(candidates, key=lambda u: (
        u not in unit.blocked_enemies, -u.attributes.get("tauntLevel"),
        u.movement.distance(u), u.inst_id))
