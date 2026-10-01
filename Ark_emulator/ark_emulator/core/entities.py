"""Small runtime entities, with no concrete enemy or operator branches."""
from dataclasses import dataclass, field
from .attributes import Attributes


@dataclass(eq=False)
class UnitState:
    definition: object
    inst_id: int
    side: int
    row: int
    col: int
    direction: int = 1
    deploy_tick: int = 0
    attributes: object = field(init=False)
    hp: float = field(init=False)
    pos_x: float = field(init=False)
    pos_y: float = field(init=False)
    dead: bool = False
    state: str = "born"
    sp: float = 0.0
    sp_acc: float = 0.0
    skill_until: int = 0
    attack_ready_at: float = 0.0
    pending_attack: object = None
    blocked_by: object = None
    blocked_enemies: list = field(default_factory=list)
    buffs: list = field(default_factory=list)
    movement: object = None
    behavior: object = None
    route: object = None
    route_index: int = 0
    _managed_by_scheduler: bool = True
    _wave_index: object = None
    _fragment_index: object = None
    _dont_block_wave: bool = False
    _released_from_wave: bool = False
    _block_fragment: bool = False

    def __post_init__(self):
        self.attributes = Attributes(self.definition.attributes)
        self.hp = self.max_hp
        self.pos_x, self.pos_y = float(self.col), float(self.row)
        if self.definition.skill:
            self.sp = self.definition.skill.get("initSp", 0)

    @property
    def max_hp(self):
        return max(1.0, self.attributes.get("maxHp"))

    @property
    def char_id(self):
        return self.definition.key

    @property
    def enemy_key(self):
        return self.definition.key

    @property
    def sp_max(self):
        return self.definition.skill.get("spCost", 0) if self.definition.skill else 0

    def range_cells(self):
        cells = []
        for r, c in self.definition.range_shape:
            if self.direction == 0:
                dr, dc = -c, r
            elif self.direction == 2:
                dr, dc = c, -r
            elif self.direction == 3:
                dr, dc = -r, -c
            else:
                dr, dc = r, c
            cells.append((self.row + dr, self.col + dc))
        return set(cells)

    def to_dict(self, tick=0):
        skill = self.definition.skill
        skills = ([{"skillId": skill["key"], "spCost": skill["spCost"],
                    "name": skill.get("name", skill["key"]), "equipped": True,
                    "spType": skill["spType"], "duration": skill["duration"],
                    "automatic": skill["automatic"], "onCooldown": bool(self.skill_until)}]
                  if skill else [])
        out = {"instId": self.inst_id, "row": self.row, "col": self.col,
               "pos": {"x": round(self.pos_x, 5), "y": round(self.pos_y, 5)},
               "hp": round(self.hp, 5), "maxHp": round(self.max_hp, 5),
               "sp": round(self.sp, 5), "spMax": self.sp_max, "state": self.state,
               "dead": self.dead, "direction": self.direction,
               "atk": round(self.attributes.get("atk"), 5),
               "def": round(self.attributes.get("def"), 5),
               "magicResistance": self.attributes.get("magicResistance"),
               "mres": self.attributes.get("magicResistance"),
               "attackSpeed": self.attributes.get("attackSpeed"),
               "blockCnt": self.attributes.get("blockCnt"),
               "cost": self.attributes.get("cost"),
               "blocked": [u.inst_id for u in self.blocked_enemies if not u.dead],
               "blockedEnemies": [u.inst_id for u in self.blocked_enemies if not u.dead],
               "blockedBy": self.blocked_by.inst_id if self.blocked_by else None,
               "skills": skills, "equippedSkillIndex": 0 if skill else None,
               "activeSkill": ({"skillId": skill["key"], "remaining": max(0, self.skill_until - tick)/30}
                               if skill and self.skill_until else None),
               "buffs": [dict(b.to_dict(), remaining=max(0, b.expiry_tick-tick)/30) for b in self.buffs],
               "abnormal": {}, "side": self.side}
        out["charId" if self.side else "enemyKey"] = self.definition.key
        if not self.side:
            out["key"] = self.definition.key
        return out
