import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ps008.deployment import DeploymentManager
from ps008.generator import generate_candidate, is_allowlisted, parse_request
from ps008.lab import DigitalTwin, Route
from ps008.state import StateStore
from ps008.workflow import run_workflow


def request(**changes):
    result = {
        "intent_type": "add_static_route", "source": "R1",
        "destination": "10.20.0.0/24", "next_hop": "10.0.1.2",
        "protocol": "static", "requested_change": "Test the lab route",
        "request_id": "test-001",
    }
    result.update(changes)
    return result


@pytest.mark.parametrize("change", [
    {"destination": "2001:db8::/64"},
    {"next_hop": "2001:db8::1"},
    {"request_id": "  "},
    {"source": ["R1"]},
    {"intent_type": ["add_static_route"]},
    {"commands": ["reload"]},
])
def test_untrusted_request_rejected(change):
    with pytest.raises(ValueError):
        parse_request(request(**change))


def test_ios_command_allowlist_is_canonical():
    assert is_allowlisted("ip route 10.20.0.0 255.255.255.0 10.0.1.2")
    assert is_allowlisted("no ip route 10.20.0.0 255.255.255.0 10.0.1.2")
    assert not is_allowlisted("no ip route 10.20.0.0 255.255.255.0")
    assert not is_allowlisted("ip route 10.20.0.0/24 10.0.1.2")
    assert not is_allowlisted("ip route 10.20.0.1 255.255.255.0 10.0.1.2")
    assert not is_allowlisted("ip route 10.20.0.0 255.255.255.0 10.0.1.2 ; reload")


def test_reviewed_candidate_cannot_be_swapped():
    twin = DigitalTwin()
    candidate = generate_candidate(parse_request(request()), twin.revision)
    forged = replace(candidate, commands=("ip route 10.30.0.0 255.255.255.0 10.0.1.2",))
    result = DeploymentManager(twin).deploy(forged, True)
    assert not result.success
    assert twin.state()["R1"] == []


def test_overlapping_prefix_forwarding_loop_is_rejected():
    twin = DigitalTwin()
    twin.routes["R2"].append(Route("10.0.0.0/8", "10.0.1.1", "R2"))
    candidate = generate_candidate(parse_request(request()))
    result = twin.validate(candidate)
    assert not result.valid
    assert any("routing loop" in message for message in result.errors)


def test_next_hop_must_be_on_source_link():
    twin = DigitalTwin()
    candidate = generate_candidate(parse_request(request(next_hop="10.0.2.1")))
    result = twin.validate(candidate)
    assert not result.valid
    assert any("directly connected" in message for message in result.errors)


def test_stale_review_cannot_overwrite_new_state():
    twin = DigitalTwin()
    stale = generate_candidate(parse_request(request()), twin.revision)
    other = generate_candidate(parse_request(request(destination="10.30.0.0/24", request_id="test-002")), twin.revision)
    manager = DeploymentManager(twin)
    assert manager.deploy(other, True).success
    result = manager.deploy(stale, True)
    assert not result.success
    assert len(twin.routes["R1"]) == 1


def test_request_id_cannot_be_reused_after_commit():
    twin = DigitalTwin()
    manager = DeploymentManager(twin)
    assert manager.deploy(generate_candidate(parse_request(request())), True).success
    duplicate = generate_candidate(parse_request(request(destination="10.30.0.0/24")))
    result = manager.deploy(duplicate, True)
    assert not result.success
    assert len(twin.routes["R1"]) == 1


def test_persistent_add_remove_and_rollback(tmp_path):
    store = StateStore(tmp_path / "lab.json")
    twin = store.load()
    manager = DeploymentManager(twin, store)
    assert run_workflow(request(), lambda _: True, twin, manager)["deployment"].success
    assert store.load().routes["R1"][0].network == "10.20.0.0/24"
    assert store.load().revision == 1
    assert store.load().applied_request_ids == {"test-001"}

    manager.fail_next = True
    second = request(destination="10.30.0.0/24", request_id="test-002")
    failed = run_workflow(second, lambda _: True, twin, manager)
    assert failed["deployment"].rolled_back
    assert len(store.load().routes["R1"]) == 1
    assert store.load().revision == 1
    assert store.load().applied_request_ids == {"test-001"}

    remove = request(intent_type="remove_static_route", next_hop=None, request_id="test-003")
    assert run_workflow(remove, lambda _: True, twin, manager)["deployment"].success
    assert store.load().routes["R1"] == []
    assert store.load().revision == 2


def test_pending_journal_restores_before_state_on_restart(tmp_path):
    store = StateStore(tmp_path / "lab.json")
    twin = store.load()
    store.begin(twin)
    twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
    twin.revision = 1
    store._write(store.path, store._payload(twin))
    recovered = store.load()
    assert recovered.routes["R1"] == []
    assert recovered.revision == 0
    assert not store.journal.exists()


def test_stale_process_state_cannot_replace_committed_state(tmp_path):
    store = StateStore(tmp_path / "lab.json")
    first = store.load()
    second = store.load()
    assert DeploymentManager(first, store).deploy(generate_candidate(parse_request(request()), 0), True).success
    other = generate_candidate(parse_request(request(destination="10.30.0.0/24", request_id="test-002")), 0)
    result = DeploymentManager(second, store).deploy(other, True)
    assert not result.success
    assert len(store.load().routes["R1"]) == 1


def test_cli_state_survives_separate_invocations(tmp_path):
    root = Path(__file__).resolve().parents[1]
    state = tmp_path / "lab.json"
    for example in ("add_route.json", "remove_route.json"):
        process = subprocess.run(
            [sys.executable, "-m", "ps008", str(root / "examples" / example), "--state-file", str(state)],
            input="yes\n", text=True, capture_output=True, cwd=root, check=True,
        )
        output = json.loads(process.stdout[process.stdout.index("{\n"):])
        assert output["deployment"]["success"]
    store_state = StateStore(state).load()
    assert store_state.revision == 2
    assert store_state.routes["R1"] == []
