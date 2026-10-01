"""Replay only with the same compiled content and mechanics version."""
from ..adapters.api import Simulator


def replay(record, pack_dir=None):
    if record.get("schemaVersion") != 1:
        raise ValueError("unsupported replay schema")
    sim = Simulator(level_id=record["levelId"], seed=record["seed"],
                    squad=record["squad"], backend="modular", pack_dir=pack_dir)
    if sim.battle.content.digest != record["contentDigest"]:
        raise ValueError("replay content digest mismatch")
    if sim.battle.mechanics_version != record["mechanicsVersion"]:
        raise ValueError("replay mechanics version mismatch")
    until = record["untilTick"]
    if not isinstance(until, int) or until < 0:
        raise ValueError("invalid replay end tick")
    for command in record["commands"]:
        tick = command["tick"]
        if not isinstance(tick, int) or tick < sim.tick or tick > until:
            raise ValueError("commands must be ordered within the replay interval")
        sim.step(tick - sim.tick)
        if sim.tick != tick:
            raise ValueError("command occurs after battle end")
        result = sim.battle.execute(command["action"])
        if (result[0], result[1]) != (command["ok"], command["result"]):
            raise ValueError(f"command result mismatch at tick {tick}")
    sim.step(until - sim.tick)
    if sim.tick != until:
        raise ValueError("replay end tick exceeds battle end")
    return sim
