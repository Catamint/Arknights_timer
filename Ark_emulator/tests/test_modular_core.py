"""Independent rules and first-level integration for the migrated backend."""
import copy
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ark_emulator import Simulator
from ark_emulator.content.dependency import resolve
from ark_emulator.content.repository import ContentRepository
from ark_emulator.content.schemas import UnsupportedContent
from ark_emulator.core.attributes import Attributes
from ark_emulator.core.damage import calculate
from ark_emulator.validation.compare import first_difference
from ark_emulator.validation.replay import replay

LEVEL = "level_main_00-01"
SQUAD = [{"charId": "char_502_nblade", "level": 30},
         {"charId": "char_124_kroos", "level": 40}]


def simulator(squad=None):
    return Simulator(level_id=LEVEL, squad=SQUAD if squad is None else squad, seed=123)


def victory():
    sim = simulator()
    assert sim.deploy("char_502_nblade", 3, 3)[0]
    sim.run_ticks(180)
    assert sim.deploy("char_124_kroos", 1, 3, 2)[0]
    sim.run_ticks(10000)
    return sim


def test_runtime_does_not_import_legacy_or_load_mega_bundle():
    code = "from ark_emulator import Simulator; import sys; s=Simulator(level_id='level_main_00-01'); s.run_ticks(10); assert s._store is None; assert 'ark_emulator.battle' not in sys.modules; assert 'ark_emulator.loader' not in sys.modules; assert 'ark_emulator.buff_templates' not in sys.modules; assert 'ark_emulator.operator_skills' not in sys.modules; print(s.backend)"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "modular"


def test_immutable_definitions_and_selected_dependencies():
    repo = ContentRepository()
    content = repo.load(LEVEL, [{"charId": "char_502_nblade"}])
    assert content.enemies["enemy_1002_nsabr"].attributes["def"] == 30
    assert set(content.operators) == {"char_502_nblade"}
    assert content.report["handlers"] == ("ground_melee_v1",)
    with pytest.raises(TypeError):
        content.enemies["enemy_1002_nsabr"].attributes["def"] = 999


@pytest.mark.parametrize("entry", [
    {"charId": "char_002_amiya"}, {"charId": "char_124_kroos", "phase": 1},
    {"charId": "char_124_kroos", "level": 41}, {"charId": "char_502_nblade", "trust": 200},
    {"charId": "char_124_kroos", "skillLevels": [8]},
    {"charId": "char_124_kroos", "level": True},
])
def test_unsupported_build_is_not_silently_mapped(entry):
    with pytest.raises(UnsupportedContent):
        simulator([entry]).snapshot()


def test_missing_pack_and_explicit_legacy_boundary():
    with pytest.raises(UnsupportedContent):
        Simulator(level_id="level_not_migrated", backend="modular").snapshot()
    assert Simulator(level_id="level_main_01-01").backend == "legacy"
    assert Simulator(stage_id="main_00-01", level_id=None).backend == "modular"


def test_dependency_missing_and_cycle_report_the_path():
    with pytest.raises(UnsupportedContent, match="a -> missing"):
        resolve({"a": ["missing"]}, ["a"], {"a"})
    with pytest.raises(UnsupportedContent, match="cycle"):
        resolve({"a": ["b"], "b": ["a"]}, ["a"], {"a", "b"})


def test_unmigrated_wave_and_handler_rejected_before_running(tmp_path):
    repo = ContentRepository()
    raw, _ = repo.pack(LEVEL)
    from ark_emulator.content.schemas import thaw
    pack = thaw(raw)
    pack["level"]["waves"][0]["fragments"][0]["actions"][0]["actionType"] = "SPAWN_UNSUPPORTED"
    path = tmp_path / (LEVEL + ".json")
    path.write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(UnsupportedContent, match="wave action"):
        ContentRepository(tmp_path).load(LEVEL, [])
    pack = thaw(raw)
    pack["capabilities"].append("unimplemented_effect")
    path.write_text(json.dumps(pack), encoding="utf-8")
    with pytest.raises(UnsupportedContent, match="unmigrated capabilities"):
        ContentRepository(tmp_path).load(LEVEL, [])


