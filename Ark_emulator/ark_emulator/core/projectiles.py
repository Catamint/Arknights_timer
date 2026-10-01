"""Launched projectiles own their damage payload and remain after retreat."""
from dataclasses import dataclass
import math
from .clock import DT


@dataclass
class ProjectileState:
    source: object
    target: object
    amount: float
    damage_type: int
    speed: float
    key: str
    pos_x: float
    pos_y: float
    skill_attack: bool = False

    def to_dict(self):
        return {"key": self.key, "source": self.source.inst_id,
                "target": self.target.inst_id,
                "pos": {"x": self.pos_x, "y": self.pos_y}, "speed": self.speed}


def update(battle):
    remaining = []
    for p in battle.projectiles:
        if p.target.dead:
            continue
        dx, dy = p.target.pos_x - p.pos_x, p.target.pos_y - p.pos_y
        dist = math.hypot(dx, dy)
        if dist <= p.speed * DT:
            battle.abilities.hit(p.source, p.target, p.amount, p.damage_type, p.skill_attack)
        else:
            p.pos_x += dx / dist * p.speed * DT
            p.pos_y += dy / dist * p.speed * DT
            remaining.append(p)
    battle.projectiles = remaining
