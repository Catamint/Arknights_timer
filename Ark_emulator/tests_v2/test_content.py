"""Compiler/authoring tests; production provider behavior has separate tests."""
import json
from copy import deepcopy
from pathlib import Path
import pytest

from ark_sim.content import Compiler, CompileError
from ark_sim.content.compiler import PRESET_PATH, _provider_names
from ark_sim.tools.authoring import PackageBuilder, EntityBuilder, AbilityBuilder, preview_rule
from ark_sim.tools.inspect import explain_dependencies
from ark_sim.contracts.models import freeze


ROOT = Path(__file__).resolve().parents[1]


def compile_fixture_provider(inputs, parameters, context):
    """Only verifies callable registration; never executes policy behavior here."""
    raise AssertionError("Compiler fixture providers must not run")


def registered_providers():
    package = json.loads(PRESET_PATH.read_text(encoding="utf-8"))
    definitions = [definition for values in package.values() if isinstance(values, list)
                   for definition in values if isinstance(definition, dict) and "kind" in definition]
    names = set().union(*(_provider_names(definition) for definition in definitions))
    names.add("ark.selector.grid")
    return {name: {"callable": compile_fixture_provider, "version": "compiler-fixture-1"} for name in names}


def minimum_package():
    available = json.loads(PRESET_PATH.read_text(encoding="utf-8"))["rulesets"][0]["bindings"]
    calculations = ("attributes.effective", "attributes.modifier_layer", "resource.capacity", "resource.bounds",
                    "buff.duration", "buff.interval", "buff.stack_amount", "time.quantize")
    return {"manifest": {"id": "package/test", "version": "1"}, "definitions": [
        {"id": "ruleset/test", "kind": "ruleset", "bindings": {key: available[key] for key in calculations}, "quantum": 0.1},
        {"id": "unit/anything_can_be_created", "kind": "entity", "components": {
            "attributes": {"base": {"power": 10}}, "resources": {"battery": {"initial": 10, "capacity": 15}}, "abilities": []}}],
        "scenarioDraft": {"id": "scenario/test", "ruleset": "ruleset/test", "initialEntities": [
            {"definition": "unit/anything_can_be_created", "instanceAlias": "actor/test"}]}}


def test_arbitrary_id_without_official_whitelist():
    program = Compiler().compile(minimum_package())
    assert "unit/anything_can_be_created" in program.definitions
    assert "rule/ark_standard_mitigation" not in program.definitions
    assert "unit/anything_can_be_created" in program.dependency_ids


def test_program_is_recursively_immutable_and_input_independent():
    package = minimum_package()
    program = Compiler().compile(package)
    package["definitions"][1]["components"]["attributes"]["base"]["power"] = 500
    assert program.definitions["unit/anything_can_be_created"]["components"]["attributes"]["base"]["power"] == 10
    with pytest.raises(TypeError):
        program.scenario["id"] = "other"
    with pytest.raises(TypeError):
        program.definitions["unit/anything_can_be_created"]["components"]["attributes"]["base"]["power"] = 2


def test_json_path_directory_and_dict_have_identical_fingerprint(tmp_path):
    package = minimum_package()
    path = tmp_path / "content.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    compiler = Compiler()
    programs = [compiler.compile(package), compiler.compile(path), compiler.compile("scenario/test", packages=tmp_path)]
    assert len({program.fingerprint for program in programs}) == 1


def test_unused_package_is_excluded_from_dependency_and_identity():
    package = minimum_package()
    other = {"manifest": {"id": "package/unrelated", "version": "99"}, "definitions": [
        {"id": "unit/unrelated", "kind": "entity", "components": {"abilities": ["ability/unresolved"]}}]}
    one = Compiler().compile(package)
    two = Compiler().compile("scenario/test", packages=[package, other])
    assert one.fingerprint == two.fingerprint
    assert "unit/unrelated" not in two.definitions


@pytest.mark.parametrize("field", ["componnets", "attack_interval"])
def test_unknown_entity_field_is_rejected(field):
    package = minimum_package()
    package["definitions"][1][field] = {}
    with pytest.raises(CompileError, match="unknown fields"):
        Compiler().compile(package)


def test_unknown_component_and_resource_field_are_rejected():
    package = minimum_package()
    package["definitions"][1]["components"]["official_stats"] = {}
    with pytest.raises(CompileError, match="unsupported component"):
        Compiler().compile(package)
    package = minimum_package()
    package["definitions"][1]["components"]["resources"]["battery"]["intial"] = 1
    with pytest.raises(CompileError, match="unknown fields"):
        Compiler().compile(package)


