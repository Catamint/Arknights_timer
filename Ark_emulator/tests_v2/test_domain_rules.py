"""Independent substrate requirements discovered during domain integration review."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Mapping

import pytest

from ark_sim.contracts import thaw, Intent
from ark_sim.domains.attributes import AttributeSystem
from ark_sim.domains.resources import ResourceSystem
from ark_sim.domains.effects import EffectSystem
from ark_sim.domains.providers import BUILTIN_PROVIDERS
from ark_sim.kernel import Session
from ark_sim.rules import RuleRuntime, RuleError, ProviderContext


def preset():
    return json.loads((Path(__file__).parents[1] / "ark_sim/content/presets/ark_standard.json").read_text(encoding="utf-8"))


class IsolatedContext:
    """Real kernel and rules, without depending on the in-progress public Engine."""
    def __init__(self, components, *, rule_definitions=None, bindings=None, ruleset_changes=None, providers=None, quantum=1):
        package = preset()
        ruleset = deepcopy(package["rulesets"][0])
        ruleset.update(ruleset_changes or {})
        if bindings is not None:
            ruleset["bindings"] = bindings
        self.program = SimpleNamespace(ruleset=ruleset)
        self.session = Session(quantum=quantum)
        self.ref = self.session.world.create("unit/test", components)
        self.rules = RuleRuntime(rule_definitions if rule_definitions is not None else package["rules"],
            bindings=ruleset["bindings"], providers={**BUILTIN_PROVIDERS, **(providers or {})})
        self.attributes = AttributeSystem(self)
        self.resources = ResourceSystem(self)
        self.lifecycle = None
        self.calls = []

    def entity(self, ref):
        return self.session.world.get(ref)

    def get(self, ref, path, default=None):
        value = self.entity(ref)["components"]
        for part in path:
            if not isinstance(value, Mapping) or part not in value:
                return default
            value = value[part]
        return thaw(value)

    def calc(self, calculation, inputs, **kwargs):
        self.calls.append((calculation, inputs))
        scope = {"component": kwargs.get("component", {}), "attribute_or_resource": kwargs.get("local", {}),
                 "ability": (kwargs.get("ability") or {}).get("rules", {}),
                 "effect": (kwargs.get("effect") or {}).get("rules", {})}
        context = {"time": self.session.time, "seconds": self.session.time*self.session.quantum,
            **(kwargs.get("extra") or {})}
        result = self.rules.evaluate(calculation, inputs, scope=scope, rule_id=kwargs.get("rule_id"), context=context)
        self.last_calculation_event_id = self.session.emit("calculation", {"calculation_id": calculation, "value": thaw(result.value)})
        return thaw(result.value)

    def state(self):
        return {"finished": False}

    def quantize(self, seconds):
        return self.calc("time.quantize", {"seconds": seconds, "quantum": self.session.quantum, "rounding": {"mode": "ceil"}})

    def alive(self, ref):
        return True

    def emit(self, kind, payload, cause=None):
        return self.session.emit(kind, payload, cause=cause)

    def attribute_role(self, role):
        return {"attack": "atk", "defense": "def", "resistance": "res"}.get(role, role)

    def role_value(self, ref, role):
        return self.attributes.value(ref, self.attribute_role(role))

    def health_resource(self, ref):
        return "hp"

    def state_update(self, **values):
        self.battle_state = values


def test_standard_attribute_layers_have_independent_expected_value():
    context = IsolatedContext({"attributes": {"base": {"atk": 100}, "modifiers": [
        {"attribute": "atk", "layer": "flat", "value": 10},
        {"attribute": "atk", "layer": "direct_ratio", "value": 0.5}]}})
    assert context.attributes.value(context.ref, "atk") == 165


def test_whole_attribute_pipeline_can_skip_all_individual_layers():
    rule = {"id": "custom/effective", "contract": "attributes.effective", "implementation": {"type": "provider", "provider": "custom/effective"}}
    context = IsolatedContext({"attributes": {"base": {"atk": 10}, "modifiers": [
        {"attribute": "atk", "layer": "opaque_custom_layer", "value": 3}]}},
        rule_definitions=[rule], bindings={"attributes.effective": rule["id"]},
        ruleset_changes={"attribute_layers": ["opaque_custom_layer"], "parameters": {"layer_operations": {}}},
        providers={"custom/effective": lambda inputs, params, context: 777})
    assert context.attributes.value(context.ref, "atk") == 777
    assert [calculation for calculation, _ in context.calls] == ["attributes.effective"]


def test_custom_modifier_formula_can_define_a_new_nonstandard_layer_operation():
    package = preset()
    selected = [rule for rule in package["rules"] if rule["contract"] in ("attributes.effective", "attributes.modifier_layer")]
    custom = {"id": "custom/square", "contract": "attributes.modifier_layer", "implementation": {
        "type": "expression", "expression": "inputs.value * inputs.value + inputs.modifiers[0].value"}}
    selected.append(custom)
    context = IsolatedContext({"attributes": {"base": {"atk": 10}, "modifiers": [
        {"attribute": "atk", "layer": "square", "value": 3}]}}, rule_definitions=selected,
        bindings={"attributes.effective": "rule/ark_attribute_layers", "attributes.modifier_layer": "custom/square"},
        ruleset_changes={"attribute_layers": ["square"], "parameters": {"layer_operations": {"square": "custom"}}})
    assert context.attributes.value(context.ref, "atk") == 103


def test_plain_recovery_rate_flows_through_default_resource_rule():
    context = IsolatedContext({"resources": {"energy": {"current": 1, "spec": {"initial": 1, "capacity": 20, "recovery_rate": 2}}}})
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "energy") == 3


def test_multiple_resource_payment_failure_does_not_partially_charge():
    context = IsolatedContext({"resources": {
        "energy": {"current": 20, "spec": {"initial": 20, "capacity": 20}},
        "ammo": {"current": 1, "spec": {"initial": 1, "capacity": 1}},
    }})
    before = context.session.world.snapshot()
    with pytest.raises(ValueError, match="insufficient"):
        context.resources.payment_plan(context.ref, [{"resource": "energy", "amount": 10}, {"resource": "ammo", "amount": 2}])
    assert context.session.world.snapshot() == before
    assert context.session.scheduler.pending == ()


def test_multiple_resource_payment_plan_is_atomic_and_handles_duplicate_costs():
    context = IsolatedContext({"resources": {
        "energy": {"current": 20, "spec": {"initial": 20, "capacity": 20}},
        "ammo": {"current": 3, "spec": {"initial": 3, "capacity": 3}},
    }})
    intents = context.resources.payment_plan(context.ref, [
        {"resource": "energy", "amount": 3}, {"resource": "energy", "amount": 4}, {"resource": "ammo", "amount": 2}])
    assert context.resources.current(context.ref, "energy") == 20
    context.session.commit(intents)
    assert context.resources.current(context.ref, "energy") == 13
    assert context.resources.current(context.ref, "ammo") == 1


def test_provider_context_pure_services_preserve_local_bindings_and_trace():
    captured = []
    def whole(inputs, params, context):
        captured.append(context)
        addition = context.invoke_provider("custom/aggregate", {"values": [1, 2]}, {"factor": 1})
        return context.calculate("attributes.modifier_layer", {
            "value": inputs["base"], "modifiers": [], "layer_parameters": {"additive": addition}}).value

    rules = [
        {"id": "whole", "contract": "attributes.effective", "implementation": {"type": "provider", "provider": "custom/whole"}},
        {"id": "add", "contract": "attributes.modifier_layer", "implementation": {"type": "expression", "expression": "inputs.value + inputs.layer_parameters.additive"}},
        {"id": "square", "contract": "attributes.modifier_layer", "implementation": {"type": "expression", "expression": "inputs.value ** 2 + inputs.layer_parameters.additive"}},
    ]
    runtime = RuleRuntime(rules, bindings={"attributes.effective": "whole", "attributes.modifier_layer": "add"}, providers={
        "custom/whole": {"callable": whole, "version": "1"},
        "custom/aggregate": {"callable": lambda i, p, c: sum(i["values"])*p["factor"], "version": "1", "output_schema": {"type": "number"}},
    })
    inputs = {"base": 10, "modifier_layers": [], "order": []}
    assert runtime.evaluate("attributes.effective", inputs).value == 13
    result = runtime.evaluate("attributes.effective", inputs, scope={"owner": {"attributes.modifier_layer": "square"}}, context={"nested": {"a": 1}})
    assert result.value == 103
    assert isinstance(captured[-1], ProviderContext)
    assert captured[-1]["nested"]["a"] == 1
    with pytest.raises(TypeError):
        captured[-1].mutable = True
    assert "calculate" not in captured[-1] and "invoke_provider" not in captured[-1]
    assert result.trace["stages"][0]["kind"] == "provider"
    assert result.trace["stages"][1]["trace"]["binding"]["origin"] == "owner"
    assert result.trace["stages"][1]["rule_id"] == "square"
    json.dumps(thaw(result.trace), allow_nan=False)


def test_provider_context_rejects_pure_provider_and_calculation_cycles():
    rules = [{"id": "whole", "contract": "attributes.effective", "implementation": {"type": "provider", "provider": "custom/whole"}}]
    inputs = {"base": 10, "modifier_layers": [], "order": []}
    runtime = RuleRuntime(rules, bindings={"attributes.effective": "whole"}, providers={
        "custom/whole": lambda i, p, c: c.invoke_provider("custom/whole", i, p)})
    with pytest.raises(RuleError, match="Pure provider cycle"):
        runtime.evaluate("attributes.effective", inputs)
    assert runtime._provider_stack == [] and runtime._evaluation_stack == []
    runtime = RuleRuntime(rules, bindings={"attributes.effective": "whole"}, providers={
        "custom/whole": lambda i, p, c: c.calculate("attributes.effective", i).value})
    with pytest.raises(RuleError, match="Dynamic rule binding cycle"):
        runtime.evaluate("attributes.effective", inputs)
    assert runtime._provider_stack == [] and runtime._evaluation_stack == []


def test_explicit_recovery_rule_can_cool_a_resource_at_capacity():
    custom = {"id": "custom/cooling", "contract": "resource.recovery", "implementation": {
        "type": "expression", "expression": "inputs.current - 5 * inputs.delta_seconds"}}
    context = IsolatedContext({"resources": {"heat": {"current": 100,
        "spec": {"initial": 100, "capacity": 100, "recovery_rule": custom["id"]}}}},
        rule_definitions=preset()["rules"] + [custom])
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "heat") == 95


def test_resource_capacity_custom_formula_receives_effective_attributes():
    custom = {"id": "custom/capacity", "contract": "resource.capacity", "implementation": {
        "type": "expression", "expression": "inputs.attributes.atk * 2"}}
    context = IsolatedContext({"attributes": {"base": {"atk": 10}, "modifiers": [
        {"attribute": "atk", "layer": "direct_ratio", "value": 0.5}]},
        "resources": {"energy": {"current": 1, "spec": {"initial": 1, "capacity": 20, "capacity_rule": custom["id"]}}}},
        rule_definitions=preset()["rules"] + [custom])
    assert context.resources.capacity(context.ref, "energy") == 30


def test_resource_capacity_in_resource_change_respects_ability_scope():
    custom = {"id": "custom/small_capacity", "contract": "resource.capacity", "implementation": {"type": "expression", "expression": "5"}}
    context = IsolatedContext({"resources": {"energy": {"current": 10, "spec": {"initial": 10, "capacity": 20}}}},
        rule_definitions=preset()["rules"] + [custom])
    context.resources.adjust(context.ref, "energy", 1, ability={"rules": {"resource.capacity": custom["id"]}})
    assert context.resources.current(context.ref, "energy") == 5


def test_each_attribute_has_its_own_rule_binding_and_name_in_context():
    seen = []
    def squared(inputs, params, context):
        seen.append(context["attribute"])
        return inputs["base"] ** 2
    custom = {"id": "custom/atk", "contract": "attributes.effective", "implementation": {"type": "provider", "provider": "custom/atk"}}
    context = IsolatedContext({"attributes": {"base": {"atk": 10, "def": 10},
        "attribute_rules": {"atk": {"attributes.effective": custom["id"]}}}},
        rule_definitions=preset()["rules"] + [custom], providers={"custom/atk": squared})
    assert context.attributes.value(context.ref, "atk") == 100
    assert context.attributes.value(context.ref, "def") == 10
    assert seen == ["atk"]


def test_explicit_empty_layer_order_skips_all_modifiers():
    context = IsolatedContext({"attributes": {"base": {"atk": 10}, "layers": [], "modifiers": [
        {"attribute": "atk", "layer": "flat", "value": 50}]}})
    assert context.attributes.value(context.ref, "atk") == 10


def test_resource_cost_and_recovery_receive_effective_attributes():
    cost_rule = {"id": "custom/cost", "contract": "resource.cost", "implementation": {
        "type": "expression", "expression": "inputs.attributes.atk / 2"}}
    recover_rule = {"id": "custom/recovery", "contract": "resource.recovery", "implementation": {
        "type": "expression", "expression": "inputs.current + inputs.attributes.atk * inputs.delta_seconds"}}
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}, "resources": {"energy": {"current": 20,
        "spec": {"initial": 20, "capacity": 30, "recovery_rule": recover_rule["id"]}}}},
        rule_definitions=preset()["rules"] + [cost_rule, recover_rule])
    intents = context.resources.payment_plan(context.ref, [{"resource": "energy", "rule": cost_rule["id"]}])
    assert context.resources.current(context.ref, "energy") == 20
    context.session.commit(intents)
    assert context.resources.current(context.ref, "energy") == 15
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "energy") == 25


def test_recovery_freezing_is_explicit_per_resource_during_active_cast():
    recover = {"id": "custom/recover", "contract": "resource.recovery", "implementation": {
        "type": "expression", "expression": "inputs.current + 2 * inputs.delta_seconds"}}
    components = {"runtime": {"casts": {"cast/1": {"ability": "skill/1"}}}, "resources": {
        "sp": {"current": 1, "spec": {"initial": 1, "capacity": 20, "recovery_rule": recover["id"], "parameters": {"freeze_while_cast": True}}},
        "energy": {"current": 1, "spec": {"initial": 1, "capacity": 20, "recovery_rule": recover["id"]}},
    }}
    context = IsolatedContext(components, rule_definitions=preset()["rules"] + [recover])
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "sp") == 1
    assert context.resources.current(context.ref, "energy") == 3
    context.session.commit([Intent("set", context.ref, ("runtime", "casts"), {})])
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "sp") == 3


def test_raw_modifier_stack_count_is_interpreted_by_preset():
    context = IsolatedContext({"attributes": {"base": {"atk": 100}, "modifiers": [
        {"attribute": "atk", "layer": "direct_ratio", "value": 0.1, "stacks": 3}]}})
    assert context.attributes.value(context.ref, "atk") == 130


def test_rejected_damage_does_not_trigger_success_or_attack_recovery():
    rule = {"id": "custom/reject_damage", "contract": "damage.pipeline", "implementation": {"type": "provider", "provider": "custom/reject_damage"}}
    package = preset()
    binding = {**package["rulesets"][0]["bindings"], "damage.pipeline": rule["id"]}
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}, "resources": {
        "energy": {"current": 20, "spec": {"initial": 20, "capacity": 30}}}},
        rule_definitions=package["rules"] + [rule], bindings=binding,
        providers={"custom/reject_damage": lambda i, p, c: {"accepted": False, "amount": 0, "allocations": [], "events": []}})
    target = context.session.world.create("unit/target", {"attributes": {"base": {"def": 0, "res": 0}},
        "resources": {"hp": {"current": 100, "spec": {"initial": 100, "capacity": 100}}}})
    effects = EffectSystem(context)
    effects.execute(context.ref, [target], {"op": "damage", "on_success": [
        {"op": "modify_resource", "target": "source", "resource": "energy", "delta": 2}],
        "on_failure": [{"op": "modify_resource", "target": "source", "resource": "energy", "delta": -1}]},
        ability={"activation": {"parameters": {"sp_resource": "energy", "recovery_per_attack": 3}}})
    assert context.resources.current(context.ref, "energy") == 19
    assert context.resources.current(target, "hp") == 100


def test_whole_damage_pipeline_can_apply_fixed_damage_without_attack_attributes():
    rule = {"id": "custom/fixed_damage", "contract": "damage.pipeline", "implementation": {"type": "provider", "provider": "custom/fixed_damage"}}
    package = preset()
    binding = {**package["rulesets"][0]["bindings"], "damage.pipeline": rule["id"]}
    context = IsolatedContext({}, rule_definitions=package["rules"] + [rule], bindings=binding,
        providers={"custom/fixed_damage": lambda i, p, c: {"accepted": True, "amount": 5, "allocations": [], "events": []}})
    target = context.session.world.create("unit/target", {"resources": {"hp": {"current": 100,
        "spec": {"initial": 100, "capacity": 100}}}})
    EffectSystem(context).execute(context.ref, [target], {"op": "damage"})
    assert context.resources.current(target, "hp") == 95


def test_calculation_context_uses_string_keys_for_json_replay_identity():
    rule = {"id": "custom/number", "contract": "damage.base", "implementation": {"type": "expression", "expression": "1"}}
    runtime = RuleRuntime([rule])
    with pytest.raises(RuleError, match="JSON mapping keys must be strings"):
        runtime.evaluate_ref(rule["id"], {"attack": 1, "scale": 1, "additions": 0}, {"entity_snapshots": {1: {"id": 1}}})


def test_recovery_freeze_can_select_skill_casts_and_keep_normal_attack_recovery():
    recover = {"id": "custom/recover", "contract": "resource.recovery", "implementation": {
        "type": "expression", "expression": "inputs.current + 2 * inputs.delta_seconds"}}
    components = {"runtime": {"casts": {"cast/1": {"ability": "basic", "activation_mode": "automatic_attack"}}},
        "resources": {"sp": {"current": 1, "spec": {"initial": 1, "capacity": 20, "recovery_rule": recover["id"],
            "parameters": {"freeze_while_cast": True, "freeze_cast_modes": ["manual"]}}}}}
    context = IsolatedContext(components, rule_definitions=preset()["rules"] + [recover])
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "sp") == 3
    context.session.commit([Intent("set", context.ref, ("runtime", "casts"), {
        "cast/2": {"ability": "skill", "activation_mode": "manual"}})])
    context.resources.tick(context.session)
    assert context.resources.current(context.ref, "sp") == 3


def allocation_context(allocations):
    rule = {"id": "custom/allocate", "contract": "damage.pipeline", "implementation": {"type": "provider", "provider": "custom/allocate"}}
    package = preset()
    context = IsolatedContext({}, rule_definitions=package["rules"] + [rule],
        bindings={**package["rulesets"][0]["bindings"], "damage.pipeline": rule["id"]},
        providers={"custom/allocate": lambda i, p, c: {"accepted": True, "amount": 10, "allocations": allocations, "events": []}})
    context.lifecycle = SimpleNamespace(check=lambda ref, cause: None)
    target = context.session.world.create("unit/target", {"resources": {
        "hp": {"current": 100, "spec": {"initial": 100, "capacity": 100}},
        "shield": {"current": 20, "spec": {"initial": 20, "capacity": 20}},
    }})
    return context, target


def test_whole_pipeline_allocates_damage_to_shield_resource():
    context, target = allocation_context([{"resource": "shield", "amount": 10}])
    EffectSystem(context).execute(context.ref, [target], {"op": "damage"})
    assert context.resources.current(target, "shield") == 10
    assert context.resources.current(target, "hp") == 100


def test_multiple_pipeline_allocations_to_same_resource_do_not_overwrite_each_other():
    context, target = allocation_context([{"resource": "shield", "amount": 5}, {"resource": "shield", "amount": 5}])
    EffectSystem(context).execute(context.ref, [target], {"op": "damage"})
    assert context.resources.current(target, "shield") == 10
    assert context.resources.current(target, "hp") == 100


def test_failed_later_allocation_does_not_commit_earlier_resource_change():
    context, target = allocation_context([{"resource": "shield", "amount": 5}, {"resource": "missing", "amount": 5}])
    before = context.session.world.snapshot()
    with pytest.raises(ValueError, match="resource 'missing'"):
        EffectSystem(context).execute(context.ref, [target], {"op": "damage"})
    assert context.session.world.snapshot() == before


def snapshot_damage_context(*, self_target=False):
    rule = {"id": "custom/snapshot_damage", "contract": "damage.pipeline",
        "metadata": {"input_bindings": {"armor": {"entity": "target", "attribute": "def"}}},
        "implementation": {"type": "provider", "provider": "custom/snapshot_damage"}}
    package = preset()
    components = {"attributes": {"base": {"def": 10}}, "resources": {
        "hp": {"current": 100, "spec": {"initial": 100, "capacity": 100}}}}
    context = IsolatedContext(components if self_target else {}, rule_definitions=package["rules"] + [rule],
        bindings={**package["rulesets"][0]["bindings"], "damage.pipeline": rule["id"]},
        providers={"custom/snapshot_damage": lambda i, p, c: {"accepted": True, "amount": i["effect"]["armor"], "allocations": [], "events": []}})
    target = context.ref if self_target else context.session.world.create("unit/target", components)
    original = context.entity(target)
    context.session.commit([Intent("set", target, ("attributes", "base", "def"), 80)])
    cast = {"source_snapshot": original if self_target else context.entity(context.ref),
        "target_snapshots": {str(target): original}, "launch_target_snapshots": {str(target): original}}
    return context, target, cast


@pytest.mark.parametrize("mode,expected_hp", [("at_cast", 90), ("at_launch", 90), ("at_hit", 20)])
def test_target_attribute_read_mode_uses_the_requested_snapshot(mode, expected_hp):
    context, target, cast = snapshot_damage_context()
    EffectSystem(context).execute(context.ref, [target], {"op": "damage", "read_mode": {"target_attributes": mode}}, cast=cast)
    assert context.resources.current(target, "hp") == expected_hp


def test_same_entity_can_have_different_source_and_target_attribute_read_modes():
    context, target, cast = snapshot_damage_context(self_target=True)
    EffectSystem(context).execute(context.ref, [target], {"op": "damage", "read_mode": {
        "source_attributes": "at_hit", "target_attributes": "at_cast"}}, cast=cast)
    assert context.resources.current(target, "hp") == 90


def deployment_simulation(*, custom_cost=False):
    from ark_sim.content import Compiler
    from ark_sim.adapters.api import Engine
    unit = {"id": "unit/custom_deploy", "kind": "entity", "tags": ["player", "ground"], "components": {
        "attributes": {"base": {"atk": 10}},
        "resources": {"hp": {"initial": 100, "capacity": 100, "role": "health"}},
        "deployable": {"cost": 100 if custom_cost else 10, "policy": "policy/ark_ground_deploy"},
        "spatial": {"coordinate_space": "scenario_map"}}}
    rules = []
    if custom_cost:
        rule = {"id": "rule/free_deployment", "kind": "calculation_rule", "contract": "deploy.cost", "implementation": {"type": "expression", "expression": "0"}}
        rules.append(rule)
        unit["rules"] = {"deploy.cost": rule["id"]}
    package = {"schemaVersion": 2, "entities": [unit], "rules": rules, "scenarioDraft": {
        "id": "scenario/deploy", "ruleset": "ruleset/ark_standard", "roster": [unit["id"]],
        "map": {"rows": 3, "cols": 3}, "resources": {"dp": {"initial": 10, "capacity": 100}}}}
    return Engine.create(Compiler().compile(package))


def test_deployment_uses_custom_unit_cost_rule():
    simulation = deployment_simulation(custom_cost=True)
    simulation.submit({"action": "deploy", "entity": "unit/custom_deploy", "row": 1, "col": 1})
    simulation.advance(1)
    assert simulation.ctx.resources.current("system/battle", "dp") == 10
    assert any(entity["definition_id"] == "unit/custom_deploy" for entity in simulation.session.world.entities())


def test_failed_deployment_alias_collision_does_not_spend_currency():
    simulation = deployment_simulation()
    simulation.submit({"action": "deploy", "entity": "unit/custom_deploy", "row": 1, "col": 1, "alias": "system/battle"})
    simulation.advance(1)
    assert simulation.ctx.resources.current("system/battle", "dp") == 10
    assert not any(entity["definition_id"] == "unit/custom_deploy" for entity in simulation.session.world.entities())


def test_custom_catalog_scalar_alias_keeps_declared_units_and_type_checks():
    catalog = {"contracts": [{"id": "custom.temperature", "owner": "owner", "inputs": [], "outputType": "temperature"}],
        "types": {"temperature": {"representation": "NumericProfile scalar", "unit": "celsius"}}}
    rule = {"id": "custom/celsius", "contract": "custom.temperature", "implementation": {"type": "expression", "expression": "-20.5"}}
    runtime = RuleRuntime([rule], catalog=catalog)
    assert runtime.evaluate_ref(rule["id"], {}).value == -20.5
    rule["implementation"]["expression"] = "True"
    runtime = RuleRuntime([rule], catalog=catalog)
    with pytest.raises(RuleError, match="expected temperature"):
        runtime.evaluate_ref(rule["id"], {})


def test_periodic_recovery_has_exact_units_and_six_second_payment_threshold():
    context = IsolatedContext({"resources": {"funds": {"current": 3, "spec": {"initial": 3, "capacity": 100,
        "recovery_rate": 1, "recovery": {"mode": "periodic", "interval_seconds": 1}}}}}, quantum=1/30)
    context.session.add_system(context.resources.tick)
    context.session.advance(29)
    assert context.resources.current(context.ref, "funds") == 3
    assert context.get(context.ref, ("resources", "funds", "timing", "elapsed_units")) == 29
    context.session.advance(1)
    assert context.resources.current(context.ref, "funds") == 4
    changes = [event for event in context.session.events if event["type"] == "resource.changed"]
    assert changes[-1]["time"] == 29
    context.session.advance(150)
    assert context.resources.current(context.ref, "funds") == 9
    context.session.commit(context.resources.payment_plan(context.ref, [{"resource": "funds", "amount": 9}]))
    assert context.resources.current(context.ref, "funds") == 0


def test_arbitrary_heat_resource_cools_on_two_second_period_while_full():
    cooling = {"id": "custom/cooling", "contract": "resource.recovery", "implementation": {"type": "expression", "expression": "inputs.current - 5 * inputs.delta_seconds"}}
    context = IsolatedContext({"resources": {"heat": {"current": 100, "spec": {"initial": 100, "capacity": 100,
        "recovery_rule": cooling["id"], "recovery": {"mode": "periodic", "interval_seconds": 2}}}}},
        rule_definitions=preset()["rules"] + [cooling])
    context.session.add_system(context.resources.tick)
    context.session.advance(1)
    assert context.resources.current(context.ref, "heat") == 100
    context.session.advance(1)
    assert context.resources.current(context.ref, "heat") == 90


def test_periodic_recovery_freeze_retains_fractional_period_and_checkpoint():
    context = IsolatedContext({"runtime": {"casts": {}}, "resources": {"energy": {"current": 1,
        "spec": {"initial": 1, "capacity": 10, "recovery_rate": 1,
            "recovery": {"mode": "periodic", "interval_seconds": 1}, "parameters": {"freeze_while_cast": True}}}}}, quantum=1/30)
    context.session.add_system(context.resources.tick)
    context.session.advance(15)
    context.session.commit([Intent("set", context.ref, ("runtime", "casts"), {"cast/1": {"ability": "test"}})])
    context.session.advance(60)
    assert context.get(context.ref, ("resources", "energy", "timing", "elapsed_units")) == 15
    checkpoint = context.session.checkpoint()
    context.session.commit([Intent("set", context.ref, ("runtime", "casts"), {})])
    context.session.advance(15)
    assert context.resources.current(context.ref, "energy") == 2
    assert context.get(context.ref, ("resources", "energy", "timing", "elapsed_units")) == 0
    context.session.restore(checkpoint)
    context.session.commit([Intent("set", context.ref, ("runtime", "casts"), {})])
    context.session.advance(15)
    assert context.resources.current(context.ref, "energy") == 2


def test_pause_at_full_is_explicit_and_pauses_timer():
    context = IsolatedContext({"resources": {"heat": {"current": 10, "spec": {"initial": 10, "capacity": 10,
        "recovery_rate": -1, "recovery": {"mode": "periodic", "interval_seconds": 2}, "parameters": {"pause_at_full": True}}}}})
    context.session.add_system(context.resources.tick)
    context.session.advance(5)
    assert context.resources.current(context.ref, "heat") == 10
    assert context.get(context.ref, ("resources", "heat", "timing")) is None
    context.resources.adjust(context.ref, "heat", -1)
    context.session.advance(2)
    assert context.resources.current(context.ref, "heat") == 7


def test_declared_periodic_driver_uses_resource_parameters_without_top_level_rate():
    context = IsolatedContext({"resources": {"heat": {"current": 100, "spec": {"initial": 100, "capacity": 100,
        "recovery": {"mode": "periodic", "interval_seconds": 2}, "parameters": {"rate": -5}}}}})
    context.session.add_system(context.resources.tick)
    context.session.advance(2)
    assert context.resources.current(context.ref, "heat") == 90


@pytest.mark.parametrize("model", [{"mode": "unknown"}, {"mode": "periodic", "interval_seconds": 0},
    {"mode": "periodic", "interval_seconds": True}, {"mode": "periodic", "interval_seconds": 1, "hidden": 3},
    {"mode": "continuous", "interval_seconds": 1}])
def test_runtime_rejects_unsupported_or_invalid_recovery_models(model):
    context = IsolatedContext({"resources": {"energy": {"current": 1, "spec": {"initial": 1, "capacity": 10,
        "recovery_rate": 1, "recovery": model}}}})
    with pytest.raises(ValueError):
        context.resources.tick(context.session)


def test_attribute_memo_reuses_same_tick_but_observes_changed_view():
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}})
    assert context.attributes.value(context.ref, "atk") == 10
    original = context.last_calculation_event_id
    assert context.attributes.value(context.ref, "atk") == 10
    assert len(context.calls) == 1
    event = context.session.events[-1]
    assert event["type"] == "calculation.cached" and event["cause"] == original
    context.session.commit([Intent("set", context.ref, ("attributes", "base", "atk"), 20)])
    assert context.attributes.value(context.ref, "atk") == 20
    assert len(context.calls) == 2


def test_attribute_memo_clears_between_ticks_for_time_dependent_rules():
    rule = {"id": "custom/time_stat", "contract": "attributes.effective", "implementation": {"type": "provider", "provider": "custom/time_stat"}}
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}},
        rule_definitions=[rule], bindings={"attributes.effective": rule["id"]},
        providers={"custom/time_stat": lambda i, p, c: i["base"] + c["time"]})
    assert context.attributes.value(context.ref, "atk") == 10
    context.session.advance(1)
    assert context.attributes.value(context.ref, "atk") == 11


def test_attribute_memo_distinguishes_full_effect_scope_and_in_place_scope_changes():
    rules = [{"id": f"custom/{factor}", "contract": "attributes.effective", "parameters": {"factor": factor},
        "implementation": {"type": "provider", "provider": "custom/scaled"}} for factor in (2, 3)]
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}}, rule_definitions=rules,
        bindings={"attributes.effective": "custom/2"}, providers={"custom/scaled": lambda i, p, c: i["base"]*p["factor"]})
    effect = {"rules": {"attributes.effective": "custom/2"}}
    assert context.attributes.value(context.ref, "atk", effect=effect) == 20
    effect["rules"]["attributes.effective"] = "custom/3"
    assert context.attributes.value(context.ref, "atk", effect=effect) == 30


def test_attribute_memo_does_not_reuse_rolled_back_revision_branch():
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}})
    with pytest.raises(ValueError):
        with context.session.atomic():
            context.session.commit([Intent("set", context.ref, ("attributes", "base", "atk"), 0)])
            assert context.attributes.value(context.ref, "atk") == 0
            failed_revision = context.session.world.version(context.ref)
            raise ValueError("abort branch")
    context.session.commit([Intent("set", context.ref, ("attributes", "base", "atk"), 50)])
    assert context.session.world.version(context.ref) == failed_revision
    assert context.attributes.value(context.ref, "atk") == 50


def test_frozen_historical_attribute_memo_recalculates_after_event_rollback():
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}})
    history = context.session.world.get(context.ref)
    with pytest.raises(ValueError):
        with context.session.atomic():
            assert context.attributes.value(context.ref, "atk", snapshot=history) == 10
            raise ValueError("abort calc event")
    assert context.attributes.value(context.ref, "atk", snapshot=history) == 10
    assert len(context.calls) == 2
    assert context.attributes.value(context.ref, "atk", snapshot=history) == 10
    events = context.session.events
    assert events[-1]["type"] == "calculation.cached" and events[-1]["cause"] == events[-2]["id"]


def test_mutable_historical_attribute_snapshot_cannot_create_stale_memo():
    context = IsolatedContext({"attributes": {"base": {"atk": 10}}})
    history = thaw(context.session.world.get(context.ref))
    assert context.attributes.value(context.ref, "atk", snapshot=history) == 10
    history["components"]["attributes"]["base"]["atk"] = 25
    assert context.attributes.value(context.ref, "atk", snapshot=history) == 25