def test_first_level_without_deployments_and_soldier_wait():
    sim = simulator([])
    snap = sim.run_ticks(10000)
    assert snap["finished"] and snap["result"] == "victory"
    assert snap["waves"]["spawned"] == 11
    assert snap["stats"]["leaks"] == 11 and snap["lifePoint"] == 9
    waits = [e for e in snap["events"] if e["type"] == "checkpoint_wait"]
    assert len(waits) == 1 and waits[0]["data"]["until"] - waits[0]["tick"] == 90
    spawns = [e for e in snap["events"] if e["type"] == "enemy_spawn"]
    assert sum(e["data"]["key"] == "enemy_1007_slime" for e in spawns) == 10
    assert sum(e["data"]["key"] == "enemy_1002_nsabr" for e in spawns) == 1


def test_first_level_full_clear_with_two_operators():
    snap = victory().snapshot()
    assert snap["finished"] and snap["result"] == "victory"
    assert snap["stats"]["kills"] == 11 and snap["stats"]["leaks"] == 0
    assert snap["lifePoint"] == 20 and snap["stats"]["skillCasts"] > 0
    assert any(e["type"] == "projectile_launch" for e in snap["events"])


def test_defeat_boundary_is_separate_from_official_no_deploy_case():
    sim = simulator([])
    sim.battle.life_point = 1
    snap = sim.run_ticks(10000)
    assert snap["result"] == "defeat" and snap["stats"]["leaks"] == 1


@pytest.mark.parametrize("row,col,direction,reason", [
    (-1, 0, 1, "not_buildable"), (0, 0, 1, "not_buildable"),
    (3, 3, 4, "invalid_direction"), (3.0, 3, 1, "invalid_position"),
])
def test_invalid_deploy_has_no_side_effect(row, col, direction, reason):
    sim = simulator()
    before = sim.snapshot()
    assert sim.deploy("char_502_nblade", row, col, direction) == (False, reason)
    after = sim.snapshot()
    assert (before["cost"], before["deployed"], before["stats"], before["events"]) == \
           (after["cost"], after["deployed"], after["stats"], after["events"])


def test_duplicate_occupancy_skill_and_insufficient_cost():
    sim = simulator()
    ok, uid = sim.deploy("char_502_nblade", 3, 3)
    assert ok
    assert sim.deploy("char_502_nblade", 3, 2) == (False, "already_deployed")
    assert sim.deploy("char_124_kroos", 3, 3) == (False, "not_buildable")
    assert sim.deploy("char_124_kroos", 1, 3) == (False, "insufficient_cost")
    assert sim.activate_skill(uid) == (False, "invalid_skill")


def test_pause_and_step_preserve_pause_and_exact_clock():
    sim = simulator()
    sim.pause()
    assert sim.run_ticks(30)["tick"] == 0
    assert sim.step(30)["tick"] == 30 and sim.battle.paused
    sim.resume()
    assert sim.run_ticks(3)["tick"] == 33
    with pytest.raises(ValueError):
        sim.step(-1)


def test_atomic_concurrent_first_use_and_duplicate_deployment():
    sim = simulator()
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda cell: sim.deploy("char_502_nblade", *cell), [(3, 3), (3, 2)]))
    assert sum(r[0] for r in results) == 1
    assert len(sim.battle.operators) == 1 and sim.battle.cost == 3


def test_legal_actions_use_same_ground_and_highland_validator():
    sim = simulator()
    sim.battle.battle_cost_add(99)
    actions = sim.battle.legal_actions(include_directions=True)
    assert actions
    for action in actions:
        clone = copy.deepcopy(sim)
        assert clone.battle.execute(action)[0], action
    for action in actions:
        if action["charId"] == "char_124_kroos":
            assert sim.battle.map.tile(action["row"], action["col"]).buildable_type & 2


def test_withdraw_releases_block_and_sets_redeploy_cooldown():
    sim = simulator()
    ok, uid = sim.deploy("char_502_nblade", 3, 3)
    assert ok
    sim.run_ticks(600)
    assert sim.withdraw(uid)[0]
    assert not sim.battle.operators
    assert all(e.blocked_by is None for e in sim.battle.enemies)
    assert sim.deploy("char_502_nblade", 3, 3) == (False, "on_cooldown")
    assert sim.battle._redeploy_until["char_502_nblade"] == sim.tick + 40 * 30


def test_attributes_and_damage_independent_expected_values():
    attrs = Attributes({"atk": 100, "def": 90, "magicResistance": 0})
    attrs.modifiers[1] = {"stat": "atk", "mul": 0.5}
    attrs.modifiers[2] = {"stat": "atk", "mul": 0.5}
    assert attrs.get("atk") == 200
    attrs.modifiers[3] = {"stat": "atk", "final_mul": 2}
    assert attrs.get("atk") == 400
    assert calculate(100, attrs, 0) == 10
    assert calculate(100, attrs, 1) == 100
    assert calculate(100, attrs, 2) == 100


