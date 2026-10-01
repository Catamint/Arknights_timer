"""Historical V1 stage-one model example; current validation uses ark_sim."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ark_emulator import Simulator
from ark_emulator.validation.replay import replay
from ark_emulator.validation.compare import first_difference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-out", type=Path)
    args = parser.parse_args()
    sim = Simulator(level_id="level_main_00-01", backend="modular", seed=123,
                    squad=[{"charId": "char_502_nblade", "level": 30},
                           {"charId": "char_124_kroos", "level": 40}])
    assert sim.deploy("char_502_nblade", 3, 3)[0]
    sim.run_ticks(180)
    assert sim.deploy("char_124_kroos", 1, 3, 2)[0]
    snap = sim.run_ticks(10000)
    record = sim.export_replay()
    difference = first_difference(snap, replay(record).snapshot())
    if args.replay_out:
        args.replay_out.parent.mkdir(parents=True, exist_ok=True)
        args.replay_out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"level": sim.level_id, "backend": sim.backend, "tick": sim.tick,
                      "result": snap["result"], "lifePoint": snap["lifePoint"],
                      "stats": snap["stats"], "replayDifference": difference,
                      "supportStatus": snap["support"]["status"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
