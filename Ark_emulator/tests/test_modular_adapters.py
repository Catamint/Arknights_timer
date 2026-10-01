"""Public HTTP and AI contracts on the migrated level."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ark_emulator import Simulator
from ark_emulator.live_server import LiveServer
from ark_emulator.agent_env import AgentEnv


def request(port, path, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)


@pytest.fixture
def server():
    sim = Simulator(level_id="level_main_00-01")
    sim.pause()
    srv = LiveServer(sim, port=0)
    srv.start()
    try:
        yield srv, srv._httpd.server_address[1]
    finally:
        srv.stop()


def test_http_catalog_does_not_load_extraction_bundle(server):
    srv, port = server
    assert request(port, "/status")["level"]["name"] == "0-1"
    assert request(port, "/levels")["levels"] == ["level_main_00-01"]
    assert len(request(port, "/operators")["hits"]) == 4
    catalog = {row["charId"]: row for row in request(port, "/operators")["hits"]}
    assert catalog["char_124_kroos"]["rarity"] == 3
    assert catalog["char_502_nblade"]["rarity"] == 2
    assert len(request(port, "/enemies")["hits"]) == 2
    assert srv.sim._store is None
    assert request(port, "/snapshot")["backend"] == "modular"


def test_http_single_step_and_restart(server):
    srv, port = server
    original_seed = srv.sim.battle.seed
    data = request(port, "/action", {"action": "step", "n": 30})
    assert data["snapshot"]["tick"] == 30 and data["snapshot"]["paused"]
    assert request(port, "/action", {"action": "restart"})["ok"]
    assert request(port, "/status")["tick"] == 0
    assert srv.sim.battle.paused
    assert srv.sim.battle.seed == original_seed


def test_http_concurrent_commands_cannot_duplicate_operator(server):
    srv, port = server
    actions = [{"action": "deploy", "charId": "char_502_nblade", "row": 3, "col": c} for c in (2, 3)]
    with ThreadPoolExecutor(2) as executor:
        result = list(executor.map(lambda a: request(port, "/action", a), actions))
    assert sum(item["ok"] for item in result) == 1
    assert len(srv.sim.battle.operators) == 1


def test_invalid_squad_is_reported_and_previous_config_preserved(server):
    srv, port = server
    previous = request(port, "/config")
    with pytest.raises(urllib.error.HTTPError) as error:
        request(port, "/squad", {"squad": [{"charId": "char_002_amiya"}]})
    assert error.value.code == 400
    assert request(port, "/config") == previous
    assert srv.sim.battle.content.operators.keys() == {"char_502_nblade", "char_124_kroos"}


def test_ai_legal_actions_execute_and_rewards_track_the_new_core():
    env = AgentEnv(level_id="level_main_00-01", squad=[{"charId": "char_502_nblade", "level": 30}])
    obs, info = env.reset(seed=123)
    assert obs["backend"] == "modular"
    actions = env.legal_actions(max_cells=3)
    assert actions
    _, reward, _, info = env.step(actions[0])
    assert not info["invalid"] and reward == -0.5
    assert env.info()["stats"]["deployments"] == 1