def test_buff_sources_independent_refresh_and_expiry_timestamps():
    sim = simulator()
    sim.battle.battle_cost_add(99)
    sim.deploy("char_502_nblade", 3, 3)
    sim.deploy("char_124_kroos", 1, 3)
    a, source = sim.battle.operators
    sim.step(2)
    base = a.attributes.get("atk")
    b1 = sim.battle.buffs.apply(a, "atk", {"stat": "atk", "mul": .5}, 3, a)
    b2 = sim.battle.buffs.apply(a, "atk", {"stat": "atk", "mul": .5}, 8, source)
    assert a.attributes.get("atk") == base * 2
    assert b1.uid != b2.uid
    sim.step(4)
    assert a.attributes.get("atk") == base * 1.5
    expired = [e for e in sim.snapshot()["events"] if e["type"] == "buff_expired"]
    assert expired[0]["tick"] == 5


def test_queued_same_tick_order_and_past_tick_rejected():
    sim = simulator()
    first = sim.schedule({"type": "deploy", "charId": "char_502_nblade", "row": 3, "col": 3}, 0)
    second = sim.schedule({"type": "deploy", "charId": "char_502_nblade", "row": 3, "col": 2}, 0)
    sim.step()
    assert sim.battle.command_results[first][0]
    assert sim.battle.command_results[second] == (False, "already_deployed")
    with pytest.raises(ValueError):
        sim.schedule({"type": "resume"}, 0)


def test_replay_exact_snapshot_and_content_mismatch_rejected():
    sim = victory()
    record = sim.export_replay()
    restored = replay(record)
    assert first_difference(sim.snapshot(), restored.snapshot()) is None
    record["contentDigest"] = "different_version"
    with pytest.raises(ValueError, match="digest mismatch"):
        replay(record)


def test_snapshot_and_clone_are_independent_from_live_state():
    sim = simulator()
    sim.deploy("char_502_nblade", 3, 3)
    snap = sim.snapshot()
    snap["deployed"][0]["hp"] = -1
    snap["routes"][0]["startPosition"]["row"] = 999
    assert sim.battle.operators[0].hp > 0
    assert sim.battle.routes[0]["startPosition"]["row"] != 999
    clone = copy.deepcopy(sim)
    clone.step(10)
    clone.battle.operators[0].hp -= 100
    assert sim.tick == 0 and clone.tick == 10
    assert sim.battle.operators[0].hp > clone.battle.operators[0].hp
    assert sim.battle.content is clone.battle.content


def test_cost_and_healing_handlers_load_only_when_selected():
    sim = simulator([{"charId": "char_502_nblade"}, {"charId": "char_123_fang"}, {"charId": "char_120_hibisc"}])
    b = sim.battle
    assert "attack_burst_v1" not in b.content.report["handlers"]
    b.battle_cost_add(99)
    sim.deploy("char_502_nblade", 3, 3)
    sim.deploy("char_123_fang", 3, 1)
    sim.deploy("char_120_hibisc", 4, 3, 0)
    blade, fang, medic = b.operators
    blade.hp = 100
    sim.step(120)
    assert blade.hp > 100
    fang.sp = fang.sp_max
    previous = b.cost
    sim.step()
    assert b.cost == min(b.max_cost, previous + 6)
    medic.sp = medic.sp_max
    atk = medic.attributes.get("atk")
    assert sim.activate_skill(medic.inst_id)[0]
    assert medic.attributes.get("atk") == pytest.approx(atk * 1.1)
    displayed = next(u for u in sim.snapshot()["deployed"] if u["instId"] == medic.inst_id)
    assert displayed["skills"][0]["equipped"] and displayed["activeSkill"]


def test_comparison_tolerances_are_explicit():
    assert first_difference({"x": 1}, {"x": 1.01})["path"] == "$.x"
    assert first_difference({"x": 1}, {"x": 1.01}, {"$.x": .02}) is None
    assert first_difference({"x": True}, {"x": 1}) is not None


def test_checked_in_model_fixture_replays_declared_outcome():
    record = json.loads((Path(__file__).resolve().parents[1] / "ark_emulator/validation/fixtures/level_00_01_model_replay.json").read_text(encoding="utf-8"))
    sim = replay(record)
    snap = sim.snapshot()
    assert snap["stats"]["kills"] == 11 and snap["stats"]["leaks"] == 0
    assert snap["lifePoint"] == 20 and snap["result"] == "victory"
    assert record.get("evidenceKind") == "model_regression"
