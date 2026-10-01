"""One validation path used by API, queued replay and AI legal actions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    tick: int
    order: int
    action: str
    payload: dict


def validate_deploy(battle, key, row, col, direction=1, skill_index=None):
    if battle.finished:
        return "battle_finished"
    if not isinstance(key, str):
        return "invalid_character"
    definition = battle.content.operators.get(key)
    if definition is None:
        return "not_in_supported_squad"
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (row, col, direction)):
        return "invalid_position"
    if direction not in (0, 1, 2, 3):
        return "invalid_direction"
    if skill_index is not None and (not definition.skill or skill_index != 0):
        return "invalid_skill"
    tile = battle.map.tile(row, col)
    mask = 2 if definition.position == 2 else 1
    if tile is None or not tile.buildable_type or not tile.buildable_type & mask:
        return "not_buildable"
    if any(u.row == row and u.col == col for u in battle.operators):
        return "occupied"
    if any(u.char_id == key for u in battle.operators):
        return "already_deployed"
    if len(battle.operators) >= battle.character_limit:
        return "character_limit"
    if battle.tick < battle._redeploy_until.get(key, 0):
        return "on_cooldown"
    if battle.cost + 1e-9 < definition.attributes.get("cost", 0):
        return "insufficient_cost"
    return None
