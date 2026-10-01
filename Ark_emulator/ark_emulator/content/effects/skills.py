"""Declarative skill effects for the initial E0 operator profiles."""
from ...core.clock import ticks


class AttackBurst:
    @staticmethod
    def attack_parameters(skill):
        bb = skill["blackboard"]
        return float(bb["atk_scale"]), int(bb["times"])


class CostSkill:
    @staticmethod
    def activate(battle, unit, skill):
        battle.battle_cost_add(float(skill["blackboard"]["cost"]))


class HealingSkill:
    @staticmethod
    def activate(battle, unit, skill):
        duration = ticks(skill["duration"])
        effect = battle.repository.registry.require("stat_buff_v1", skill["key"])
        effect.apply(battle, unit, skill["key"], "atk", skill["blackboard"]["atk"], duration)
        unit.skill_until = battle.tick + duration
