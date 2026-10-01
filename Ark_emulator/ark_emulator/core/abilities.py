"""Shared windup, target lock, impact and SP lifecycle."""
from . import damage, targeting
from .clock import RATE, DT, ticks
from .projectiles import ProjectileState


class AbilitySystem:
    def __init__(self, battle):
        self.battle = battle

    def hit(self, source, target, amount, damage_type, skill_attack=False):
        b = self.battle
        if target.dead:
            return
        if source.definition.ability.healing:
            damage.heal(b, target, amount, source)
        else:
            damage.apply(b, target, amount, damage_type, source)
        b.emit(b.tick, "attack_hit", {"unit": source.inst_id, "target": target.inst_id,
                                      "skill": skill_attack})
        skill = source.definition.skill
        if not source.dead and skill and skill["spType"] == 2 and not skill_attack and not source.skill_until:
            source.sp = min(source.sp_max, source.sp + skill["increment"])

    def activate_skill(self, unit, automatic=False):
        b, skill = self.battle, unit.definition.skill
        if not skill:
            return False, "invalid_skill"
        if skill["handler"] == "attack_burst_v1":
            return False, "automatic_attack_skill"
        if skill["automatic"] and not automatic:
            return False, "automatic_skill"
        if unit.skill_until:
            return False, "already_active"
        if unit.sp + 1e-9 < skill["spCost"]:
            return False, "not_ready"
        unit.sp -= skill["spCost"]
        handler = b.repository.registry.require(skill["handler"], skill["key"])
        handler.activate(b, unit, skill)
        b.stats["skillCasts"] += 1
        b.emit(b.tick, "skill_cast", {"unit": unit.inst_id, "skill": skill["key"]})
        return True, skill["key"]

    def tick_unit(self, unit):
        b, skill = self.battle, unit.definition.skill
        if unit.dead:
            return
        if unit.skill_until and b.tick >= unit.skill_until:
            unit.skill_until = 0
            b.emit(b.tick, "skill_finish", {"unit": unit.inst_id})
        if skill and skill["spType"] == 1 and not unit.skill_until and unit.sp < unit.sp_max:
            unit.sp_acc += DT * skill["increment"] * unit.attributes.get("spRecoveryPerSec")
            while unit.sp_acc + 1e-9 >= 1 and unit.sp < unit.sp_max:
                unit.sp_acc -= 1
                unit.sp += 1
        if skill and skill["automatic"] and skill["handler"] != "attack_burst_v1" and unit.sp >= unit.sp_max:
            self.activate_skill(unit, automatic=True)
        pending = unit.pending_attack
        if pending:
            if pending["target"].dead:
                unit.pending_attack = None
            elif b.tick >= pending["hit_tick"]:
                target, ability = pending["target"], unit.definition.ability
                amount = unit.attributes.get("atk") * pending["scale"]
                for _ in range(pending["hits"]):
                    if ability.projectile_speed:
                        b.projectiles.append(ProjectileState(unit, target, amount, ability.damage_type,
                            ability.projectile_speed, ability.projectile_key, unit.pos_x, unit.pos_y, pending["skill"]))
                        b.emit(b.tick, "projectile_launch", {"unit": unit.inst_id, "target": target.inst_id})
                    else:
                        self.hit(unit, target, amount, ability.damage_type, pending["skill"])
                unit.pending_attack = None
        if unit.pending_attack or b.tick < unit.attack_ready_at:
            return
        # A failed search also observes the three-tick selector cadence.
        if b.tick < getattr(unit, "search_at", 0):
            return
        unit.search_at = b.tick + 3
        target = targeting.choose(unit, b)
        if target is None:
            return
        scale, hits, skill_attack = 1.0, 1, False
        if skill and skill["handler"] == "attack_burst_v1" and unit.sp >= unit.sp_max:
            handler = b.repository.registry.require(skill["handler"], skill["key"])
            scale, hits = handler.attack_parameters(skill)
            unit.sp -= unit.sp_max
            skill_attack = True
            b.stats["skillCasts"] += 1
            b.emit(b.tick, "skill_cast", {"unit": unit.inst_id, "skill": skill["key"]})
        interval = unit.attributes.attack_interval()
        unit.pending_attack = {"target": target, "hit_tick": b.tick + max(1, ticks(interval * unit.definition.ability.hit_ratio)),
                               "scale": scale, "hits": hits, "skill": skill_attack}
        unit.attack_ready_at = b.tick + interval * RATE
        b.emit(b.tick, "attack_start", {"unit": unit.inst_id, "target": target.inst_id,
                                       "hitTick": unit.pending_attack["hit_tick"],
                                       "ability": unit.definition.ability.key})