def test_missing_reference_includes_chain():
    package = minimum_package()
    package["definitions"][1]["components"]["abilities"] = ["ability/missing"]
    with pytest.raises(CompileError, match="scenario/test.*unit/anything.*ability/missing"):
        Compiler().compile(package)


def test_duplicate_definition_never_last_import_wins():
    package = minimum_package()
    package["definitions"].append(deepcopy(package["definitions"][1]))
    with pytest.raises(CompileError, match="Conflicting definition"):
        Compiler().compile(package)


def test_inheritance_merges_maps_and_replaces_arrays():
    package = minimum_package()
    package["definitions"].append({"id": "unit/child", "kind": "entity", "extends": "unit/anything_can_be_created",
                                   "tags": ["custom"], "components": {"attributes": {"base": {"speed": 3}}}})
    package["scenarioDraft"]["initialEntities"][0]["definition"] = "unit/child"
    program = Compiler().compile(package)
    child = program.definitions["unit/child"]
    assert dict(child["components"]["attributes"]["base"]) == {"power": 10, "speed": 3}
    assert child["tags"] == ("custom",)


def test_inheritance_cycle_rejected_with_ids():
    package = minimum_package()
    package["definitions"][1]["extends"] = "unit/child"
    package["definitions"].append({"id": "unit/child", "kind": "entity", "extends": "unit/anything_can_be_created"})
    with pytest.raises(CompileError, match="Inheritance cycle.*unit/"):
        Compiler().compile(package)


def test_dependency_cycle_rejected():
    package = minimum_package()
    package["definitions"][1]["dependencies"] = ["unit/child"]
    package["definitions"].append({"id": "unit/child", "kind": "entity", "components": {},
                                   "dependencies": ["unit/anything_can_be_created"]})
    with pytest.raises(CompileError, match="Dependency cycle"):
        Compiler().compile(package)


def test_dynamic_references_require_finite_allowlist():
    package = minimum_package()
    package["scenarioDraft"]["initialEntities"][0]["definition"] = {"dynamic": "choose_by_seed"}
    with pytest.raises(CompileError, match="dynamic reference requires"):
        Compiler().compile(package)
    package["scenarioDraft"]["initialEntities"][0]["definition"]["allowed"] = ["unit/anything_can_be_created"]
    with pytest.raises(CompileError, match="dynamic reference execution is unsupported"):
        Compiler().compile(package)
    package["scenarioDraft"]["initialEntities"][0]["definition"] = "unit/anything_can_be_created"
    package["scenarioDraft"]["dynamicReferences"] = [{"dynamic": "choose_by_seed", "allowed": ["unit/anything_can_be_created"]}]
    assert "unit/anything_can_be_created" in Compiler().compile(package).dependency_ids


def test_rule_binding_wrong_contract_and_same_scope_conflict():
    package = minimum_package()
    package["definitions"].append({"id": "rule/custom", "kind": "calculation_rule", "contract": "damage.base",
                                   "implementation": {"type": "expression", "expression": "inputs.attack"}})
    with pytest.raises(CompileError, match="belongs to damage.base"):
        Compiler().compile(package, overrides={"damage.mitigation": "rule/custom"})
    with pytest.raises(CompileError, match="Conflicting overrides"):
        Compiler().compile(package, overrides=[{"damage.base": "rule/custom"}, {"damage.base": "rule/custom"}])


def test_unimplemented_provider_is_a_compile_error():
    package = minimum_package()
    package["definitions"][1]["components"]["behavior"] = {"machine": "behavior/new"}
    package["definitions"].append({"id": "behavior/new", "kind": "behavior", "provider": "missing.algorithm"})
    with pytest.raises(CompileError, match="not implemented or registered"):
        Compiler().compile(package)


def test_noncallable_provider_is_not_a_capability():
    package = minimum_package()
    package["definitions"][1]["components"]["behavior"] = {"machine": "behavior/new"}
    package["definitions"].append({"id": "behavior/new", "kind": "behavior", "provider": "missing.algorithm"})
    with pytest.raises(CompileError, match="no callable"):
        from ark_sim.domains.providers import BUILTIN_PROVIDERS
        Compiler(providers={**BUILTIN_PROVIDERS, "missing.algorithm": {"version": "declared-only"}}).compile(package)


def test_custom_guard_sample_compiles_with_all_externals_and_previews():
    package = json.loads((ROOT / "docs/v2_examples/custom_guard.json").read_text(encoding="utf-8"))
    registry = registered_providers()
    program = Compiler(providers=registry).compile(package)
    assert "ability/my_burst" in program.dependency_ids
    assert "buff/my_attack_boost" in program.dependency_ids
    for case in package["formulaPreviewCases"]:
        result = preview_rule(program, case["rule"], case["inputs"], providers=registry)
        assert result.value == pytest.approx(case["expected"])


