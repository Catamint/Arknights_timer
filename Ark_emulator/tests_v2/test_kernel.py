"""Independent expectations for the generic kernel's consistency guarantees."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from ark_sim.contracts.models import Intent, thaw, freeze
from ark_sim.kernel import RandomStreams, ReactionBudgetExceeded, Session, World


def test_arbitrary_json_components_read_only_and_input_detached():
    world = World()
    components = {"fuel": {"current": -10, "marks": [1, 2]}}
    entity = world.create("user/submarine", components, tags=["vehicle", "vehicle"], alias="sub")
    components["fuel"]["marks"].append(3)
    assert entity == 1
    assert world.resolve("sub") == 1
    assert world.get(entity)["components"]["fuel"]["current"] == -10
    assert world.get(entity)["components"]["fuel"]["marks"] == (1, 2)
    with pytest.raises(TypeError):
        world.get(entity)["components"]["fuel"]["current"] = 0
    snapshot = world.snapshot()
    snapshot["entities"][0]["components"]["fuel"]["current"] = 42
    assert world.get(entity)["components"]["fuel"]["current"] == -10


def test_aliases_unique_deleted_and_local_ids_never_reused():
    world = World()
    first = world.create("unit/a", {}, alias="a")
    before = world.snapshot()
    with pytest.raises(ValueError, match="Alias already exists"):
        world.create("unit/b", {}, alias="a")
    assert world.snapshot() == before
    world.delete("a")
    with pytest.raises(KeyError):
        world.resolve("a")
    assert world.create("unit/b", {}, alias="a") == first + 1
    assert World().create("unit/c", {}) == 1


def test_half_open_interval_and_stable_phase_priority_sequence_order():
    session = Session(quantum=0.25)
    execution = []
    session.register_handler("record", lambda s, payload: execution.append((s.time, payload)))
    session.add_system(lambda s: execution.append((s.time, "system")), phase=0)
    session.schedule("record", "low", at=0, phase=1, priority=4)
    session.schedule("record", "first", at=0, phase=1, priority=-1)
    session.schedule("record", "second", at=0, phase=1, priority=-1)
    session.schedule("record", "end", at=2, phase=1)
    assert session.advance(2) == 2
    assert execution == [(0, "system"), (0, "first"), (0, "second"), (0, "low"), (1, "system")]
    assert session.clock.seconds == 0.5
    session.advance(1)
    assert execution[-2:] == [(2, "system"), (2, "end")]


def test_named_phase_order_reactions_and_cancellation():
    session = Session(phase_order=["command", "simulation", "reaction"])
    execution = []

    def handler(s, payload):
        execution.append(payload)
        if payload == "root":
            s.schedule("record", "child", at=s.time, phase="reaction")

    session.register_handler("record", handler)
    task = session.schedule("record", "cancelled", at=0, phase="reaction")
    session.cancel(task)
    session.schedule("record", "root", at=0, phase="command")
    session.add_system(lambda s: execution.append("system"), phase="simulation")
    session.advance(1)
    assert execution == ["root", "system", "child"]
    with pytest.raises(ValueError, match="past"):
        session.schedule("record", "past", at=0, phase="command")


def test_intent_atomic_batch_creation_alias_set_and_complete_component_replacement():
    session = Session()
    results = session.commit([
        Intent("create", data={"definition_id": "custom/object", "components": {"flags": {}}, "alias": "new"}),
        Intent("set", "new", ("flags", "value"), 17),
        Intent("set", "new", ("fuel",), {"current": 20}),
        Intent("emit", data={"type": "created", "payload": {"alias": "new"}}),
        Intent("schedule", data={"kind": "next", "payload": {"entity": "new"}, "at": 4}),
    ])
    assert results == (1, None, None, 1, 1)
    assert thaw(session.world.get("new")["components"]) == {"flags": {"value": 17}, "fuel": {"current": 20}}
    assert session.events[0]["type"] == "created"
    assert session.scheduler.pending[0]["at"] == 4


def test_invalid_last_intent_rolls_back_entities_events_tasks_ids_and_cancellations():
    session = Session()
    entity = session.world.create("user/object", {"v": 1}, alias="keep")
    keep_task = session.schedule("nothing", {}, at=10)
    session.emit("old", {})
    before = session.checkpoint()
    with pytest.raises(KeyError):
        session.commit([
            Intent("create", data={"definition_id": "user/new", "components": {}, "alias": "staged"}),
            Intent("set", entity, ("v",), 99),
            Intent("emit", data={"type": "staged", "payload": {}, "cause": 1}),
            Intent("schedule", data={"kind": "staged", "at": 12}),
            Intent("cancel", data={"task_id": keep_task}),
            Intent("delete", "keep"),
            Intent("set", "absent", ("v",), 100),
        ])
    assert session.checkpoint() == before
    assert session.commit([Intent("create", data={"definition_id": "user/new", "components": {}})]) == (2,)
    assert session.emit("after", {}, cause=1) == 2
    assert session.schedule("after", {}, at=20) == 2


def test_infinite_same_time_reaction_fails_with_cause_and_can_restore():
    session = Session(reaction_budget=3)
    session.register_handler("loop", lambda s, p: s.schedule("loop", {}, at=s.time))
    session.schedule("loop", {}, at=0)
    initial = session.checkpoint()
    with pytest.raises(ReactionBudgetExceeded, match="time 0.*loop"):
        session.advance(1)
    assert session.time == 0
    with pytest.raises(RuntimeError, match="execution has failed"):
        session.advance(1)
    session.restore(initial)
    session.cancel(1)
    session.advance(1)
    assert session.time == 1


def test_retrograde_current_phase_is_rejected_and_batch_is_atomic():
    session = Session()
    entity = session.world.create("x", {"count": 0})

    def handler(s, p):
        before = s.world.snapshot()
        with pytest.raises(ValueError, match="cannot precede"):
            s.commit([Intent("set", entity, ("count",), 1),
                      Intent("schedule", data={"kind": "earlier", "at": s.time, "phase": 0})])
        assert s.world.snapshot() == before

    session.register_handler("late", handler)
    session.schedule("late", {}, at=0, phase=1)
    session.advance(1)
    assert session.world.get(entity)["components"]["count"] == 0


def test_rng_streams_independent_with_fixed_algorithm_sequence():
    random = RandomStreams(seed=9)
    reference = [random.sample("weapon") for _ in range(3)]
    other = RandomStreams(seed=9)
    actual = []
    for _ in range(3):
        other.sample("cosmetic")
        actual.append(other.sample("weapon"))
    assert reference == actual
    assert [sample["index"] for sample in other.samples if sample["stream"] == "weapon"] == [1, 2, 3]
    # Independently generated from SHA256(b'[9,"weapon"]') = 33c73303...ea070f4.
    assert reference == [0.7830382814154855, 0.8561124546405428, 0.29375342231087875]


def test_json_checkpoint_restores_pending_tasks_rng_events_and_sequences():
    session = Session(seed=53)
    entity = session.world.create("generic/counter", {"sum": 0}, alias="counter")

    def tick(s):
        current = s.world.get(entity)["components"]["sum"]
        s.commit([Intent("set", entity, ("sum",), current + s.random.sample("gain"))])

    session.add_system(tick)
    session.register_handler("note", lambda s, p: s.emit("note", p))
    session.schedule("note", {"mark": 1}, at=2, phase=1)
    cancelled = session.schedule("note", {"mark": 2}, at=3, phase=1)
    session.cancel(cancelled)
    session.advance(2)
    checkpoint = json.loads(json.dumps(session.checkpoint(), allow_nan=False))
    session.advance(4)
    expected = session.snapshot()
    session.restore(checkpoint)
    session.advance(4)
    assert session.snapshot() == expected
    assert len(session.events) == 1
    assert session.events[0]["payload"]["mark"] == 1


def test_restore_invalid_last_store_is_atomic():
    session = Session(seed=1)
    session.world.create("generic", {"v": 1})
    session.random.sample("source")
    before = session.checkpoint()
    broken = json.loads(json.dumps(before))
    broken["world"]["entities"][0]["components"]["v"] = 55
    broken["random"]["samples"][0]["index"] = 100
    with pytest.raises(ValueError, match="consumption index"):
        session.restore(broken)
    assert session.checkpoint() == before


def test_concurrent_commits_are_serialized_and_no_ids_collide():
    session = Session()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: session.commit([
            Intent("create", data={"definition_id": "custom", "components": {"worker": i}, "alias": f"worker-{i}"}),
            Intent("emit", data={"type": "worker", "payload": {"worker": i}}),
        ]), range(50)))
    assert sorted(result[0] for result in results) == list(range(1, 51))
    assert sorted(result[1] for result in results) == list(range(1, 51))
    assert len(session.world.entities()) == 50
    assert len(session.events) == 50


def test_snapshot_inside_callback_and_external_commit_waits_for_executor():
    session = Session()
    callback_entered = threading.Event()
    callback_can_end = threading.Event()
    transaction_done = threading.Event()

    def handler(s, payload):
        assert s.snapshot()["time"] == 0
        callback_entered.set()
        assert callback_can_end.wait(2)

    session.register_handler("wait", handler)
    session.schedule("wait", {}, at=0)
    advance_thread = threading.Thread(target=lambda: session.advance(1))
    advance_thread.start()
    assert callback_entered.wait(2)

    def transaction():
        session.commit([Intent("create", data={"definition_id": "external", "components": {}})])
        transaction_done.set()

    transaction_thread = threading.Thread(target=transaction)
    transaction_thread.start()
    assert not transaction_done.wait(0.03)
    callback_can_end.set()
    advance_thread.join(2)
    transaction_thread.join(2)
    assert transaction_done.is_set()
    assert session.time == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_values_never_reach_stores(value):
    session = Session()
    before = session.checkpoint()
    with pytest.raises((ValueError, TypeError)):
        session.commit([Intent("create", data={"definition_id": "invalid", "components": {"value": value}})])
    assert session.checkpoint() == before


@pytest.mark.parametrize("amount", [-1, 0.5, True])
def test_logic_time_requires_non_negative_integer_units(amount):
    session = Session()
    with pytest.raises(ValueError):
        session.advance(amount)
    assert session.time == 0


def test_snapshot_clone_does_not_mutate_event_or_pending_task_payload():
    session = Session()
    session.emit("state", {"values": [1]})
    session.schedule("state", {"values": [2]}, at=4)
    snapshot = session.snapshot()
    snapshot["events"]["records"][0]["payload"]["values"].append(99)
    snapshot["scheduler"]["tasks"][0]["payload"]["values"].append(99)
    assert session.events[0]["payload"]["values"] == (1,)
    assert session.scheduler.pending[0]["payload"]["values"] == (2,)


def test_restore_into_another_session_retains_registered_implementations():
    session = Session(seed=7)
    session.register_handler("spawn", lambda s, p: s.world.create(p["definition"], {}))
    session.add_system(lambda s: s.emit("tick", {"sample": s.random.sample("tick")}))
    session.schedule("spawn", {"definition": "custom/object"}, at=3, phase=1)
    session.advance(2)
    checkpoint = session.checkpoint()
    other = Session()
    other.register_handler("spawn", lambda s, p: s.world.create(p["definition"], {}))
    other.add_system(lambda s: s.emit("tick", {"sample": s.random.sample("tick")}))
    other.restore(json.loads(json.dumps(checkpoint)))
    session.advance(3)
    other.advance(3)
    assert other.snapshot() == session.snapshot()


def test_snapshot_rejects_broken_task_payload_before_adopting_any_store():
    session = Session()
    session.world.create("generic", {"v": 1})
    session.schedule("next", {"payload": True}, at=2)
    before = session.checkpoint()
    invalid = json.loads(json.dumps(before))
    invalid["world"]["entities"][0]["components"]["v"] = 50
    del invalid["scheduler"]["tasks"][0]["payload"]
    with pytest.raises(ValueError, match="required fields"):
        session.restore(invalid)
    assert session.checkpoint() == before


def test_mid_callback_read_snapshot_cannot_be_restored_as_checkpoint():
    session = Session()
    observed = []
    session.add_system(lambda s: observed.append(s.snapshot()))
    session.advance(1)
    before = session.checkpoint()
    with pytest.raises(ValueError, match="between advance calls"):
        session.restore(observed[0])
    assert session.checkpoint() == before


class SequenceBackend:
    algorithm = "custom/sequence"
    version = "1"
    configuration = {"values": [0.125, 0.5, 0.875]}

    def __init__(self, seed):
        self.index = 0

    def sample(self):
        value = self.configuration["values"][self.index % 3]
        self.index += 1
        return value

    def snapshot(self):
        return {"index": self.index}

    def restore(self, data):
        if type(data["index"]) is not int or data["index"] < 0:
            raise ValueError("invalid sequence index")
        self.index = data["index"]


def test_user_random_factory_named_streams_sequence_and_checkpoint_replay():
    session = Session(seed=9, random_factory=SequenceBackend)
    assert session.random.algorithm == "custom/sequence"
    assert session.random.version == "1"
    assert [session.random.sample("weapon") for _ in range(2)] == [0.125, 0.5]
    assert session.random.sample("weather") == 0.125
    checkpoint = json.loads(json.dumps(session.checkpoint()))
    assert checkpoint["random"]["fingerprint"] == session.random.fingerprint
    continuation = [session.random.sample("weapon") for _ in range(4)]
    expected = session.checkpoint()
    assert continuation == [0.875, 0.125, 0.5, 0.875]
    restored = Session(random_factory=SequenceBackend)
    restored.restore(checkpoint)
    assert [restored.random.sample("weapon") for _ in range(4)] == continuation
    assert restored.checkpoint() == expected


def test_custom_rng_restore_requires_matching_registered_algorithm_and_version():
    custom = Session(random_factory=SequenceBackend)
    custom.random.sample("example")
    checkpoint = custom.checkpoint()
    ordinary = Session()
    before = ordinary.checkpoint()
    with pytest.raises(ValueError, match="Unknown random algorithm"):
        ordinary.restore(checkpoint)
    assert ordinary.checkpoint() == before
    checkpoint["random"]["version"] = "2"
    with pytest.raises(ValueError, match="version"):
        custom.restore(checkpoint)


def test_rng_registry_selects_algorithm_and_fingerprint_change_is_rejected():
    registry = {"custom/sequence": {"algorithm": "custom/sequence", "version": "1",
                                    "factory": SequenceBackend, "configuration": {"variant": "a"}}}
    session = Session(random_registry=registry, random_algorithm="custom/sequence")
    assert session.random.sample("registered") == 0.125
    checkpoint = session.checkpoint()
    changed = Session(random_registry={"custom/sequence": {
        **registry["custom/sequence"], "configuration": {"variant": "b"}}},
        random_algorithm="custom/sequence")
    before = changed.checkpoint()
    with pytest.raises(ValueError, match="fingerprint"):
        changed.restore(checkpoint)
    assert changed.checkpoint() == before


def test_registered_rng_can_be_selected_from_checkpoint_without_default_selection():
    source = Session(random_factory=SequenceBackend)
    source.random.sample("a")
    target = Session(random_registry={"custom/sequence": SequenceBackend})
    assert target.random.algorithm == RandomStreams.ALGORITHM
    target.restore(source.checkpoint())
    assert target.random.algorithm == "custom/sequence"
    assert target.random.sample("a") == 0.5


@pytest.mark.parametrize("attribute", ["sample", "snapshot", "restore"])
def test_rng_backend_contract_rejects_missing_method(attribute):
    broken = type("BrokenBackend", (SequenceBackend,), {attribute: None})
    session = Session(random_factory=broken)
    with pytest.raises(ValueError, match="required method"):
        session.random.sample("bad")
    assert session.random.samples == ()


def test_bad_rng_output_rolls_back_provider_state_and_consumption_log():
    class BadBackend(SequenceBackend):
        algorithm = "custom/bad"

        def sample(self):
            self.index += 1
            return True

    session = Session(random_factory=BadBackend)
    with pytest.raises(ValueError, match="finite and in"):
        session.random.sample("bad")
    assert session.random.samples == ()
    assert session.random.snapshot()["streams"]["bad"] == {"state": {"index": 0}, "count": 0}


def test_rng_backend_metadata_and_unknown_selection_are_rejected():
    with pytest.raises(ValueError, match="Unknown random algorithm"):
        Session(random_algorithm="unregistered")
    def incomplete_factory(seed):
        return SequenceBackend(seed)

    incomplete_factory.algorithm = "custom/sequence"
    with pytest.raises(ValueError, match="version"):
        Session(random_factory=incomplete_factory)
    session = Session(random_registry={"declared": {"algorithm": "declared", "version": "1",
                                                   "factory": SequenceBackend}}, random_algorithm="declared")
    with pytest.raises(ValueError, match="disagrees"):
        session.random.sample("bad")


def test_long_event_history_retains_payload_isolation_and_transaction_rollback():
    session = Session()
    for number in range(1000):
        session.emit("history", {"number": number})
    before = session.checkpoint()
    payload = {"values": [1]}
    with pytest.raises(ValueError, match="Unknown intent operation"):
        session.commit([Intent("emit", data={"type": "staged", "payload": payload}), Intent("invalid")])
    assert session.checkpoint() == before
    result = session.commit([Intent("emit", data={"type": "next", "payload": payload, "cause": 1000})])
    payload["values"].append(2)
    assert result == (1001,)
    assert session.events[-1]["payload"]["values"] == (1,)
    assert session.events[0]["payload"]["number"] == 0


def test_atomic_across_commits_emits_schedules_cancels_rng_and_alias_failure():
    session = Session(seed=41)
    entity = session.world.create("generic", {"energy": 20}, alias="taken")
    task = session.schedule("keep", {}, at=4)
    session.emit("history", {"v": 1})
    session.random.sample("existing")
    before = session.checkpoint()
    with pytest.raises(ValueError, match="Alias already exists"):
        with session.atomic():
            session.commit([Intent("set", entity, ("energy",), 10)])
            session.emit("payment", {"value": 10}, cause=1)
            session.cancel(task)
            session.schedule("temporary", {}, at=3)
            session.random.sample("existing")
            session.random.sample("new")
            session.world.create("generic", {}, alias="new")
            session.world.create("generic", {}, alias="taken")
    assert session.checkpoint() == before
    assert session.world.create("generic", {}) == 2
    assert session.schedule("after", {}, at=9) == 2
    assert session.emit("after", {}) == 2


def test_atomic_rule_exception_restores_custom_random_provider_and_clock():
    session = Session(random_factory=SequenceBackend)
    assert session.random.sample("a") == 0.125
    before = session.checkpoint()
    with pytest.raises(ValueError, match="rule rejected"):
        with session.atomic():
            session.random.sample("a")
            session.random.sample("b")
            session.emit("temporary", {})
            session.clock.time = 5
            session.clock.quantum = 0.5
            raise ValueError("rule rejected")
    assert session.checkpoint() == before
    assert session.random.sample("a") == 0.5


def test_atomic_nested_savepoints_commit_outer_and_roll_back_inner():
    session = Session()
    entity = session.world.create("generic", {"v": 0})
    with session.atomic():
        session.commit([Intent("set", entity, ("v",), 1)])
        session.emit("outer", {})
        try:
            with session.atomic():
                session.commit([Intent("set", entity, ("v",), 2)])
                session.emit("inner", {})
                raise ValueError("inner rejected")
        except ValueError:
            pass
        assert session.world.get(entity)["components"]["v"] == 1
        session.emit("outer_accepted", {})
    assert session.world.get(entity)["components"]["v"] == 1
    assert [event["type"] for event in session.events] == ["outer", "outer_accepted"]
    assert [event["id"] for event in session.events] == [1, 2]
    before = session.checkpoint()
    with pytest.raises(ValueError):
        with session.atomic():
            with session.atomic():
                session.world.create("generic", {})
            raise ValueError("outer rejected")
    assert session.checkpoint() == before


def test_atomic_inside_handler_preserves_active_phase_after_rollback():
    session = Session()
    entity = session.world.create("generic", {"v": 0})

    def handler(s, payload):
        active = s._active_key
        with pytest.raises(ValueError, match="command invalid"):
            with s.atomic():
                s.commit([Intent("set", entity, ("v",), 1)])
                s.emit("temporary", {})
                s.schedule("temporary", {}, at=s.time, phase=2)
                raise ValueError("command invalid")
        assert s._active_key == active
        with pytest.raises(ValueError, match="cannot precede"):
            s.schedule("early", {}, at=s.time, phase=0)
        s.emit("command.rejected", {})

    session.register_handler("command", handler)
    session.schedule("command", {}, at=0, phase=2)
    session.advance(1)
    assert session.world.get(entity)["components"]["v"] == 0
    assert [event["type"] for event in session.events] == ["command.rejected"]
    assert session.scheduler.pending == ()


def test_atomic_does_not_dump_historical_event_or_rng_journals(monkeypatch):
    session = Session()
    session.emit("history", {})
    session.random.sample("a")
    before = session.checkpoint()

    def forbidden():
        raise AssertionError("Historical journal must not be dumped at atomic boundaries")

    with monkeypatch.context() as scoped:
        scoped.setattr(session._events, "snapshot", forbidden)
        scoped.setattr(session.random, "snapshot", forbidden)
        with pytest.raises(ValueError):
            with session.atomic():
                session.emit("temporary", {})
                session.random.sample("a")
                raise ValueError("rollback")
    assert session.checkpoint() == before


def test_atomic_prohibits_callback_registration_advance_and_boundary_checkpoint():
    session = Session()
    before = session.checkpoint()
    with session.atomic():
        assert session.snapshot()["boundary"] is False
        for operation in (lambda: session.add_system(lambda s: None),
                          lambda: session.register_handler("new", lambda s, p: None),
                          lambda: session.advance(1), session.checkpoint, lambda: session.restore(before)):
            with pytest.raises(RuntimeError):
                operation()
    assert session.checkpoint() == before


def test_entity_revision_and_cached_immutable_views_preserve_old_references():
    world = World()
    value = {"values": [1]}
    entity = world.create("generic", value, alias="a")
    old = world.get(entity)
    assert freeze(old) is old
    assert world.get("a") is old
    assert old["version"] == world.version(entity) == 1
    value["values"].append(2)
    assert old["components"]["values"] == (1,)
    world.set(entity, ("values",), [2])
    new = world.get(entity)
    assert new is world.get(entity) and new is not old
    assert new["version"] == world.version("a") == 2
    assert old["version"] == 1 and old["components"]["values"] == (1,)
    assert new["components"]["values"] == (2,)
    with pytest.raises(TypeError):
        new["components"]["values"] = (3,)
    world.delete(entity)
    assert world.version(entity) == 3
    with pytest.raises(KeyError):
        world.version("a")
    restored = World()
    restored.restore(json.loads(json.dumps(world.snapshot())))
    assert restored.version(entity) == 3
    assert restored.entities() == ()


def test_transaction_and_atomic_rollback_restore_revisions_and_invalidate_cache():
    session = Session()
    entity = session.world.create("generic", {"v": 1})
    initial = session.world.get(entity)
    session.commit([Intent("set", entity, ("v",), 2)])
    committed = session.world.get(entity)
    assert committed["version"] == 2 and initial["components"]["v"] == 1
    with pytest.raises(ValueError):
        with session.atomic():
            session.commit([Intent("set", entity, ("v",), 3)])
            assert session.world.version(entity) == 3
            assert session.world.get(entity)["components"]["v"] == 3
            raise ValueError("rollback")
    assert session.world.version(entity) == 2
    assert session.world.get(entity)["components"]["v"] == 2
    assert session.world.get(entity) is not committed
    assert committed["components"]["v"] == 2
    checkpoint = session.checkpoint()
    session.world.set(entity, ("v",), 4)
    session.restore(checkpoint)
    assert session.world.version(entity) == 2
    assert session.world.get(entity)["components"]["v"] == 2


def test_event_journal_records_are_immutable_once_and_incremental_reads_are_stable():
    session = Session()
    payload = {"rows": [{"v": 1}]}
    session.emit("first", payload)
    first = session.events[0]
    payload["rows"][0]["v"] = 99
    assert first["payload"]["rows"][0]["v"] == 1
    assert session.events[0] is first
    with pytest.raises(TypeError):
        first["payload"]["rows"][0]["v"] = 2
    sliced = session._events.iter_records(start=0)
    session.emit("second", {"v": 2}, cause=1)
    assert list(sliced) == [first]
    assert [event["id"] for event in session._events.iter_records(start=1)] == [2]
    checkpoint = session.checkpoint()
    session.restore(json.loads(json.dumps(checkpoint)))
    restored = session.events[0]
    assert restored == first and restored is not first
    with pytest.raises(TypeError):
        restored["payload"]["rows"][0]["v"] = 2
    with pytest.raises(ValueError):
        session._events.iter_records(-1)


def test_event_records_keep_historical_reference_immutable_after_transaction_and_rollback():
    session = Session()
    session.emit("old", {"n": 1})
    old = session.events[0]
    session.commit([Intent("emit", data={"type": "new", "payload": {"n": 2}})])
    assert session.events[0] is old
    with pytest.raises(ValueError):
        with session.atomic():
            session.emit("staged", {"n": 3})
            raise ValueError("rollback")
    assert len(session.events) == 2 and session.events[0] is old


def test_cache_epoch_is_monotonic_nonpersistent_and_only_invalidates_on_restore_or_rollback():
    session = Session()
    assert session.cache_epoch == 0
    session.commit([Intent("create", data={"definition_id": "generic", "components": {"v": 1}})])
    with session.atomic():
        session.commit([Intent("set", 1, ("v",), 2)])
    assert session.cache_epoch == 0
    checkpoint = session.checkpoint()
    assert "cache_epoch" not in checkpoint
    invalid = json.loads(json.dumps(checkpoint))
    invalid["world"]["next_id"] = 1
    with pytest.raises(ValueError):
        session.restore(invalid)
    assert session.cache_epoch == 0
    session.restore(checkpoint)
    assert session.cache_epoch == 1
    with pytest.raises(ValueError):
        with session.atomic():
            session.world.set(1, ("v",), 3)
            raise ValueError("rollback")
    assert session.cache_epoch == 2
    assert session.checkpoint() == checkpoint
    session.restore(checkpoint)
    assert session.cache_epoch == 3
    assert session.checkpoint() == checkpoint


def test_nested_rollback_invalidates_epoch_without_outer_success_resetting_it():
    session = Session()
    with session.atomic():
        with pytest.raises(ValueError):
            with session.atomic():
                raise ValueError("inner rollback")
        assert session.cache_epoch == 1
    assert session.cache_epoch == 1
    with pytest.raises(ValueError):
        with session.atomic():
            with pytest.raises(ValueError):
                with session.atomic():
                    raise ValueError("inner rollback")
            assert session.cache_epoch == 2
            raise ValueError("outer rollback")
    assert session.cache_epoch == 3


def test_commit_preserves_unmodified_entity_view_and_invalidates_only_modified_entity():
    session = Session()
    first = session.world.create("generic/a", {"v": 1})
    second = session.world.create("generic/b", {"v": 2})
    first_view, second_view = session.world.get(first), session.world.get(second)
    session.commit([Intent("set", second, ("v",), 3)])
    assert session.world.get(first) is first_view
    assert session.world.get(second) is not second_view
    assert second_view["components"]["v"] == 2
    assert session.world.get(second)["components"]["v"] == 3
    assert session.world.version(first) == 1 and session.world.version(second) == 2
