"""Version-aware replay and exact-path snapshot comparison expectations."""
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ark_sim.tools.compare import first_difference
from ark_sim.tools.replay import ReplayError, _validated_record, replay


def test_compare_first_difference_sorted_object_keys_then_array_indices():
    expected = {"z": 12, "a": [2, 3, 4]}
    actual = {"z": 99, "a": [2, 13, 14]}
    assert first_difference(expected, actual) == {
        "path": "/a/1", "expected": 3, "actual": 13, "reason": "number"}


def test_compare_does_not_treat_booleans_as_numbers_even_under_tolerance():
    assert first_difference({"live": True}, {"live": 1}, {"/live": 10}) == {
        "path": "/live", "expected": True, "actual": 1, "reason": "type"}
    assert first_difference([False], [0], {"/0": 1})["reason"] == "type"
    assert first_difference(True, True) is None


def test_exact_numeric_default_and_path_local_absolute_tolerance():
    expected = {"hp": 100, "dp": 10}
    actual = {"hp": 100.00001, "dp": 10.00001}
    assert first_difference(expected, actual)["path"] == "/dp"
    assert first_difference(expected, actual, {"/dp": 0.001})["path"] == "/hp"
    assert first_difference(expected, actual, {"/dp": 0.001, "/hp": 0.001}) is None


def test_quantization_precedes_tolerance_and_exposes_rounding():
    assert first_difference({"x": 0.125}, {"x": 0.124}, {
        "/x": {"quantum": 0.01, "rounding": "half_even"}}) is None
    assert first_difference({"x": 0.125}, {"x": 0.124}, {
        "/x": {"quantum": 0.01, "rounding": "half_up"}})["path"] == "/x"
    assert first_difference(0.101, 0.109, {"": {"quantum": 0.01, "rounding": "floor"}}) is None
    assert first_difference(1000, 1000.1, {"": {"rel": 0.001}}) is None


def test_pointer_escaping_missing_null_and_freeze_shapes():
    assert first_difference({"a/b~c": []}, {"a/b~c": [None]}) == {
        "path": "/a~1b~0c/0", "expected": None, "actual": None, "reason": "missing_expected"}
    assert first_difference({"a": None}, {})["reason"] == "missing_actual"
    assert first_difference([1, 2], (1.0, 2.0)) is None


def test_arbitrary_json_integer_precision_is_preserved_with_quantization():
    large = 10 ** 400
    assert first_difference(large, large + 1, {"": {"quantum": 1}})["reason"] == "number"
    assert first_difference(large, large + 1, {"": 1}) is None


@pytest.mark.parametrize("tolerance", [
    {"/x": True}, {"x": 0.1}, {"/x": -1}, {"/x": {"abs": float("nan")}},
    {"/x": {"quantum": 0}}, {"/x": {"rounding": "floor"}},
    {"/x": {"quantum": 1, "rounding": "mystery"}}, {"/x": {"misspelled": 1}},
])
def test_invalid_compare_configuration_is_explicit(tolerance):
    with pytest.raises(ValueError):
        first_difference({"x": 1}, {"x": 2}, tolerance)


def _record(**overrides):
    return {"schema": "ark-sim/replay/v2", "program_fingerprint": "program-v1",
            "runtime_fingerprint": "runtime-v1", "seed": 4, "until": 20,
            "commands": [{"at": 0, "order": 0, "action": {"type": "custom"}}], **overrides}


def test_replay_validation_detaches_record_and_checks_versions_before_engine_import():
    record = _record()
    validated = _validated_record(SimpleNamespace(fingerprint="program-v1"), record)
    record["commands"][0]["action"]["type"] = "mutated"
    assert validated["commands"][0]["action"]["type"] == "custom"
    json.dumps(validated, allow_nan=False)
    with pytest.raises(ReplayError, match="program fingerprint"):
        _validated_record(SimpleNamespace(fingerprint="other"), validated)


@pytest.mark.parametrize("overrides,match", [
    ({"schema": "old"}, "schema"), ({"runtime_fingerprint": None}, "runtime fingerprint"),
    ({"until": True}, "end time"), ({"until": -1}, "end time"),
    ({"commands": [{"at": 0.5, "order": 1, "action": {}}]}, "time"),
    ({"commands": [{"at": 0, "order": 1, "action": []}]}, "action"),
    ({"commands": [{"at": 0, "order": 1, "action": {}}, {"at": 1, "order": 1, "action": {}}]}, "Duplicate"),
    ({"commands": [{"at": 1, "submitted_at": 2, "order": 1, "action": {}}]}, "before its submission"),
    ({"commands": [{"at": 30, "submitted_at": 25, "order": 1, "action": {}}]}, "after replay end"),
    ({"commands": [{"at": 5, "submitted_at": 5, "order": 1, "action": {}},
                   {"at": 2, "submitted_at": 2, "order": 2, "action": {}}]}, "nondecreasing"),
])
def test_invalid_replay_record_is_rejected_before_execution(overrides, match):
    with pytest.raises(ReplayError, match=match):
        _validated_record(SimpleNamespace(fingerprint="program-v1"), _record(**overrides))


