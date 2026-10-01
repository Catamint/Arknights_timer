"""Independent source facts and offline conversion boundaries for 0-1."""
import hashlib
import json
import sys
from copy import deepcopy

import pytest

from ark_sim.adapters.imports.ark_level import DEFAULT_PACK, ArkImportError, _waves, import_ark_level
from ark_sim.content import Compiler
from ark_sim.domains.spatial import GridTopology


def test_import_reads_only_json_and_preserves_provenance_and_calibration_status():
    before = {key for key in sys.modules if key == "ark_emulator" or key.startswith("ark_emulator.")}
    package = import_ark_level()
    after = {key for key in sys.modules if key == "ark_emulator" or key.startswith("ark_emulator.")}
    assert after == before
    assert package["schemaVersion"] == 2
    assert package["status"] == "model_unverified"
    assert package["manifest"]["metadata"]["source_hash"] == hashlib.sha256(DEFAULT_PACK.read_bytes()).hexdigest()
    assert package["scenarioDraft"]["metadata"]["pendingCalibration"]
    json.dumps(package, ensure_ascii=False, allow_nan=False)


def test_exact_enemy_attributes_and_fixed_e0_configurations():
    package = import_ark_level()
    entities = {value["id"]: value for value in package["entities"]}
    assert len(entities) == 6
    soldier = entities["unit/enemy_1002_nsabr"]["components"]["attributes"]["base"]
    assert {key: soldier[key] for key in ("max_hp", "atk", "def", "move_speed", "attack_interval")} == {
        "max_hp": 1650, "atk": 200, "def": 30, "move_speed": 1.1, "attack_interval": 2.0}
    night = entities["unit/char_502_nblade"]
    assert night["metadata"]["elite"] == 0 and night["metadata"]["level"] == 30
    assert night["components"]["attributes"]["base"]["atk"] == 232
    assert night["components"]["attributes"]["base"]["redeploy_time"] == 40
    assert night["components"]["resources"]["hp"]["role"] == "health"
    kroos = entities["unit/char_124_kroos"]
    assert kroos["metadata"]["level"] == 40
    assert kroos["components"]["attributes"]["base"]["atk"] == 258
    assert kroos["components"]["attributes"]["base"]["attack_speed_ratio"] == 1
    assert kroos["components"]["deployable"]["terrain"] == "high"
    assert kroos["components"]["resources"]["sp"]["capacity"] == 5
    assert kroos["components"]["resources"]["sp"]["recovery_rate"] == 0


def test_independent_wave_timeline_count_routes_and_three_second_wait():
    scene = import_ark_level()["scenarioDraft"]
    assert scene["map"]["rows"] == 6 and scene["map"]["cols"] == 9
    assert len(scene["routes"]) == 10
    assert [value["at_seconds"] for value in scene["waves"]] == [5, 16, 18, 29, 29.7, 34, 45, 45.7, 52, 52.7, 63.7]
    assert sum(value["definition"] == "unit/enemy_1007_slime" for value in scene["waves"]) == 10
    soldier = scene["waves"][-1]
    assert soldier["definition"] == "unit/enemy_1002_nsabr"
    assert soldier["position"] == {"row": 3, "col": 8}
    wait = soldier["route"]["checkpoints"][1]
    assert wait["type"]["name"] == "WAIT_FOR_SECONDS" and wait["time"] == 3
    assert scene["resources"]["dp"]["initial"] == 10
    assert scene["resources"]["dp"]["recovery"] == {"mode": "periodic", "interval_seconds": 1}
    assert scene["resources"]["life"]["initial"] == 20


def test_fragment_cursor_parallel_actions_repeat_interval_and_wave_post_delay():
    route = {"startPosition": {"row": 0, "col": 2}, "endPosition": {"row": 0, "col": 0}}
    action = {"actionType": {"name": "SPAWN"}, "key": "enemy", "routeIndex": 0, "count": 2,
              "preDelay": 3, "interval": 2}
    level = {"routes": [route], "waves": [{"preDelay": 1, "postDelay": 2, "fragments": [
        {"preDelay": 2, "actions": [action, {**action, "count": 1, "preDelay": 1}]},
        {"preDelay": 4, "actions": [{**action, "count": 1, "preDelay": 0}]},
    ]}, {"preDelay": 1, "fragments": [{"actions": [{**action, "count": 1, "preDelay": 0}]}]}]}
    waves, controls = _waves(level)
    assert controls == []
    assert [wave["at_seconds"] for wave in waves] == [4, 6, 8, 12, 15]
    waves[0]["route"]["startPosition"]["col"] = 99
    assert route["startPosition"]["col"] == 2


def test_all_ground_routes_are_reachable_with_waits_treated_as_control_points():
    scene = import_ark_level()["scenarioDraft"]
    grid = GridTopology(scene["map"])
    for wave in scene["waves"]:
        route = wave["route"]
        origin = route["startPosition"]
        checkpoints = [value["position"] for value in route.get("checkpoints", [])
                       if value.get("type") is None]
        for destination in checkpoints + [route["endPosition"]]:
            path = grid.path(origin, destination)
            assert path and path[-1] == destination
            assert all(grid.passable(value["row"], value["col"]) for value in path)
            origin = destination


def test_compiled_0_1_contains_all_four_roster_units_and_generic_skill_dependencies():
    program = Compiler().compile(import_ark_level())
    for identifier in ("char_502_nblade", "char_124_kroos", "char_123_fang", "char_120_hibisc"):
        assert f"unit/{identifier}" in program.definitions
    assert "buff/char_120_hibisc_skill_1" in program.definitions
    assert "ability/char_124_kroos_skill_1" in program.definitions
    assert program.ruleset["bindings"]["movement.speed"] == "rule/ark_00_01_movement_speed"
    assert program.definitions["selector/char_502_nblade_attack"]["parameters"]["include_blocked"] is True
    assert program.definitions["selector/char_124_kroos_attack"]["parameters"]["include_blocked"] is False


def test_source_fixture_is_not_mutated_and_unsupported_level_is_rejected(tmp_path):
    source = json.loads(DEFAULT_PACK.read_text(encoding="utf-8"))
    original = deepcopy(source)
    _waves(source["level"])
    assert source == original
    source["levelId"] = "level_main_01-01"
    path = tmp_path / "other_level.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ArkImportError, match="explicit level_main_00-01"):
        import_ark_level(path)