def test_unsupported_effect_cannot_fake_compile():
    package = minimum_package()
    package["definitions"][1]["components"]["abilities"] = ["ability/custom"]
    package["definitions"].append({"id": "ability/custom", "kind": "ability", "activation": {"mode": "manual"},
                                   "timeline": [{"effect": {"op": "win_by_magic"}}]})
    with pytest.raises(CompileError, match="unsupported effect op"):
        Compiler().compile(package)


def test_passive_without_event_is_explicitly_rejected():
    package = minimum_package()
    package["definitions"][1]["components"]["abilities"] = ["ability/custom"]
    package["definitions"].append({"id": "ability/custom", "kind": "ability", "activation": {"mode": "passive"}, "timeline": []})
    with pytest.raises(CompileError, match="requires an explicit event"):
        Compiler().compile(package)


def test_builder_and_json_compile_same_semantics():
    entity = EntityBuilder("unit/anything_can_be_created").attributes(power=10).resource("battery", initial=10, capacity=15).abilities()
    builder = PackageBuilder("package/test", version="1")
    builder.add(minimum_package()["definitions"][0])
    builder.add(entity).scenario("scenario/test", ruleset="ruleset/test", initialEntities=[{"definition": entity.build()["id"], "instanceAlias": "actor/test"}])
    program = builder.compile()
    serialized = json.loads(json.dumps(builder.build()))
    assert Compiler().compile(serialized).fingerprint == program.fingerprint
    assert explain_dependencies(program)["count"] == len(Compiler().compile(minimum_package()).dependency_ids)


def test_formula_and_rule_overrides_change_program_identity():
    package = minimum_package()
    package["definitions"].append({"id": "rule/custom", "kind": "calculation_rule", "contract": "damage.base",
                                   "implementation": {"type": "expression", "expression": "inputs.attack * 2"}})
    first = Compiler().compile(package, overrides={"damage.base": "rule/custom"})
    package["definitions"][-1]["implementation"]["expression"] = "inputs.attack * 3"
    second = Compiler().compile(package, overrides={"damage.base": "rule/custom"})
    assert first.fingerprint != second.fingerprint


@pytest.mark.parametrize("expression", ["inputs.attak", "params.undefined"])
def test_formula_input_or_parameter_typo_fails_before_runtime(expression):
    package = minimum_package()
    package["definitions"].append({"id": "rule/custom", "kind": "calculation_rule", "contract": "damage.base",
                                   "implementation": {"type": "expression", "expression": expression}})
    with pytest.raises(CompileError, match="undeclared"):
        Compiler().compile(package, overrides={"damage.base": "rule/custom"})


def test_growth_is_unrestricted_by_official_progression():
    package = minimum_package()
    package["definitions"].append({"id": "rule/user_growth", "kind": "calculation_rule", "contract": "attributes.growth",
                                   "implementation": {"type": "expression", "expression": "inputs.base + inputs.level * params.rate"}, "parameters": {"rate": 2}})
    package["definitions"][1]["components"]["attributes"]["growth"] = {"power": {"rule": "rule/user_growth", "level": 500, "parameters": {}}}
    program = Compiler().compile(package)
    assert "rule/user_growth" in program.dependency_ids
    result = preview_rule(program, "rule/user_growth", {"base": 10, "level": 500, "growth_parameters": {}})
    assert result.value == 1010


def test_not_implemented_progression_content_is_rejected():
    package = minimum_package()
    package["definitions"][1]["equipment"] = [{"weapon": "custom"}]
    with pytest.raises(CompileError, match="execution is unsupported"):
        Compiler().compile(package)


def test_source_cost_resource_is_verified():
    package = minimum_package()
    package["definitions"][1]["components"]["abilities"] = ["ability/skill"]
    package["definitions"].append({"id": "ability/skill", "kind": "ability",
                                   "activation": {"mode": "manual", "costs": [{"resource": "typo_energy", "amount": 1}]},
                                   "timeline": [{"effect": {"op": "emit", "event": "custom"}}]})
    with pytest.raises(CompileError, match="undefined resource typo_energy"):
        Compiler().compile(package)