def test_submission_time_defaults_zero_and_future_pending_command_is_valid():
    record = _record(commands=[{"at": 30, "order": 1, "action": {}}])
    validated = _validated_record(SimpleNamespace(fingerprint="program-v1"), record)
    assert validated["commands"][0]["submitted_at"] == 0


def test_replay_preserves_submission_order_and_exact_clock_boundary(monkeypatch):
    operations = []

    class Simulation:
        time = 0

        @property
        def session(self):
            return self

        def checkpoint(self):
            return {"runtime_fingerprint": "runtime-v1"}

        def snapshot(self):
            return {"time": self.time}

        def advance(self, n):
            operations.append(("advance", n))
            self.time += n

        def submit(self, action, at):
            operations.append(("submit", self.time, at, action["type"]))

    module = ModuleType("ark_sim.adapters.api")
    module.Engine = SimpleNamespace(create=lambda program, seed: Simulation())
    monkeypatch.setitem(sys.modules, "ark_sim.adapters.api", module)
    record = _record(commands=[
        {"at": 6, "submitted_at": 4, "order": 2, "action": {"type": "interactive"}},
        {"at": 30, "submitted_at": 0, "order": 0, "action": {"type": "future_pending"}},
        {"at": 1, "order": 1, "action": {"type": "early"}},
    ])
    result = replay(SimpleNamespace(fingerprint="program-v1"), record)
    assert result.time == 20
    assert operations == [
        ("advance", 0), ("submit", 0, 30, "future_pending"),
        ("advance", 0), ("submit", 0, 1, "early"),
        ("advance", 4), ("submit", 4, 6, "interactive"), ("advance", 16),
    ]


def test_runtime_fingerprint_mismatch_rejects_before_commands(monkeypatch):
    module = ModuleType("ark_sim.adapters.api")
    module.Engine = SimpleNamespace(create=lambda program, seed: SimpleNamespace(
        checkpoint=lambda: {"runtime_fingerprint": "changed-runtime"}))
    monkeypatch.setitem(sys.modules, "ark_sim.adapters.api", module)
    with pytest.raises(ReplayError, match="runtime fingerprint"):
        replay(SimpleNamespace(fingerprint="program-v1"), _record())


def _real_program(providers=None, custom_mitigation=False, deployment=False):
    from ark_sim.content import Compiler

    package = {"schemaVersion": 2,
        "rulesets": [{"id": "ruleset/replay_test", "kind": "ruleset", "extends": "ruleset/ark_standard", "quantum": 0.1}],
        "selectors": [{"id": "selector/replay_enemy", "kind": "selector", "region": {"type": "all"},
                       "filters": [{"tag": "enemy"}, {"state": "alive"}], "limit": 1}],
        "abilities": [{"id": "ability/replay_burst", "kind": "ability", "activation": {"mode": "manual",
                       "costs": [{"resource": "energy", "amount": 20}]}, "selector": "selector/replay_enemy",
                       "timeline": [{"at_seconds": 0.2, "repeat": {"count": 3, "interval_seconds": 0.2},
                                     "effect": {"op": "damage", "target": "selected", "damage_type": "physical"}}]}],
        "entities": [
            {"id": "unit/replay_guard", "kind": "entity", "tags": ["player"], "components": {
                "attributes": {"base": {"atk": 100, "def": 0, "mres": 0, "max_hp": 1000}},
                "resources": {"hp": {"initial": 1000, "capacity_attribute": "max_hp", "role": "health"},
                              "energy": {"initial": 20, "capacity": 40}},
                "abilities": ["ability/replay_burst"], "lifecycle": {"policy": "policy/ark_lifecycle"}}},
            {"id": "unit/replay_dummy", "kind": "entity", "tags": ["enemy"], "components": {
                "attributes": {"base": {"atk": 0, "def": 80, "mres": 0, "max_hp": 10000}},
                "resources": {"hp": {"initial": 10000, "capacity_attribute": "max_hp", "role": "health"}},
                "abilities": [], "lifecycle": {"policy": "policy/ark_lifecycle"}}}],
        "scenarioDraft": {"id": "scenario/replay_test", "ruleset": "ruleset/replay_test", "map": {"rows": 2, "cols": 3},
            "initialEntities": [{"definition": "unit/replay_guard", "instanceAlias": "guard", "position": {"row": 0, "col": 0}},
                                {"definition": "unit/replay_dummy", "instanceAlias": "dummy", "position": {"row": 0, "col": 1}}]}}
    if custom_mitigation:
        package["rules"] = [{"id": "rule/custom_half", "kind": "calculation_rule", "contract": "damage.mitigation",
            "implementation": {"type": "provider", "provider": "test.custom.half"}}]
        package["scenarioDraft"]["rules"] = {"damage.mitigation": "rule/custom_half"}
    if deployment:
        deployable = json.loads(json.dumps(package["entities"][0]))
        deployable["id"] = "unit/deploy_guard"
        deployable["components"]["abilities"] = []
        deployable["components"]["deployable"] = {"policy": "policy/ark_ground_deploy", "cost": 5, "terrain": "ground"}
        package["entities"].append(deployable)
        package["scenarioDraft"]["dependencies"] = [deployable["id"]]
        package["scenarioDraft"]["resources"] = {"dp": {"initial": 10, "capacity": 10}}
    return Compiler(providers=providers).compile(package)


