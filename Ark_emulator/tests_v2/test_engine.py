"""Production substrate requirements: no legacy engine or official ID branches."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ark_sim import Compiler, Engine
from ark_sim.tools.replay import replay
from ark_sim.tools.compare import first_difference

ROOT = Path(__file__).resolve().parents[1]


def package():
    return json.loads((ROOT / "docs/v2_examples/custom_guard.json").read_text(encoding="utf-8"))


def actor(simulation, alias):
    return simulation.ctx.entity(alias)


def deployment_package(base_cost=10, commands=None):
    return {"schemaVersion": 2, "entities": [{"id": "unit/custom_sentinel", "kind": "entity", "tags": ["player"],
        "components": {"attributes": {"base": {"atk": 1}}, "resources": {"hp": {"initial": 100, "capacity": 100}},
            "deployable": {"base_cost": base_cost, "cost": 7, "cooldown_seconds": 2, "refund_ratio": 0.5},
            "spatial": {}}}],
        "scenarioDraft": {"id": "scenario/deployment", "kind": "scenario", "ruleset": "ruleset/ark_standard",
            "roster": ["unit/custom_sentinel"], "map": {"rows": 3, "cols": 3},
            "resources": {"dp": {"initial": 100, "capacity": 100}}, "commands": commands or []}}


def test_scenario_command_at_uses_integer_logical_time():
    data = deployment_package(commands=[{"at": 30, "action": "deploy", "entity": "unit/custom_sentinel",
        "row": 1, "col": 1, "alias": "sentinel"}])
    sim = Engine.create(Compiler().compile(data))
    sim.session.advance(30)
    assert len(sim.session.world.entities()) == 1
    sim.session.advance(1)
    assert actor(sim, "sentinel")["components"]["runtime"]["alive"]
    assert [e["time"] for e in sim.session.events if e["type"] == "command.accepted"] == [30]


def test_deployable_base_cost_is_used_and_expensive_deployment_is_rejected():
    sim = Engine.create(Compiler().compile(deployment_package(base_cost=1000)))
    sim.submit({"action": "deploy", "entity": "unit/custom_sentinel", "row": 1, "col": 1})
    sim.session.advance(1)
    assert sim.ctx.resources.current("system/battle", "dp") == 100
    assert len(sim.session.world.entities()) == 1
    assert [e["payload"]["reason"] for e in sim.session.events if e["type"] == "command.rejected"] == ["insufficient_resource"]


def test_withdraw_refund_and_cooldown_work_without_a_redeploy_attribute():
    sim = Engine.create(Compiler().compile(deployment_package()))
    sim.submit({"action": "deploy", "entity": "unit/custom_sentinel", "row": 1, "col": 1, "alias": "sentinel"})
    sim.session.advance(1)
    assert sim.ctx.resources.current("system/battle", "dp") == 90
    sim.submit({"action": "withdraw", "source": "sentinel"})
    sim.session.advance(1)
    assert sim.ctx.resources.current("system/battle", "dp") == 95
    assert not actor(sim, "sentinel")["components"]["runtime"]["alive"]
    assert sim.ctx.state()["deployments"]["unit/custom_sentinel"]["ready_at"] == 61
    sim.submit({"action": "deploy", "entity": "unit/custom_sentinel", "row": 1, "col": 1})
    sim.session.advance(1)
    assert sim.session.events[-1]["type"] == "command.rejected"
    assert sim.session.events[-1]["payload"]["reason"] == "on_cooldown"


def test_resource_effect_amount_alias_changes_declared_resource():
    sim = Engine.create(Compiler().compile(package()))
    ref = sim.session.world.resolve("actor/guard_1")
    before = sim.ctx.resources.current(ref, "energy")
    sim.ctx.effects.execute(ref, [ref], {"op": "modify_resource", "resource": "energy", "amount": -3})
    assert sim.ctx.resources.current(ref, "energy") == before-3


def test_wave_at_and_instance_components_are_used_before_birth():
    data = deployment_package()
    data["scenarioDraft"]["waves"] = [{"at": 5, "definition": "unit/custom_sentinel", "instanceAlias": "arrival",
        "components": {"attributes": {"base": {"atk": 99}}}, "tags": ["custom_enemy"]}]
    sim = Engine.create(Compiler().compile(data))
    sim.session.advance(5)
    assert len(sim.session.world.entities()) == 1
    sim.session.advance(1)
    arrival = actor(sim, "arrival")
    assert arrival["tags"] == ("custom_enemy",)
    assert arrival["components"]["attributes"]["base"]["atk"] == 99
    assert sim.ctx.state()["pending_waves"] == 0


@pytest.mark.parametrize("component", ["buffs", "buff_container"])
def test_initial_buff_is_applied_to_a_new_instance(component):
    data = package()
    data["scenarioDraft"]["commands"] = []
    data["buffs"].append({"id": "buff/on_birth", "kind": "buff", "duration_seconds": 10,
        "stacking": {"mode": "refresh"}, "modifiers": [{"attribute": "atk", "layer": "flat", "value": 12}]})
    unit = data["entities"][0]
    unit["components"].pop("buffs", None)
    unit["components"][component] = {"initial": ["buff/on_birth"]}
    sim = Engine.create(Compiler().compile(data))
    ref = sim.session.world.resolve("actor/guard_1")
    assert sim.ctx.attributes.value(ref, "atk") == unit["components"]["attributes"]["base"]["atk"] + 12


def test_custom_unit_composed_skill_and_independent_numbers():
    sim = Engine.create(Compiler().compile(package()), seed=123)
    sim.advance(30)
    assert sim.snapshot()["state"]["damage_dealt"] == 850
    assert actor(sim, "actor/dummy_1")["components"]["resources"]["hp"]["current"] == 9150
    assert actor(sim, "actor/guard_1")["components"]["resources"]["energy"]["current"] == pytest.approx(8)
    hits = [e for e in sim.session.events if e["type"] == "damage.accepted"]
    assert sorted(e["payload"]["amount"] for e in hits) == [70, 260, 260, 260]


@pytest.mark.parametrize("factor,damage", [(1, 730), (0, 970)])
def test_data_only_formula_replacement_changes_outcome(factor, damage):
    data = package()
    data["rules"][0]["parameters"]["defense_factor"] = factor
    sim = Engine.create(Compiler().compile(data), seed=123)
    sim.advance(30)
    assert sim.snapshot()["state"]["damage_dealt"] == damage


def test_arbitrary_unit_ids_and_multiple_instances():
    data = package()
    for entity in data["entities"]:
        if entity["id"] == "unit/my_guard":
            entity["id"] = "unit/user_created_robot_999"
    data["scenarioDraft"]["initialEntities"][0]["definition"] = "unit/user_created_robot_999"
    data["scenarioDraft"]["initialEntities"].append({"definition": "unit/user_created_robot_999",
        "instanceAlias": "actor/second", "position": {"row": 4, "col": 0}, "facing": "right"})
    sim = Engine.create(Compiler().compile(data))
    assert actor(sim, "actor/guard_1")["id"] != actor(sim, "actor/second")["id"]


def test_changed_resource_formula_and_timing_configuration():
    data = package()
    data["rules"][2]["parameters"]["rate"] = 5
    data["rulesets"] = [{"id": "ruleset/custom_clock", "kind": "ruleset", "extends": "ruleset/ark_standard", "quantum": 0.1}]
    data["scenarioDraft"]["ruleset"] = "ruleset/custom_clock"
    sim = Engine.create(Compiler().compile(data))
    sim.advance(10)
    hits = [e["time"] for e in sim.session.events if e["type"] == "damage.accepted"]
    assert hits[:3] == [2, 4, 6]
    assert actor(sim, "actor/guard_1")["components"]["resources"]["energy"]["current"] == pytest.approx(11)


def test_growth_and_instance_overrides_are_applied_before_birth():
    data = package()
    data["scenarioDraft"]["commands"] = []
    data["rules"].append({"id": "rule/robot_growth", "kind": "calculation_rule", "contract": "attributes.growth",
                         "implementation": {"type": "expression", "expression": "inputs.base + inputs.level * params.step"}, "parameters": {"step": 2}})
    data["entities"][0]["components"]["attributes"]["growth"] = {"atk": {"rule": "rule/robot_growth", "level": 100}}
    data["scenarioDraft"]["initialEntities"][0]["components"] = {"attributes": {"base": {"atk": 999}}}
    sim = Engine.create(Compiler().compile(data))
    assert actor(sim, "actor/guard_1")["components"]["attributes"]["base"]["atk"] == 1199


@pytest.mark.parametrize("scope,step", [("definition", 2), ("instance", 3)])
def test_growth_uses_owner_binding_before_the_entity_is_created(scope, step):
    data = package()
    data["scenarioDraft"]["commands"] = []
    for name, value in (("definition_growth", 2), ("instance_growth", 3)):
        data["rules"].append({"id": f"rule/{name}", "kind": "calculation_rule",
            "contract": "attributes.growth", "parameters": {"step": value},
            "implementation": {"type": "expression", "expression": "inputs.base + inputs.level * params.step"}})
    entity = data["entities"][0]
    entity["components"]["attributes"]["growth"] = {"atk": {"level": 10}}
    entity.setdefault("rules", {})["attributes.growth"] = "rule/definition_growth"
    if scope == "instance":
        data["scenarioDraft"]["initialEntities"][0]["rules"] = {"attributes.growth": "rule/instance_growth"}
    sim = Engine.create(Compiler().compile(data))
    assert actor(sim, "actor/guard_1")["components"]["attributes"]["base"]["atk"] == entity["components"]["attributes"]["base"]["atk"] + 10 * step


def test_user_provider_runtime_identity_and_pure_calculation():
    data = package()
    data["rules"][0]["implementation"] = {"type": "provider", "provider": "user/mitigation"}
    def function(inputs, params, context):
        return inputs["power"] / 2
    providers = {"user/mitigation": {"callable": function, "version": "1"}}
    from ark_sim.domains.providers import BUILTIN_PROVIDERS
    compiled = Compiler(providers={**BUILTIN_PROVIDERS, **providers}).compile(data)
    sim = Engine.create(compiled, seed=123, providers=providers)
    sim.advance(30)
    assert sim.snapshot()["state"]["damage_dealt"] == 520
    assert first_difference(sim.snapshot(), replay(compiled, sim.export_replay(), providers=providers).snapshot()) is None
    with pytest.raises((ValueError, KeyError)):
        Engine.create(compiled)


def test_interactive_submission_and_json_checkpoint_are_exact():
    data = package()
    data["scenarioDraft"]["commands"] = []
    program = Compiler().compile(data)
    sim = Engine.create(program, seed=123)
    sim.advance(3)
    sim.submit({"action": "activate_ability", "source": "actor/guard_1", "ability": "ability/my_burst"}, at=6)
    sim.advance(7)
    saved = json.loads(json.dumps(sim.checkpoint()))
    continued = Engine.restore(program, saved)
    sim.advance(30)
    continued.advance(30)
    assert first_difference(sim.snapshot(), continued.snapshot()) is None
    assert first_difference(sim.snapshot(), replay(program, sim.export_replay()).snapshot()) is None


def test_snapshots_and_program_are_isolated():
    program = Compiler().compile(package())
    sim = Engine.create(program)
    snap = sim.snapshot()
    snap["entities"][1]["components"]["resources"]["hp"]["current"] = -100
    assert actor(sim, "actor/guard_1")["components"]["resources"]["hp"]["current"] == 1200
    with pytest.raises(TypeError):
        program.scenario["id"] = "changed"


def test_whole_ruleset_switch_reuses_identical_content_and_commands():
    data = json.loads((ROOT / "packages/custom/custom_guard.json").read_text(encoding="utf-8"))
    standard = Compiler().compile(data, ruleset="ruleset/ark_standard")
    balanced = Compiler().compile(data, ruleset="ruleset/custom_balance")
    a, b = Engine.create(standard, 123), Engine.create(balanced, 123)
    a.advance(30)
    b.advance(30)
    assert a.snapshot()["state"]["damage_dealt"] == 850
    assert b.snapshot()["state"]["damage_dealt"] == 60
    assert standard.fingerprint != balanced.fingerprint


def test_runtime_has_no_legacy_imports():
    code = "from ark_sim import Compiler,Engine; from pathlib import Path; import sys; p=Compiler().compile(Path('docs/v2_examples/custom_guard.json')); s=Engine.create(p); s.advance(1); assert not any(x=='ark_emulator' or x.startswith('ark_emulator.') for x in sys.modules); print('independent')"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "independent"