def test_buff_events_and_state_graph_content_have_strict_effect_checks():
    package = minimum_package()
    package["definitions"][1]["components"]["behavior"] = {"machine": "behavior/graph"}
    package["definitions"].append({"id": "behavior/graph", "kind": "behavior", "initial_state": "idle",
                                   "states": {"idle": {"on_enter": []}, "done": {"on_exit": []}},
                                   "transitions": [{"from": "idle", "to": "done", "condition": "inputs.resources.battery > 5", "priority": 1,
                                                    "effects": [{"op": "apply_buff", "target": "source", "buff": "buff/reactive"}]}]})
    package["definitions"].append({"id": "buff/reactive", "kind": "buff", "duration_seconds": 1,
                                   "events": [{"event": "damage.accepted", "condition": "inputs.event.amount > 0",
                                               "effects": [{"op": "modify_resource", "target": "source", "resource": "battery", "delta": 1}]}]})
    program = Compiler().compile(package)
    assert "buff/reactive" in program.dependency_ids
    package["definitions"][-2]["states"]["idle"]["subgraph"] = "unsupported"
    with pytest.raises(CompileError, match="unknown fields"):
        Compiler().compile(package)


def test_wrong_growth_contract_is_rejected():
    package = minimum_package()
    package["definitions"].append({"id": "rule/incorrect_growth", "kind": "calculation_rule", "contract": "damage.base",
                                   "implementation": {"type": "expression", "expression": "inputs.attack"}})
    package["definitions"][1]["growth"] = {"power": {"rule": "rule/incorrect_growth", "level": 2}}
    with pytest.raises(CompileError, match="not an attributes.growth rule"):
        Compiler().compile(package)


def test_metadata_and_parameters_do_not_create_accidental_references():
    package = minimum_package()
    package["definitions"][1]["metadata"] = {"definition": "descriptive text, not a reference"}
    package["definitions"][1]["components"]["spatial"] = {"parameters": {"rule": "provider-specific data"}}
    program = Compiler().compile(package)
    assert len(program.dependency_ids) == len(Compiler().compile(minimum_package()).dependency_ids)


def test_graph_dependencies_include_only_rule_references_not_input_names():
    package = minimum_package()
    package["definitions"].extend([
        {"id": "rule/raw", "kind": "calculation_rule", "contract": "damage.base",
         "implementation": {"type": "expression", "expression": "inputs.attack * inputs.scale + inputs.additions"}},
        {"id": "rule/graph", "kind": "calculation_rule", "contract": "damage.base",
         "implementation": {"type": "graph", "nodes": [{"id": "power", "rule": "rule/raw", "inputs": {
             "attack": "inputs.attack", "scale": "inputs.scale", "additions": "inputs.additions"}}], "output": "nodes.power"}}])
    program = Compiler().compile(package, overrides={"damage.base": "rule/graph"})
    assert {"rule/raw", "rule/graph"} <= set(program.dependency_ids)
    assert preview_rule(program, "rule/graph", {"attack": 10, "scale": 2, "additions": 5}).value == 25


def test_frozen_author_input_is_accepted_without_shared_mutability():
    frozen = freeze(minimum_package())
    assert Compiler().compile(frozen).fingerprint == Compiler().compile(minimum_package()).fingerprint


def test_explicit_draft_with_its_package_is_valid_and_matches_id_selection():
    package = minimum_package()
    direct = Compiler().compile(package["scenarioDraft"], packages=package)
    by_id = Compiler().compile("scenario/test", packages=package)
    assert direct.fingerprint == by_id.fingerprint


def test_attribute_local_rules_enter_closure_and_must_match_contract():
    package = minimum_package()
    package["definitions"].append({"id": "rule/custom_attribute", "kind": "calculation_rule", "contract": "attributes.effective",
                                   "implementation": {"type": "graph", "nodes": [{"id": "value", "expression": "inputs.base * 2"}], "output": "nodes.value"}})
    attrs = package["definitions"][1]["components"]["attributes"]
    attrs["attribute_rules"] = {"power": {"attributes.effective": "rule/custom_attribute"}}
    program = Compiler().compile(package)
    assert "rule/custom_attribute" in program.dependency_ids
    from ark_sim.tools.inspect import explain_bindings
    assert any(scope["path"].endswith("attribute_rules.power") for scope in explain_bindings(program)["scopes"])
    attrs["attribute_rules"]["power"] = {"damage.base": "rule/custom_attribute"}
    with pytest.raises(CompileError, match="belongs to attributes.effective"):
        Compiler().compile(package)