def test_real_engine_interactive_replay_and_json_checkpoint_have_identical_states():
    from ark_sim.adapters.api import Engine

    program = _real_program()
    original = Engine.create(program, seed=19)
    original.advance(3)
    original.submit({"action": "activate_ability", "source": "guard", "ability": "ability/replay_burst"}, at=5)
    original.advance(4)
    checkpoint = json.loads(json.dumps(original.checkpoint()))
    original.advance(6)
    expected = original.snapshot()
    dummy = next(entity for entity in expected["entities"] if entity["definition_id"] == "unit/replay_dummy")
    assert dummy["components"]["resources"]["hp"]["current"] == 9940
    assert original.export_replay()["commands"][0]["submitted_at"] == 3
    repeated = replay(program, json.loads(json.dumps(original.export_replay())))
    assert first_difference(expected, repeated.snapshot()) is None
    restored = Engine.restore(program, checkpoint)
    restored.advance(6)
    assert first_difference(expected, restored.snapshot()) is None


def test_real_engine_runtime_lock_rejects_record_from_another_implementation_identity():
    from ark_sim.adapters.api import Engine

    program = _real_program()
    record = Engine.create(program).export_replay()
    record["runtime_fingerprint"] = "different-implementation"
    with pytest.raises(ReplayError, match="runtime fingerprint"):
        replay(program, record)


def _half_damage(inputs, params, context):
    return inputs["power"] * 0.5


_half_damage.version = "test-half/1"


class ConstantBackend:
    algorithm = "test/constant"
    version = "1"

    def __init__(self, seed):
        self.counter = 0

    def sample(self):
        self.counter += 1
        return 0.25

    def snapshot(self):
        return {"counter": self.counter}

    def restore(self, data):
        self.counter = data["counter"]


def test_real_custom_provider_and_rng_factory_replay_require_matching_injection():
    from ark_sim.adapters.api import Engine
    from ark_sim.domains.providers import BUILTIN_PROVIDERS

    providers = {**BUILTIN_PROVIDERS, "test.custom.half": _half_damage}
    program = _real_program(providers=providers, custom_mitigation=True)
    original = Engine.create(program, seed=5, providers=providers, random_factory=ConstantBackend)
    original.submit({"action": "activate_ability", "source": "guard", "ability": "ability/replay_burst"})
    original.advance(8)
    expected = original.snapshot()
    dummy = next(entity for entity in expected["entities"] if entity["definition_id"] == "unit/replay_dummy")
    assert dummy["components"]["resources"]["hp"]["current"] == 9850
    record = original.export_replay()
    record["random_algorithm"] = original.session.random.algorithm
    repeated = replay(program, record, providers=providers, random_factory=ConstantBackend)
    assert first_difference(expected, repeated.snapshot()) is None
    with pytest.raises(ValueError, match="Unknown random algorithm"):
        replay(program, record, providers=providers)


def test_real_command_alias_failure_after_dp_payment_rolls_back_world_and_resources():
    from ark_sim.adapters.api import Engine

    program = _real_program(deployment=True)
    expected = Engine.create(program)
    failed = Engine.create(program)
    failed.submit({"action": "deploy", "entity": "unit/deploy_guard", "position": {"row": 1, "col": 1}, "alias": "guard"})
    expected.advance(1)
    failed.advance(1)
    assert failed.session.world.snapshot() == expected.session.world.snapshot()
    assert failed.ctx.resources.current("system/battle", "dp") == 10
    rejected = [event for event in failed.session.events if event["type"] == "command.rejected"]
    assert len(rejected) == 1 and "Alias already exists" in rejected[0]["payload"]["reason"]
    assert not any(event["type"] == "resource.changed" and event["payload"]["delta"] == -5 for event in failed.session.events)
