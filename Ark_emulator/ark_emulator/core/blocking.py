"""Blocking is a world rule, not a concrete enemy state-machine branch."""
import math


def update(battle):
    alive_ops = set(battle.operators)
    for op in battle.operators:
        op.blocked_enemies[:] = [e for e in op.blocked_enemies if not e.dead]
    for enemy in battle.enemies:
        if enemy.blocked_by and (enemy.blocked_by.dead or enemy.blocked_by not in alive_ops):
            enemy.blocked_by = None
        if enemy.dead or enemy.blocked_by:
            continue
        nxt = enemy.movement.next_node(enemy)
        idx = battle.map.idx(enemy.row, enemy.col)
        for op in battle.operators:
            if len(op.blocked_enemies) >= int(op.attributes.get("blockCnt")):
                continue
            if not battle.map.next_segment_contains(idx, nxt, op.row, op.col):
                continue
            if math.hypot(enemy.pos_x - op.pos_x, enemy.pos_y - op.pos_y) <= 1 + 1e-9:
                enemy.blocked_by = op
                op.blocked_enemies.append(enemy)
                battle.emit(battle.tick, "block", {"unit": op.inst_id, "target": enemy.inst_id})
                break