def test_dynamic_calculation_graph_requires_and_loads_default_stage_rules():
    package = minimum_package()
    package["definitions"].extend([
        {"id": "rule/raw", "kind": "calculation_rule", "contract": "damage.base", "implementation": {
            "type": "expression", "expression": "inputs.attack * inputs.scale + inputs.additions"}},
        {"id": "rule/pipeline", "kind": "calculation_rule", "contract": "damage.pipeline", "implementation": {
            "type": "graph", "nodes": [{"id": "power", "calculation": "damage.base", "inputs": {
                "attack": "inputs.effect.attack", "scale": "inputs.effect.scale", "additions": "inputs.effect.additions"}},
                {"id": "settle", "expression": "{'accepted': True, 'amount': nodes.power, 'allocations': [], 'events': []}"}],
            "output": "nodes.settle"}}])
    package["definitions"][0]["bindings"]["damage.pipeline"] = "rule/pipeline"
    with pytest.raises(CompileError, match="missing default rule binding for damage.base"):
        Compiler().compile(package)
    package["definitions"][0]["bindings"]["damage.base"] = "rule/raw"
    program = Compiler().compile(package)
    assert "rule/raw" in program.metadata["dependency_edges"]["rule/pipeline"]
    result = preview_rule(program, "rule/pipeline", {"source": {}, "target": {}, "effect": {
        "attack": 10, "scale": 2, "additions": 5}, "samples": [], "states": {}})
    assert result.value["amount"] == 25


def test_production_sample_versions_and_nested_aggregator_are_locked():
    program = Compiler().compile(ROOT / "docs/v2_examples/custom_guard.json")
    assert program.metadata["providers"]["ark.attributes.aggregate"]["version"] == "ark-preset/1"
    assert program.metadata["providers"]["ark.attributes.layers"]["version"] == "ark-preset/1"
    assert program.ruleset["parameters"]["health_resource"] == "hp"


def test_cli_validate_and_preview_are_real_authoring_calls(capsys):
    from ark_sim.tools.cli import main
    source = str(ROOT / "docs/v2_examples/custom_guard.json")
    assert main(["validate", source]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["preview", source, "--rule", "rule/my_physical", "--inputs",
                 '{"power":100,"defense":80,"resistance":0,"damage_type":"physical"}']) == 0
    assert json.loads(capsys.readouterr().out)["value"] == 60


