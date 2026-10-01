"""Runtime loads a small compiled pack, never the extraction mega-bundle."""
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from .dependency import resolve
from .registry import Registry
from .schemas import (AbilityDefinition, LoadedContent, UnitDefinition,
                      UnsupportedContent, freeze, numeric_attributes)

PACK_DIR = Path(__file__).resolve().parents[1] / "levels" / "packs"
PROFILE_DIR = Path(__file__).resolve().parents[1] / "levels" / "profiles"


class ContentRepository:
    def __init__(self, pack_dir=None, registry=None):
        self.pack_dir = Path(pack_dir or PACK_DIR)
        self.registry = registry or Registry()
        self._cache = {}

    def has_level(self, level_id):
        return (self.pack_dir / f"{level_id}.json").is_file()

    def pack(self, level_id):
        if not isinstance(level_id, str) or Path(level_id).name != level_id:
            raise UnsupportedContent("levelId", "invalid level identifier")
        path = self.pack_dir / f"{level_id}.json"
        if not path.is_file():
            raise UnsupportedContent(level_id, "no compiled level pack")
        blob = path.read_bytes()
        data = json.loads(blob)
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        if digest not in self._cache:
            if data.get("schemaVersion") != 1 or data.get("levelId") != level_id:
                raise UnsupportedContent(str(path), "invalid pack schema or identity")
            self._cache[digest] = freeze(data)
        return self._cache[digest], digest

    def profile(self, level_id):
        path = PROFILE_DIR / f"{level_id}.json"
        if not path.is_file():
            raise UnsupportedContent(level_id, "missing support profile")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("levelId") != level_id or data.get("mechanicsVersion") != "core_v1":
            raise UnsupportedContent(level_id, "support profile identity or mechanics mismatch")
        return freeze(data)

    def load(self, level_id, squad):
        pack, digest = self.pack(level_id)
        profile = self.profile(level_id)
        self._validate_level(pack)
        supported = set(self.registry.entries) | {"physical_damage_v1", "route_movement_v1", "standard_waves_v1", "projectile_v1"}
        unknown = set(pack["capabilities"]) - supported
        if unknown:
            raise UnsupportedContent(level_id, f"unmigrated capabilities: {sorted(unknown)}")
        enemies = {}
        roots = list(pack["levelDependencies"])
        for key, data in pack["enemies"].items():
            self.registry.require(data["behavior"], key)
            enemies[key] = self._unit(key, data)
        operators = {}
        for entry in squad:
            if not isinstance(entry, dict) or not entry.get("charId"):
                raise UnsupportedContent("squad", "entries require charId")
            key = entry["charId"]
            if key not in profile["operators"]:
                raise UnsupportedContent(key, "operator absent from support profile")
            if key in operators:
                raise UnsupportedContent(key, "duplicate squad member")
            raw = pack["operators"].get(key)
            if raw is None:
                raise UnsupportedContent(key, "operator not migrated to this pack")
            operators[key] = self._build(key, raw, entry)
            roots.append(key)
        graph = pack["dependencyGraph"]
        available = set(graph) | set(pack["capabilities"])
        dependencies = resolve(graph, roots, available)
        for key, definition in operators.items():
            if definition.skill and definition.skill["handler"] not in dependencies:
                raise UnsupportedContent(key, "skill handler absent from dependency closure")
        handlers = sorted(set(dependencies) & set(self.registry.entries))
        for key in handlers:
            self.registry.require(key, level_id)
        report = freeze({"levelId": level_id, "contentDigest": digest,
                         "dependencies": dependencies, "handlers": handlers,
                         "operators": sorted(operators), "enemies": sorted(enemies),
                         "status": profile["status"], "profile": profile,
                         "source": pack["source"],
                         "calibrationNotes": pack.get("calibrationNotes", ())})
        return LoadedContent(level_id, digest, pack["level"],
                             MappingProxyType(enemies),
                             MappingProxyType(operators), report)

    @staticmethod
    def _validate_level(pack):
        level = pack["level"]
        mm = level["map"]
        if len(mm["tiles"]) != mm["rows"] * mm["cols"]:
            raise UnsupportedContent(pack["levelId"], "map tile count mismatch")
        for wave in level["waves"]:
            for fragment in wave.get("fragments", ()):
                for action in fragment.get("actions", ()):
                    kind = action.get("actionType")
                    kind = kind.get("name") if hasattr(kind, "get") else kind or "SPAWN"
                    if kind not in ("SPAWN", "STORY", "DISPLAY_ENEMY_INFO"):
                        raise UnsupportedContent(pack["levelId"], f"unmigrated wave action {kind}")
                    if action.get("hiddenGroup") or action.get("randomSpawnGroupKey"):
                        raise UnsupportedContent(pack["levelId"], "conditional wave dependency not migrated")
                    if kind != "SPAWN":
                        continue
                    if action.get("key") not in pack["enemies"]:
                        raise UnsupportedContent(pack["levelId"], "spawn references missing enemy")
                    idx = action.get("routeIndex") or 0
                    if not 0 <= idx < len(level["routes"]):
                        raise UnsupportedContent(pack["levelId"], "spawn references missing route")
                    for cp in level["routes"][idx].get("checkpoints", ()):
                        kind = cp.get("type") or 0
                        kind = kind.get("value", 0) if hasattr(kind, "get") else kind
                        if kind not in (0, 1):
                            raise UnsupportedContent(f"route {idx}", f"checkpoint {kind} not migrated")

    @staticmethod
    def _unit(key, data, attrs=None, skill=None):
        ability = AbilityDefinition(**data["ability"])
        return UnitDefinition(key, data["name"], freeze(attrs or data["attributes"]),
                              ability, tuple(tuple(x) for x in data.get("range", ())),
                              data.get("position", 1), data.get("behavior", "ground_melee_v1"),
                              skill, data.get("lifePointReduce", 1))

    def _build(self, key, raw, entry):
        def integer(field, default):
            value = entry.get(field, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise UnsupportedContent(key, f"{field} must be an integer")
            return value
        phase, level = integer("phase", 0), integer("level", 1)
        if phase != 0:
            raise UnsupportedContent(key, "initial profile supports elite 0 only")
        if not 1 <= level <= raw["maxLevel"]:
            raise UnsupportedContent(key, f"level must be 1..{raw['maxLevel']}")
        for field in ("potential", "trust"):
            if integer(field, 0) != 0:
                raise UnsupportedContent(key, f"{field} is not migrated")
        if entry.get("moduleId") or entry.get("module"):
            raise UnsupportedContent(key, "modules are not migrated")
        frames = raw["attributeFrames"]
        lo, hi = frames[0], frames[-1]
        t = (level - lo["level"]) / max(1, hi["level"] - lo["level"])
        attrs = numeric_attributes(lo["data"])
        for stat, end in numeric_attributes(hi["data"]).items():
            attrs[stat] = attrs.get(stat, end) + (end - attrs.get(stat, end)) * t
        # Nightblade's only E0 talent unlocks at level 30.
        for talent in raw.get("talents", ()):
            if level >= talent["level"]:
                attrs[talent["stat"]] = attrs.get(talent["stat"], 0) + talent["value"]
        skill = None
        if raw.get("skillLevels"):
            if integer("skillIndex", 0) != 0:
                raise UnsupportedContent(key, "only skill index 0 is migrated")
            levels = entry.get("skillLevels", [1])
            if not isinstance(levels, (list, tuple)) or len(levels) != 1:
                raise UnsupportedContent(key, "skillLevels requires one level")
            lv = levels[0]
            if isinstance(lv, bool) or not isinstance(lv, int) or not 1 <= lv <= len(raw["skillLevels"]):
                raise UnsupportedContent(key, "unsupported skill level")
            skill = raw["skillLevels"][lv - 1]
        elif "skillIndex" in entry or entry.get("skillLevels"):
            raise UnsupportedContent(key, "operator has no skill")
        return self._unit(key, raw, attrs, skill)
