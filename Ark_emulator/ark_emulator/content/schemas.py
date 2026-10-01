"""Immutable definitions. Mutable timers and blackboards live in core."""
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping
import math


class UnsupportedContent(ValueError):
    def __init__(self, path, reason):
        self.path, self.reason = path, reason
        super().__init__(f"unsupported content at {path}: {reason}")


def freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


@dataclass(frozen=True)
class AbilityDefinition:
    key: str
    damage_type: int
    hit_ratio: float
    projectile_speed: float = 0.0
    projectile_key: str = ""
    healing: bool = False

    def __post_init__(self):
        if self.damage_type not in (0, 1, 2):
            raise UnsupportedContent(self.key, "damage type not migrated")
        if not math.isfinite(self.hit_ratio) or not 0 <= self.hit_ratio <= 1:
            raise UnsupportedContent(self.key, "invalid hit ratio")
        if not math.isfinite(self.projectile_speed) or self.projectile_speed < 0:
            raise UnsupportedContent(self.key, "invalid projectile speed")


@dataclass(frozen=True)
class UnitDefinition:
    key: str
    name: str
    attributes: Mapping
    ability: AbilityDefinition
    range_shape: tuple = ()
    position: int = 1
    behavior: str = "ground_melee_v1"
    skill: object = None
    life_point_reduce: int = 1

    def __deepcopy__(self, memo):
        return self


@dataclass(frozen=True)
class LoadedContent:
    level_id: str
    digest: str
    level: Mapping
    enemies: Mapping
    operators: Mapping
    report: Mapping


def numeric_attributes(raw):
    return {key: float(value) for key, value in raw.items()
            if isinstance(value, (int, float))}
