"""Historical V1 data extraction into a fixed JSON artifact for conversion.

Run from Ark_emulator: python tools/build_level_pack.py
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(output=None):
    from ark_emulator.loader import DataStore, merged_map, merged_routes
    from ark_emulator.map import materialize_tiles
    from ark_emulator.attack_timing import hit_frame_ratio, on_attack_times
    from ark_emulator.consts import translate_game_damage_type
    from ark_emulator.battle import range_offsets_rotated
    store = DataStore()
    lid = "level_main_00-01"
    sim, raw = store.sim_level(lid), store.raw_level(lid)
    mm = merged_map(store, lid)
    tiles = materialize_tiles(mm["rows"], mm["cols"], mm["tiles"], mm["cells"])
    tile_data = [{"tileKey": t.tile_key, "heightType": t.height_type,
                  "buildableType": t.buildable_type, "passableMask": t.passable_mask}
                 for t in tiles]
    notes, enemies, operators, graph = [], {}, {}, {}
    capabilities = ["ground_melee_v1", "stat_buff_v1", "attack_burst_v1",
                    "cost_skill_v1", "healing_skill_v1", "physical_damage_v1",
                    "route_movement_v1", "standard_waves_v1", "projectile_v1"]

    def timing(key):
        if not on_attack_times(key, "Attack"):
            notes.append(f"{key}: OnAttack missing; explicit model hit ratio 0.5")
        return hit_frame_ratio(key, "Attack")

    for ref in sim["enemyDbRefs"]:
        key = ref["id"]
        data = store.build_merged_enemy(key, ref.get("level") or 0,
                                        ref.get("overwrittenData"))["data"]
        comps = store.enemy_prefab_components(key, data["prefabKey"])
        ability = next(c["fields"] for c in comps
                       if c.get("class") == "Ability" and "_damageType" in c["fields"])
        if data.get("skills") or data.get("talentBlackboard"):
            raise ValueError(f"unmigrated enemy dependencies: {key}")
        for field in ("_buffs", "_passiveBuffs", "_activeBuffs"):
            if ability.get(field):
                raise ValueError(f"unmigrated ability {key}.{field}")
        attrs = {k: v for k, v in data["attributes"].items() if v is not None}
        enemies[key] = {"name": data["name"], "attributes": attrs,
                        "behavior": "ground_melee_v1", "lifePointReduce": data.get("lifePointReduce") or 1,
                        "ability": {"key": key + ":attack", "damage_type": translate_game_damage_type(ability["_damageType"]),
                                    "hit_ratio": timing(key)}}
        graph[key] = ["ground_melee_v1", "physical_damage_v1", "route_movement_v1"]
    kinds = {"char_502_nblade": None, "char_124_kroos": "attack_burst_v1",
             "char_123_fang": "cost_skill_v1", "char_120_hibisc": "healing_skill_v1"}
    for key, handler in kinds.items():
        data = store.characters[key]
        phase = data["phases"][0]
        skill_levels = []
        for item in data.get("skills") or []:
            sid = item["skillId"]
            for level in store.character_skills[sid]["levels"][:7]:
                bb = {x["key"]: x.get("value", 0) for x in level.get("blackboard") or []}
                sp = level.get("spData") or {}
                skill_levels.append({"key": sid, "name": level.get("name", sid), "handler": handler, "blackboard": bb,
                                     "spType": sp.get("spType", 0), "spCost": sp.get("spCost", 0),
                                     "initSp": sp.get("initSp", 0), "increment": sp.get("increment", 1),
                                     "automatic": level.get("skillType") == 2,
                                     "duration": level.get("duration", 0)})
        heal = key == "char_120_hibisc"
        ranged = data["position"] == 2 and not heal
        operators[key] = {"name": data["name"], "attributes": {}, "position": data["position"],
                          "rarity": int(data.get("rarity") or 0) + 1, "subProfession": data.get("subProfessionId"),
                          "maxLevel": phase["maxLevel"], "attributeFrames": phase["attributesKeyFrames"],
                          "range": range_offsets_rotated(phase["rangeId"], 1), "skillLevels": skill_levels,
                          "ability": {"key": key + ":attack", "damage_type": 0, "hit_ratio": timing(key),
                                      "healing": heal, "projectile_speed": 10.0 if ranged else 0.0,
                                      "projectile_key": "projectile_arrow" if ranged else ""},
                          "talents": ([{"level": 30, "stat": "respawnTime", "value": -30}]
                                      if key == "char_502_nblade" else [])}
        graph[key] = ([handler] if handler else []) + ["physical_damage_v1"]
        if ranged:
            graph[key].append("projectile_v1")
        if heal:
            graph[key].append("stat_buff_v1")
    graph["healing_skill_v1"] = ["stat_buff_v1"]
    notes.extend(["spawnRandomRange uses independent uniform axes; awaiting real-game calibration",
                  "STORY is observable and controllable by pause commands; tutorial timing awaits game traces",
                  "operator growth, attack animation scaling and projectile speed await external comparison"])
    sources = [Path(store.data_dir) / "stage_sim_bundle.json",
               Path(store.data_dir) / "enemy_database.json",
               Path(store._char_dir) / "characters.json", Path(store._char_dir) / "skills.json",
               Path(store.data_dir) / "skill_prefab_catalog.json",
               ROOT / "ark_emulator/data_enemy_prefab_catalog_current.json",
               ROOT / "ark_emulator/data_enemy_spine_events.json",
               ROOT / "ark_emulator/data_character_spine_events.json",
               ROOT / "ark_emulator/data_range_table.json",
               ROOT / "ark_emulator/data_level_assets_index.json"]
    pack = {"schemaVersion": 1, "levelId": lid,
            "source": {"server": "cn", "gameVersion": "unverified",
                       "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}},
            "calibrationNotes": notes,
            "level": {"options": sim["options"], "map": {"rows": mm["rows"], "cols": mm["cols"], "tiles": tile_data},
                      "routes": merged_routes(store, lid), "waves": raw["waves"], "randomSeed": sim.get("randomSeed") or 0},
            "enemies": enemies, "operators": operators, "capabilities": capabilities,
            "levelDependencies": list(enemies) + ["standard_waves_v1"], "dependencyGraph": graph}
    path = Path(output or ROOT / "ark_emulator" / "levels" / "packs" / f"{lid}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"compiled {lid}: {len(enemies)} enemies, {len(operators)} operators, {path.stat().st_size} bytes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    build(parser.parse_args().output)