def test_cli_explain_can_load_ark_importer_and_write_json(tmp_path):
    from ark_sim.tools.cli import main
    output = tmp_path / "dependency.json"
    assert main(["explain", "--scenario", "ark-00-01", "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["dependencies"]["scenario"] == "scenario/ark_00_01"
    assert any(definition["id"] == "unit/char_123_fang" for definition in result["dependencies"]["definitions"])


def test_cli_validation_errors_use_nonzero_exit(capsys):
    from ark_sim.tools.cli import main
    assert main(["validate"]) == 2
    assert "Supply a content" in capsys.readouterr().err


def test_cli_run_uses_custom_time_and_real_replay(tmp_path):
    from ark_sim.tools.cli import main
    from ark_sim.tools.replay import replay
    from ark_sim.tools.compare import first_difference
    package = {"rulesets": [{"id": "ruleset/half_seconds", "kind": "ruleset", "extends": "ruleset/ark_standard", "quantum": 0.5}],
               "scenarioDraft": {"id": "scenario/cli_resource", "ruleset": "ruleset/half_seconds", "resources": {
                   "energy": {"initial": 1, "capacity": 20, "recovery_rate": 2}}}}
    source, output, record = (tmp_path / name for name in ("content.json", "snapshot.json", "replay.json"))
    source.write_text(json.dumps(package), encoding="utf-8")
    assert main(["run", str(source), "--seconds", "1", "--output", str(output), "--replay-output", str(record)]) == 0
    snapshot = json.loads(output.read_text(encoding="utf-8"))
    assert snapshot["time"] == 2
    assert snapshot["entities"][0]["components"]["resources"]["energy"]["current"] == 3
    replayed = replay(Compiler().compile(source), json.loads(record.read_text(encoding="utf-8")))
    assert first_difference(snapshot, replayed.snapshot()) is None


@pytest.mark.parametrize("calculation", ["attributes.effective", "resource.capacity", "resource.bounds"])
def test_missing_required_calculation_is_a_compile_error(calculation):
    package = minimum_package()
    del package["definitions"][0]["bindings"][calculation]
    with pytest.raises(CompileError, match=f"required calculation {calculation}"):
        Compiler().compile(package)


def test_empty_world_does_not_require_unrelated_game_calculations():
    from ark_sim.adapters.api import Engine
    package = {"rulesets": [{"id": "ruleset/empty", "kind": "ruleset", "bindings": {}, "quantum": 1}],
               "scenarioDraft": {"id": "scenario/empty", "ruleset": "ruleset/empty"}}
    program = Compiler().compile(package)
    assert not program.rules
    assert not program.metadata["required_calculations"]
    simulation = Engine.create(program)
    simulation.advance(2)
    assert simulation.snapshot()["time"] == 2


def test_explicit_resource_and_attribute_scopes_can_replace_missing_defaults():
    from ark_sim.adapters.api import Engine
    package = minimum_package()
    bindings = package["definitions"][0]["bindings"]
    attributes = package["definitions"][1]["components"]["attributes"]
    attributes["attribute_rules"] = {"power": {"attributes.effective": bindings.pop("attributes.effective")}}
    spec = package["definitions"][1]["components"]["resources"]["battery"]
    spec["capacity_rule"] = bindings.pop("resource.capacity")
    spec["bounds_rule"] = bindings.pop("resource.bounds")
    program = Compiler().compile(package)
    simulation = Engine.create(program)
    assert simulation.ctx.resources.capacity("actor/test", "battery") == 15
    assert simulation.ctx.attributes.value("actor/test", "power") == 10


def test_custom_typed_catalog_is_serialized_previewed_and_used_by_engine():
    from ark_sim.rules.catalog import load_catalog
    from ark_sim.contracts import thaw
    from ark_sim.adapters.api import Engine
    catalog = thaw(load_catalog())
    catalog["contracts"]["custom.double"] = {"id": "custom.double", "kind": "calculation", "owner": "owner",
        "inputs": [{"name": "value", "type": "number", "required": True}], "outputType": "number",
        "implementations": ["expression"], "outputSchema": {"type": "number"}}
    package = {"rules": [{"id": "rule/custom_double", "kind": "calculation_rule", "contract": "custom.double",
                          "implementation": {"type": "expression", "expression": "inputs.value * 2"}}],
               "rulesets": [{"id": "ruleset/custom", "kind": "ruleset", "quantum": 1, "bindings": {"custom.double": "rule/custom_double"}}],
               "scenarioDraft": {"id": "scenario/custom", "ruleset": "ruleset/custom"}}
    program = Compiler(catalog=catalog).compile(package)
    serialized = json.loads(json.dumps(program.to_dict()))
    assert serialized["metadata"]["catalog"]["contracts"]["custom.double"]["outputType"] == "number"
    assert preview_rule(program, "rule/custom_double", {"value": 7}).value == 14
    assert Engine.create(program).ctx.calc("custom.double", {"value": 7}) == 14


@pytest.mark.parametrize("field", ["rule", "operation"])
def test_unimplemented_per_modifier_fields_are_rejected(field):
    package = minimum_package()
    package["definitions"][1]["components"]["attributes"]["modifiers"] = [{
        "attribute": "power", "layer": "flat", "value": 2, field: "unused_configuration"}]
    with pytest.raises(CompileError, match="per-modifier execution is unsupported"):
        Compiler().compile(package)


def test_ability_scope_can_supply_all_needed_contracts_and_whole_damage_pipeline():
    from ark_sim.adapters.api import Engine
    package = minimum_package()
    available = json.loads(PRESET_PATH.read_text(encoding="utf-8"))["rulesets"][0]["bindings"]
    required = ("ability.windup", "ability.repeat", "ability.duration", "ability.recovery", "resource.cost")
    package["definitions"][1]["components"]["abilities"] = ["ability/fixed"]
    package["definitions"].extend([
        {"id": "rule/fixed", "kind": "calculation_rule", "contract": "damage.pipeline", "implementation": {
            "type": "graph", "nodes": [{"id": "fixed", "expression": "{'accepted': True, 'amount': 5, 'allocations': [], 'events': []}"}], "output": "nodes.fixed"}},
        {"id": "ability/fixed", "kind": "ability", "activation": {"mode": "manual", "costs": [{"resource": "battery", "amount": 1}]},
         "rules": {calculation: available[calculation] for calculation in required},
         "timeline": [{"effect": {"op": "damage", "target": "source", "resource": "battery", "rules": {"damage.pipeline": "rule/fixed"}}}]}])
    package["scenarioDraft"]["commands"] = [{"action": "activate_ability", "at_seconds": 0, "source": "actor/test", "ability": "ability/fixed"}]
    program = Compiler().compile(package)
    assert "rule/ark_basic_power" not in program.rules
    assert "rule/ark_standard_mitigation" not in program.rules
    simulation = Engine.create(program)
    simulation.advance(1)
    assert simulation.ctx.resources.current("actor/test", "battery") == 4


@pytest.mark.parametrize("driver", [
    {"mode": "unimplemented"}, {"mode": "periodic"},
    {"mode": "periodic", "interval_seconds": 0}, {"mode": "periodic", "interval_seconds": -1},
    {"mode": "periodic", "interval_seconds": float("inf")},
    {"mode": "periodic", "interval_seconds": 1, "amout": 2},
    {"mode": "continuous", "interval_seconds": 1},
])
def test_recovery_driver_schema_rejects_unsupported_or_invalid_config(driver):
    package = minimum_package()
    package["definitions"][1]["components"]["resources"]["battery"]["recovery"] = driver
    with pytest.raises(CompileError, match="recovery"):
        Compiler().compile(package)


def test_recovery_driver_presence_requires_its_formula_even_without_rate():
    package = minimum_package()
    spec = package["definitions"][1]["components"]["resources"]["battery"]
    spec["recovery"] = {"mode": "periodic", "interval_seconds": 1}
    with pytest.raises(CompileError, match="required calculation resource.recovery"):
        Compiler().compile(package)
    spec["recovery_rule"] = "rule/ark_resource_recovery"
    spec["parameters"] = {"rate": 2}
    assert any(item["calculation"] == "resource.recovery" for item in Compiler().compile(package).metadata["required_calculations"])


def test_periodic_recovery_is_declared_content_and_executes_exact_intervals():
    from ark_sim.adapters.api import Engine
    package = minimum_package()
    package["definitions"][0]["system_order"] = ["resources"]
    spec = package["definitions"][1]["components"]["resources"]["battery"]
    spec.update(recovery={"mode": "periodic", "interval_seconds": 1}, recovery_rule="rule/ark_resource_recovery", parameters={"rate": 2})
    simulation = Engine.create(Compiler().compile(package))
    simulation.advance(9)
    assert simulation.ctx.resources.current("actor/test", "battery") == 10
    simulation.advance(1)
    assert simulation.ctx.resources.current("actor/test", "battery") == 12


def test_cli_replay_matches_real_run_and_rejects_content_identity_change(tmp_path, capsys):
    from ark_sim.tools.cli import main
    from ark_sim.tools.compare import first_difference
    package = minimum_package()
    source, snapshot, record, replayed = (tmp_path / name for name in ("content.json", "snapshot.json", "record.json", "replayed.json"))
    source.write_text(json.dumps(package), encoding="utf-8")
    assert main(["run", str(source), "--ticks", "2", "--seed", "123", "--output", str(snapshot), "--replay-output", str(record)]) == 0
    assert main(["replay", str(source), "--record", str(record), "--output", str(replayed)]) == 0
    assert first_difference(json.loads(snapshot.read_text(encoding="utf-8")), json.loads(replayed.read_text(encoding="utf-8"))) is None
    package["definitions"][1]["components"]["attributes"]["base"]["power"] = 11
    source.write_text(json.dumps(package), encoding="utf-8")
    assert main(["replay", str(source), "--record", str(record), "--output", str(replayed)]) == 2
    assert "Replay program fingerprint" in capsys.readouterr().err


@pytest.mark.parametrize("parameter,value", [("--seed", "4"), ("--seconds", "2"), ("--ticks", "30")])
def test_cli_replay_cannot_override_recorded_time_or_seed(parameter, value):
    from ark_sim.tools.cli import main
    with pytest.raises(SystemExit) as result:
        main(["replay", "content.json", "--record", "record.json", parameter, value])
    assert result.value.code == 2


def test_optional_growth_rule_uses_default_calculation_binding():
    from ark_sim.adapters.api import Engine
    package = minimum_package()
    package["definitions"].append({"id": "rule/default_growth", "kind": "calculation_rule", "contract": "attributes.growth",
        "implementation": {"type": "expression", "expression": "inputs.base + inputs.level"}})
    package["definitions"][0]["bindings"]["attributes.growth"] = "rule/default_growth"
    package["definitions"][1]["components"]["attributes"]["growth"] = {"power": {"level": 7}}
    simulation = Engine.create(Compiler().compile(package))
    assert simulation.ctx.attributes.value("actor/test", "power") == 17


def test_optional_growth_rule_still_requires_an_available_binding():
    package = minimum_package()
    package["definitions"][1]["components"]["attributes"]["growth"] = {"power": {"level": 7}}
    with pytest.raises(CompileError, match="required calculation attributes.growth"):
        Compiler().compile(package)


@pytest.mark.parametrize("reference", [None, "", "   ", 3])
def test_present_growth_rule_must_be_a_nonempty_valid_reference(reference):
    package = minimum_package()
    package["definitions"][1]["components"]["attributes"]["growth"] = {"power": {"level": 7, "rule": reference}}
    with pytest.raises(CompileError, match="nonempty attributes.growth rule reference"):
        Compiler().compile(package)


def timed_package(location, timing):
    package = minimum_package()
    if location == "command":
        package["scenarioDraft"]["commands"] = [{"action": "unsupported_action", **timing}]
    elif location == "wave":
        package["scenarioDraft"]["waves"] = [{"definition": "unit/anything_can_be_created", **timing}]
    else:
        package["definitions"][1]["components"]["abilities"] = ["ability/timed"]
        package["definitions"].append({"id": "ability/timed", "kind": "ability", "activation": {"mode": "manual"},
            "timeline": [{"effect": {"op": "emit", "event": "timed"}, **timing}]})
    return package


@pytest.mark.parametrize("location", ["command", "wave", "timeline"])
@pytest.mark.parametrize("value", [-1, 0.5, True, None, "30"])
def test_at_is_a_nonnegative_integer_at_every_content_boundary(location, value):
    with pytest.raises(CompileError, match="nonnegative integer logical time"):
        Compiler().compile(timed_package(location, {"at": value}))


@pytest.mark.parametrize("location", ["command", "wave", "timeline"])
def test_at_and_at_seconds_are_mutually_exclusive(location):
    with pytest.raises(CompileError, match="mutually exclusive"):
        Compiler().compile(timed_package(location, {"at": 0, "at_seconds": 0}))


@pytest.mark.parametrize("alias", ["buffs", "buff_container"])
def test_initial_buff_ids_enter_closure_and_modify_the_created_instance(alias):
    from ark_sim.adapters.api import Engine
    package = minimum_package()
    package["definitions"][0]["attribute_layers"] = ["flat"]
    package["definitions"][1]["components"][alias] = {"initial": ["buff/on_birth"]}
    package["definitions"].append({"id": "buff/on_birth", "kind": "buff", "duration_seconds": 5,
        "modifiers": [{"attribute": "power", "layer": "flat", "value": 3}]})
    program = Compiler().compile(package)
    assert "buff/on_birth" in program.dependency_ids
    assert Engine.create(program).ctx.attributes.value("actor/test", "power") == 13


def test_initial_buff_aliases_cannot_both_be_present():
    package = minimum_package()
    package["definitions"][1]["components"].update(buffs={"initial": []}, buff_container={"initial": []})
    with pytest.raises(CompileError, match="mutually exclusive aliases"):
        Compiler().compile(package)


@pytest.mark.parametrize("value", [[{}], [1], [""]])
def test_initial_buff_list_only_accepts_nonempty_id_strings(value):
    package = minimum_package()
    package["definitions"][1]["components"]["buffs"] = {"initial": value}
    with pytest.raises(CompileError, match="Buff ID strings"):
        Compiler().compile(package)


def test_initial_buff_id_must_have_buff_kind():
    package = minimum_package()
    package["definitions"].append({"id": "unit/not_a_buff", "kind": "entity", "components": {}})
    package["definitions"][1]["components"]["buffs"] = {"initial": ["unit/not_a_buff"]}
    with pytest.raises(CompileError, match="not a Buff definition"):
        Compiler().compile(package)


@pytest.mark.parametrize("location", ["entity", "wave"])
def test_route_id_is_rejected_until_resolved_paths_are_supported(location):
    package = minimum_package()
    if location == "entity":
        package["definitions"][1]["components"]["spatial"] = {"route_id": "route/unresolved"}
    else:
        package["scenarioDraft"]["waves"] = [{"at": 0, "definition": "unit/anything_can_be_created", "route_id": "route/unresolved"}]
    with pytest.raises(CompileError, match="use an inline route"):
        Compiler().compile(package)


@pytest.mark.parametrize("count", [0, 2, 1.5, True])
def test_wave_entries_cannot_silently_ignore_count(count):
    package = minimum_package()
    package["scenarioDraft"]["waves"] = [{"at": 0, "definition": "unit/anything_can_be_created", "count": count}]
    with pytest.raises(CompileError, match="exactly one instance"):
        Compiler().compile(package)


@pytest.mark.parametrize("seconds,expected", [(0, 0), (0.6, 6), (1.2, 12), (0.600001, 7), (1.200001, 13)])
def test_preset_quantize_has_explicit_precision_without_frame_overshoot(seconds, expected):
    from ark_sim.tools.authoring import preview_calculation
    program = Compiler().compile(minimum_package())
    result = preview_calculation(program, "time.quantize", {"seconds": seconds, "quantum": 0.1, "rounding": {"mode": "ceil"}})
    assert result.value == expected
