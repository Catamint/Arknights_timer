"""Ability/Buff expectations using the real kernel, compiler and rule runtime."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ark_sim.content import Compiler
from ark_sim.contracts import thaw
from ark_sim.domains.abilities import AbilitySystem, ActivationRejected
from ark_sim.domains.attributes import AttributeSystem
from ark_sim.domains.buffs import BuffSystem
from ark_sim.domains.context import RuntimeContext
from ark_sim.domains.lifecycle import LifecycleSystem
from ark_sim.domains.resources import ResourceSystem
from ark_sim.kernel import Session


def _ability(**changes):
    return {"id": "ability/test", "kind": "ability", "activation": {"mode": "manual",
            "costs": [{"resource": "energy", "amount": 20}]}, "selector": "selector/target",
            "timeline": [{"at_seconds": 0.2, "repeat": {"count": 3, "interval_seconds": 0.2},
                          "effect": {"op": "damage", "target": "selected"}}], **changes}


def _buff(**changes):
    return {"id": "buff/test", "kind": "buff", "duration_seconds": 0.5,
            "stacking": {"mode": "refresh"},
            "modifiers": [{"attribute": "atk", "layer": "direct_ratio", "value": 0.5}], **changes}


def _context(abilities=None, buff=None, rules=None, lifecycle=False):
    abilities = abilities if abilities is not None else [_ability()]
    buff = buff or _buff()
    actor = {"id": "unit/test", "kind": "entity", "tags": ["player"], "components": {
        "attributes": {"base": {"atk": 100, "max_hp": 100, "attack_interval": 1, "attack_speed_ratio": 1}},
        "resources": {"hp": {"initial": 100, "capacity": 100, "role": "health"},
                      "energy": {"initial": 20, "capacity": 40}, "ammo": {"initial": 2, "capacity": 2}},
        "abilities": [item["id"] for item in abilities], "buffs": {"initial": []}}}
    if lifecycle:
        actor["components"]["lifecycle"] = {"policy": "policy/ark_lifecycle"}
    stack_rule = {"id": "rule/test_stack", "kind": "calculation_rule", "contract": "buff.stack_amount",
        "implementation": {"type": "expression", "expression":
            "min(max(inputs.current_stacks, inputs.incoming.count) if inputs.stack_parameters.mode == 'max' else "
            "inputs.current_stacks + inputs.incoming.count if inputs.stack_parameters.mode == 'add' else "
            "inputs.current_stacks if inputs.stack_parameters.mode == 'extend' and inputs.current_stacks > 0 else "
            "inputs.incoming.count, inputs.stack_parameters.max_stacks)"}}
    effective_rule = {"id": "rule/test_effective", "kind": "calculation_rule", "contract": "attributes.effective",
                      "implementation": {"type": "graph", "nodes": [{"id": "base", "expression": "inputs.base"}],
                                         "output": "nodes.base"}}
    package = {"schemaVersion": 2, "entities": [actor], "abilities": abilities, "buffs": [buff],
        "selectors": [{"id": "selector/target", "kind": "selector", "region": {"type": "all"}}],
        "rules": [stack_rule, effective_rule] + (rules or []), "scenarioDraft": {"id": "scenario/test",
            "ruleset": "ruleset/ark_standard", "rules": {"buff.stack_amount": "rule/test_stack", "attributes.effective": "rule/test_effective"},
            "dependencies": [buff["id"]], "initialEntities": [{"definition": "unit/test"}]}}
    program = Compiler().compile(package)
    session = Session(quantum=0.1)
    ctx = RuntimeContext(program, session)
    session.world.create("system/battle", {"state": {"finished": False}}, alias="system/battle")
    components = thaw(actor["components"])
    components["attributes"]["modifiers"] = []
    components["buffs"] = {"instances": []}
    components["runtime"] = {"alive": True, "state": "alive", "casts": {}, "cooldowns": {}, "next_attack": 0}
    components["resources"] = {key: {"current": value["initial"], "spec": value} for key, value in components["resources"].items()}
    source = session.world.create("unit/test", components, alias="source")
    target = session.world.create("unit/test", deepcopy(components), alias="target")
    ctx.attributes, ctx.resources = AttributeSystem(ctx), ResourceSystem(ctx)
    ctx.buffs, ctx.abilities = BuffSystem(ctx), AbilitySystem(ctx)
    if lifecycle:
        ctx.lifecycle = LifecycleSystem(ctx)
    ctx.selected = [target]
    ctx.spatial = SimpleNamespace(select=lambda *a, **kw: list(ctx.selected), blocked_by=lambda source: None)
    ctx.calls = []

    def execute(source, targets, effect, ability=None, cast=None, cause=None):
        if effect.get("target") in {"source", "self"}:
            targets = [source]
        ctx.calls.append({"time": session.time, "source": source, "targets": list(targets), "effect": deepcopy(effect),
                          "cast": deepcopy(cast), "cause": cause})
        for target in targets:
            if effect["op"] == "apply_buff":
                ctx.buffs.apply(source, target, effect["buff"])
            elif effect["op"] == "modify_resource":
                ctx.resources.adjust(target, effect["resource"], effect["delta"])

    ctx.effects = SimpleNamespace(execute=execute)
    for kind, handler in (("domain.ability.effect", ctx.abilities.handle_effect), ("domain.ability.finish", ctx.abilities.finish),
                          ("domain.buff.expire", ctx.buffs.expire), ("domain.buff.periodic", ctx.buffs.periodic),
                          ("event_reaction", ctx.react)):
        session.register_handler(kind, handler)
    ctx.source, ctx.target = source, target
    return ctx


def test_atomic_payment_and_three_relative_hits_in_order():
    ctx = _context()
    cast = ctx.abilities.start(ctx.source, "ability/test")
    assert ctx.resources.current(ctx.source, "energy") == 0
    record = ctx.get(ctx.source, ("runtime", "casts"))[cast]
    assert len(record["tasks"]) == 4
    ctx.session.advance(7)
    assert [call["time"] for call in ctx.calls] == [2, 4, 6]
    assert ctx.get(ctx.source, ("runtime", "casts")) == {}


def test_last_resource_insufficient_leaves_all_resources_casts_and_tasks_untouched():
    ability = _ability(activation={"mode": "manual", "costs": [{"resource": "energy", "amount": 10},
                                                              {"resource": "ammo", "amount": 3}]})
    ctx = _context([ability])
    world, tasks = ctx.session.world.snapshot(), ctx.session.scheduler.snapshot()
    with pytest.raises(ActivationRejected, match="insufficient resource"):
        ctx.abilities.start(ctx.source, ability["id"])
    assert ctx.session.world.snapshot() == world
    assert ctx.session.scheduler.snapshot() == tasks


def test_invalid_final_scheduled_time_rolls_back_costs_and_cast_creation():
    rule = {"id": "rule/negative_delay", "kind": "calculation_rule", "contract": "ability.windup",
            "implementation": {"type": "expression", "expression": "-1"}}
    ctx = _context([_ability(rules={"ability.windup": rule["id"]})], rules=[rule])
    world, tasks = ctx.session.world.snapshot(), ctx.session.scheduler.snapshot()
    with pytest.raises(ValueError, match="negative delay"):
        ctx.abilities.start(ctx.source, "ability/test")
    assert ctx.session.world.snapshot() == world and ctx.session.scheduler.snapshot() == tasks


def test_interrupt_cancels_remaining_repeat_tasks_and_checkpoint_repeats_result():
    ctx = _context()
    ctx.abilities.start(ctx.source, "ability/test")
    ctx.session.advance(3)
    assert [call["time"] for call in ctx.calls] == [2]
    checkpoint = ctx.session.checkpoint()
    assert ctx.abilities.interrupt(ctx.source, "retreat") == 1
    ctx.session.advance(5)
    assert [call["time"] for call in ctx.calls] == [2]
    ctx.session.restore(checkpoint)
    ctx.calls.clear()
    ctx.session.advance(5)
    assert [call["time"] for call in ctx.calls] == [4, 6]


def test_each_hit_target_capture_and_at_cast_source_snapshot():
    ctx = _context([_ability(target_capture="each_hit")])
    ctx.abilities.start(ctx.source, "ability/test")
    ctx.set(ctx.source, ("attributes", "base", "atk"), 200)
    ctx.session.advance(3)
    ctx.selected = [ctx.source]
    ctx.session.advance(2)
    assert ctx.calls[0]["targets"] == [ctx.target]
    assert ctx.calls[1]["targets"] == [ctx.source]
    assert ctx.calls[0]["cast"]["source_snapshot"]["components"]["attributes"]["base"]["atk"] == 100


def test_nonblocking_manual_buff_can_overlap_ordinary_attack():
    basic = _ability(id="ability/basic", activation={"mode": "automatic_attack"})
    support = _ability(id="ability/support", parameters={"blocks_attacks": False},
            timeline=[{"at_seconds": 0, "effect": {"op": "apply_buff", "target": "self", "buff": "buff/test"}}])
    ctx = _context([basic, support])
    ctx.abilities.start(ctx.source, support["id"])
    ctx.abilities.start(ctx.source, basic["id"], automatic=True)
    ctx.session.advance(3)
    assert any(call["effect"]["op"] == "apply_buff" for call in ctx.calls)
    assert any(call["effect"]["op"] == "damage" for call in ctx.calls)


def test_passive_uses_event_payload_condition_and_queued_reaction():
    passive = _ability(activation={"mode": "passive", "event": "poke", "condition": "inputs.payload.value == 7"},
                      timeline=[{"at_seconds": 0, "effect": {"op": "emit", "event": "noted"}}])
    ctx = _context([passive])
    # Both actors share definitions; this test deliberately observes two listeners.
    ctx.emit("poke", {"value": 0})
    ctx.session.advance(1)
    assert not ctx.calls
    ctx.emit("poke", {"value": 7})
    assert not ctx.calls
    ctx.session.advance(1)
    assert len(ctx.calls) == 2


def test_refresh_and_periodic_buff_are_half_open_and_modifiers_cleanup():
    ctx = _context(buff=_buff(interval_seconds=0.2, effects=[{"op": "modify_resource", "resource": "energy", "delta": 1}]))
    uid = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    assert ctx.get(ctx.target, ("attributes", "modifiers"))[0]["buff_instance"] == uid
    ctx.session.advance(3)
    assert ctx.resources.current(ctx.target, "energy") == 21
    assert ctx.buffs.apply(ctx.source, ctx.target, "buff/test") == uid
    ctx.session.advance(6)
    assert ctx.resources.current(ctx.target, "energy") == 23
    assert ctx.get(ctx.target, ("buffs", "instances")) == []
    assert ctx.get(ctx.target, ("attributes", "modifiers")) == []


@pytest.mark.parametrize("mode,incoming,expected", [("add", 2, 3), ("max", 2, 2), ("extend", 2, 1), ("refresh", 2, 2)])
def test_stacking_modes_have_independent_expected_amounts_and_timers(mode, incoming, expected):
    ctx = _context(buff=_buff(stacking={"mode": mode, "max_stacks": 3}))
    first = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    ctx.session.advance(2)
    assert ctx.buffs.apply(ctx.source, ctx.target, "buff/test", incoming) == first
    instance = ctx.get(ctx.target, ("buffs", "instances"))[0]
    assert instance["stacks"] == expected
    assert instance["expires_at"] == (10 if mode == "extend" else 7)


def test_independent_buff_instances_remove_only_selected_instance():
    ctx = _context(buff=_buff(stacking={"mode": "independent"}))
    first = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    second = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    assert first != second
    assert ctx.buffs.remove(ctx.target, first) == 1
    assert [instance["id"] for instance in ctx.get(ctx.target, ("buffs", "instances"))] == [second]
    assert ctx.buffs.remove(ctx.target, "buff/test") == 1


def test_buff_event_subscription_condition_and_identity_source_distinction():
    buff = _buff(events=[{"event": "poke", "condition": "inputs.payload.target == context.owner.id",
                         "effects": [{"op": "modify_resource", "resource": "energy", "delta": 2}]}])
    ctx = _context(buff=buff)
    first = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    second = ctx.buffs.apply(ctx.target, ctx.target, "buff/test")
    assert first != second
    ctx.emit("poke", {"target": ctx.target})
    ctx.session.advance(1)
    assert ctx.resources.current(ctx.target, "energy") == 24


def test_auto_replace_readiness_uses_actual_cost_rule_not_raw_cost_amount():
    rule = {"id": "rule/half_cost", "kind": "calculation_rule", "contract": "resource.cost",
            "implementation": {"type": "expression", "expression": "inputs.cost_parameters.amount / 2"}}
    basic = _ability(id="ability/basic", activation={"mode": "automatic_attack"})
    charged = _ability(id="ability/charged", activation={"mode": "manual",
        "costs": [{"resource": "energy", "amount": 20, "rule": rule["id"]}],
        "parameters": {"auto_when_ready": True, "replace_attack": True}})
    ctx = _context([basic, charged], rules=[rule])
    ctx.set(ctx.target, ("abilities",), [])
    ctx.resources.adjust(ctx.source, "energy", value=10)
    ctx.abilities.tick(ctx.session)
    casts = list(ctx.get(ctx.source, ("runtime", "casts")).values())
    assert len(casts) == 1 and casts[0]["ability"] == "ability/charged"
    assert ctx.resources.current(ctx.source, "energy") == 0
    assert ctx.get(ctx.source, ("runtime", "next_attack")) == 10
    assert casts[0]["target_snapshots"][str(ctx.target)]["id"] == ctx.target


def test_auto_manual_readiness_uses_actual_cost_rule_not_raw_cost_amount():
    rule = {"id": "rule/half_cost", "kind": "calculation_rule", "contract": "resource.cost",
            "implementation": {"type": "expression", "expression": "inputs.cost_parameters.amount / 2"}}
    ability = _ability(activation={"mode": "manual", "costs": [{"resource": "energy", "amount": 20, "rule": rule["id"]}],
                                  "parameters": {"auto_when_ready": True}})
    ctx = _context([ability], rules=[rule])
    ctx.set(ctx.target, ("abilities",), [])
    ctx.resources.adjust(ctx.source, "energy", value=10)
    ctx.abilities.tick(ctx.session)
    assert ctx.resources.current(ctx.source, "energy") == 0
    assert len(ctx.get(ctx.source, ("runtime", "casts"))) == 1


def test_replace_interval_failure_preserves_payment_casts_and_all_tasks():
    rule = {"id": "rule/zero_interval", "kind": "calculation_rule", "contract": "time.interval",
            "implementation": {"type": "expression", "expression": "0"}}
    ability = _ability(parameters={"replace_attack": True}, rules={"time.interval": rule["id"]})
    ctx = _context([ability], rules=[rule])
    before_world, before_tasks = ctx.session.world.snapshot(), ctx.session.scheduler.snapshot()
    with pytest.raises(ValueError, match="interval must advance"):
        ctx.abilities.start(ctx.source, ability["id"], automatic=True)
    assert ctx.session.world.snapshot() == before_world
    assert ctx.session.scheduler.snapshot() == before_tasks


def test_charged_cost_rejection_falls_back_to_basic_attack_without_skipping_tick():
    basic = _ability(id="ability/basic", activation={"mode": "automatic_attack"})
    charged = _ability(id="ability/charged", activation={"mode": "manual", "costs": [{"resource": "ammo", "amount": 3}],
                       "parameters": {"auto_when_ready": True, "replace_attack": True}})
    ctx = _context([basic, charged])
    ctx.set(ctx.target, ("abilities",), [])
    ctx.abilities.tick(ctx.session)
    casts = list(ctx.get(ctx.source, ("runtime", "casts")).values())
    assert len(casts) == 1 and casts[0]["ability"] == "ability/basic"
    assert ctx.resources.current(ctx.source, "ammo") == 2


def test_deployment_activation_reacts_only_for_the_deployed_owner():
    ability = _ability(activation={"mode": "on_deploy"},
                       timeline=[{"at_seconds": 0, "effect": {"op": "emit", "event": "ready"}}])
    ctx = _context([ability])
    ctx.emit("entity.deployed", {"source": ctx.source})
    ctx.session.advance(1)
    assert len(ctx.calls) == 1 and ctx.calls[0]["source"] == ctx.source


def test_custom_duration_rule_changes_expiration_without_editing_buff_system():
    rule = {"id": "rule/short_duration", "kind": "calculation_rule", "contract": "buff.duration",
            "implementation": {"type": "expression", "expression": "0.2"}}
    ctx = _context(buff=_buff(duration_rule=rule["id"]), rules=[rule])
    ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    ctx.session.advance(2)
    assert ctx.get(ctx.target, ("buffs", "instances"))
    ctx.session.advance(1)
    assert not ctx.get(ctx.target, ("buffs", "instances"))


def test_target_death_cleanup_policy_is_explicit_and_customizable():
    ctx = _context()
    ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    ctx.set(ctx.target, ("runtime", "alive"), False)
    ctx.emit("entity.died", {"target": ctx.target})
    ctx.session.advance(1)
    assert not ctx.get(ctx.target, ("buffs", "instances"))
    retained = _context(buff=_buff(removal={"on_target_death": "retain"}))
    retained.buffs.apply(retained.source, retained.target, "buff/test")
    retained.set(retained.target, ("runtime", "alive"), False)
    retained.emit("entity.died", {"target": retained.target})
    retained.session.advance(1)
    assert retained.get(retained.target, ("buffs", "instances"))


def test_refresh_preserves_instance_blackboard_and_expiry_checkpoint_replays():
    ctx = _context()
    uid = ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    items = ctx.get(ctx.target, ("buffs", "instances"))
    items[0]["blackboard"]["triggers"] = 4
    ctx.set(ctx.target, ("buffs", "instances"), items)
    ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    assert ctx.get(ctx.target, ("buffs", "instances"))[0]["blackboard"] == {"triggers": 4}
    checkpoint = ctx.session.checkpoint()
    ctx.session.advance(6)
    expected = ctx.session.checkpoint()
    ctx.session.restore(checkpoint)
    ctx.session.advance(6)
    assert ctx.session.checkpoint() == expected
    assert ctx.buffs.remove(ctx.target, uid) == 0


def test_ability_event_effects_use_owner_condition_and_run_after_emit():
    ability = _ability(events=[{"event": "poke", "condition": "inputs.payload.source == context.owner.id",
                               "effects": [{"op": "modify_resource", "target": "self", "resource": "energy", "delta": 2}]}])
    ctx = _context([ability])
    ctx.emit("poke", {"source": ctx.source})
    assert ctx.resources.current(ctx.source, "energy") == 20
    ctx.session.advance(1)
    assert ctx.resources.current(ctx.source, "energy") == 22
    assert ctx.resources.current(ctx.target, "energy") == 20


def test_missing_buff_source_cleans_instance_and_tasks_without_dangling_reaction():
    ctx = _context(buff=_buff(interval_seconds=0.2))
    ctx.buffs.apply(ctx.source, ctx.target, "buff/test")
    ctx.session.world.delete(ctx.source)
    ctx.emit("entity.removed", {"source": ctx.source})
    ctx.session.advance(1)
    assert ctx.get(ctx.target, ("buffs", "instances")) == []
    assert all(task["kind"] not in {"domain.buff.periodic", "domain.buff.expire"} for task in ctx.session.scheduler.pending)


def test_hp_sacrifice_cost_immediately_checks_real_lifecycle_and_interrupts_cast():
    ability = _ability(activation={"mode": "manual", "costs": [{"resource": "hp", "amount": 100}]})
    ctx = _context([ability], lifecycle=True)
    ctx.abilities.start(ctx.source, ability["id"])
    assert ctx.resources.current(ctx.source, "hp") == 0
    assert ctx.alive(ctx.source) is False
    assert ctx.get(ctx.source, ("runtime", "casts")) == {}
    paid = [event for event in ctx.session.events if event["type"] == "resource.changed"
            and event["payload"].get("reason") == "ability_cost"]
    assert len(paid) == 1 and paid[0]["payload"]["delta"] == -100
    ctx.session.advance(7)
    assert ctx.calls == []


def test_payment_resource_event_activates_passive_after_commit_once_per_resource():
    payer = _ability(activation={"mode": "manual", "costs": [{"resource": "energy", "amount": 5},
                                                             {"resource": "energy", "amount": 5}]})
    passive = _ability(id="ability/passive", activation={"mode": "passive", "event": "resource.changed",
                       "condition": "inputs.payload.target == context.source.id and inputs.payload.reason == 'ability_cost'"},
                       parameters={"blocks_attacks": False},
                       timeline=[{"at_seconds": 0, "effect": {"op": "emit", "event": "cost_seen"}}])
    ctx = _context([payer, passive])
    ctx.abilities.start(ctx.source, payer["id"])
    assert ctx.resources.current(ctx.source, "energy") == 10
    assert ctx.calls == []
    ctx.session.advance(1)
    assert len(ctx.calls) == 1 and ctx.calls[0]["effect"]["event"] == "cost_seen"
    costs = [event for event in ctx.session.events if event["type"] == "resource.changed"
             and event["payload"].get("reason") == "ability_cost"]
    assert len(costs) == 1 and costs[0]["payload"]["delta"] == -10


def test_postpayment_lifecycle_rule_failure_rolls_back_full_start_transaction():
    ctx = _context()
    ctx.lifecycle = SimpleNamespace(check=lambda *args: (_ for _ in ()).throw(ValueError("bad lifecycle rule")))
    before = ctx.session.checkpoint()
    with pytest.raises(ValueError, match="bad lifecycle rule"):
        ctx.abilities.start(ctx.source, "ability/test")
    assert ctx.session.checkpoint() == before


def test_timeline_logic_offset_four_hits_at_tick_four_under_point_one_quantum():
    ability = _ability(timeline=[{"at": 4, "effect": {"op": "damage", "target": "selected"}}])
    ctx = _context([ability])
    ctx.abilities.start(ctx.source, ability["id"])
    ctx.session.advance(4)
    assert ctx.calls == []
    ctx.session.advance(1)
    assert [call["time"] for call in ctx.calls] == [4]


def test_timeline_conflicting_logic_and_seconds_offsets_are_explicitly_rejected():
    ability = _ability(timeline=[{"at": 4, "at_seconds": 0.4, "effect": {"op": "damage", "target": "selected"}}])
    with pytest.raises(ValueError, match="either at or at_seconds|mutually exclusive"):
        ctx = _context([ability])
        before = ctx.session.checkpoint()
        try:
            ctx.abilities.start(ctx.source, ability["id"])
        except ValueError:
            assert ctx.session.checkpoint() == before
            raise
