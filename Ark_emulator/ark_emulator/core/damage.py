"""Shared mitigation and HP settlement for all content handlers."""


def calculate(amount, attributes, damage_type):
    amount = max(0.0, float(amount))
    if damage_type == 0:
        return max(amount * 0.05, amount - max(0, attributes.get("def")))
    if damage_type == 1:
        return max(amount * 0.05, amount * (1 - min(100, max(0, attributes.get("magicResistance"))) / 100))
    if damage_type == 2:
        return amount
    raise ValueError(f"unmigrated damage type {damage_type}")


def apply(battle, target, amount, damage_type, source):
    if target.dead:
        return 0.0
    dealt = min(target.hp, calculate(amount, target.attributes, damage_type))
    target.hp -= dealt
    if source.side:
        battle.stats["playerDamageDealt"] += dealt
    if target.side:
        battle.stats["playerDamageTaken"] += dealt
    battle.emit(battle.tick, "damage", {"source": source.inst_id, "target": target.inst_id,
                                      "amount": dealt, "damageType": damage_type,
                                      "hp": target.hp, "ability": source.definition.ability.key})
    if target.hp <= 1e-9:
        battle.retire(target, "death")
    return dealt


def heal(battle, target, amount, source):
    if target.dead:
        return 0.0
    actual = min(max(0, amount), target.max_hp - target.hp)
    target.hp += actual
    battle.emit(battle.tick, "heal", {"source": source.inst_id, "target": target.inst_id,
                                    "amount": actual, "hp": target.hp})
    return actual
