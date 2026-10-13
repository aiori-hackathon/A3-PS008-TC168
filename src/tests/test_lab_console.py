import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ps008.cli import ADD_REQUEST
from ps008.generator import parse_request
from ps008.gns3 import Inventory, LabUnavailable
from ps008.lab_console import LabConsole
from ps008.observed_network import digest, normalize, preview, trace
from ps008.rehearsal import observations
from ps008.web import create_app


def model():
    return [normalize(o) for o in observations()]


def test_preview_uses_captured_routes_without_mutation():
    network = model()
    original = copy.deepcopy(network)
    assert trace(network, "R1", "10.20.0.10")["outcome"] == "missing_route"
    result = preview(network, parse_request(ADD_REQUEST))
    assert result["valid"] and not result["deployment_enabled"]
    checks = [c for c in result["checks"] if c["source"] == "R1"]
    assert all(c["after"]["path"] == ["R1", "R2", "R3"] for c in checks)
    assert result["regression_count"] == result["loop_count"] == 0
    assert network == original


def test_observed_loop_is_blocked():
    network = model()
    route = next(r for r in network[1]["routes"] if r["network"] == "10.20.0.0/24")
    route["next_hop"] = "10.0.1.1"
    result = preview(network, parse_request(ADD_REQUEST))
    assert not result["valid"] and result["loop_count"] > 0


def test_removed_route_reports_regression():
    network = model()
    network[0]["routes"].append({"network": "10.20.0.0/24", "next_hop": "10.0.1.2", "kind": "static", "interface": None})
    request = parse_request({**ADD_REQUEST, "intent_type": "remove_static_route", "next_hop": None})
    result = preview(network, request)
    assert result["regression_count"] > 0
    assert result["commands"] == ["no ip route 10.20.0.0 255.255.255.0 10.0.1.2"]


@pytest.mark.parametrize("line", ["O 172.16.0.0/24 [110/2] via 10.0.1.2", "S 172.16.0.0/24 [1/0] via 10.0.1.2, 00:00:04, GigabitEthernet0/1", "S 172.16.0.0/24 is directly connected, Null0", "% Invalid input", "S 172.16.0.0/24 [5/0] via 10.0.1.2"])
def test_parser_rejects_unsupported_routes(line):
    raw = observations()[0]
    raw["routes_raw"] += "\n" + line
    with pytest.raises(ValueError):
        normalize(raw)


def test_normalization_ignores_order():
    raw = observations()[0]
    first = normalize(raw)
    lines = raw["routes_raw"].splitlines()
    raw["routes_raw"] = "\n".join(lines[:2] + list(reversed(lines[2:])))
    assert first == normalize(raw)


def test_no_silent_baseline_routes():
    network = model()
    network[1]["routes"] = [r for r in network[1]["routes"] if r["network"] != "10.20.0.0/24"]
    result = preview(network, parse_request(ADD_REQUEST))
    assert not result["valid"]
    assert any(c["after"]["outcome"] == "missing_route" for c in result["checks"])


def test_rehearsal_persists_and_exports_digest(tmp_path):
    service = LabConsole(None, tmp_path / "evidence.json")
    capture = service.capture("rehearsal")
    checksum = capture.pop("digest")
    assert checksum == digest(capture)
    assert service.preview(ADD_REQUEST)["origin"] == "synthetic-rehearsal"
    second = LabConsole(None, tmp_path / "evidence.json").capture("rehearsal")
    assert second["drift"] == {"comparable": True, "changed_devices": []}
    assert service.status()["latest"]["origin"] == "synthetic-rehearsal"


def test_failed_live_capture_does_not_fall_back(tmp_path):
    service = LabConsole(None, tmp_path / "evidence.json")
    first = service.capture("rehearsal")
    with pytest.raises(LabUnavailable):
        service.capture("device")
    assert service.status()["latest"]["digest"] == first["digest"]
    assert not service.status()["last_attempt"]["success"]
    with pytest.raises(ValueError):
        service.preview(ADD_REQUEST)


def test_expired_evidence_blocks_preview(tmp_path):
    service = LabConsole(None, tmp_path / "evidence.json")
    service.capture("rehearsal")
    data = service._load()
    data["latest"]["captured_at"] = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    service.store._write(service.store.path, data)
    with pytest.raises(ValueError, match="15 minutes"):
        service.preview(ADD_REQUEST)


def test_device_drift_and_partial_failure(tmp_path):
    data = json.loads((Path(__file__).parents[1] / "examples/gns3-inventory.example.json").read_text())
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data))
    raw = observations()[0]
    def discoverer(_): return {"devices": [{"name": "R1", "status": "started"}]}
    service = LabConsole(path, tmp_path / "evidence.json", discoverer, lambda _: copy.deepcopy(raw))
    first = service.capture("device")
    assert first["preview_ready"] and first["origin"] == "device-observed"
    raw["routes_raw"] += "\nS 10.20.0.0/24 [1/0] via 10.0.1.2"
    assert service.capture("device")["drift"]["changed_devices"] == ["R1"]
    raw["routes_raw"] += "\nO unsupported"
    result = service.capture("device")
    assert not result["preview_ready"] and result["issues"]
    with pytest.raises(ValueError): service.preview(ADD_REQUEST)


def test_dashboard_api_csrf_and_no_deploy(tmp_path):
    with TestClient(create_app(tmp_path / "state.json"), base_url="http://127.0.0.1") as client:
        token = client.get("/api/session").json()["csrf_token"]
        assert client.get("/gns3").status_code == 200
        assert not client.get("/api/gns3/status").json()["configured"]
        assert client.post("/api/gns3/capture", json={"mode": "rehearsal"}).status_code == 403
        client.headers.update({"Origin": "http://127.0.0.1", "X-CSRF-Token": token})
        assert client.post("/api/gns3/capture", json={"mode": "rehearsal", "url": "http://evil"}).status_code == 422
        assert client.post("/api/gns3/capture", json={"mode": "rehearsal"}).status_code == 200
        response = client.post("/api/gns3/preview", json=ADD_REQUEST)
        assert response.json()["valid"]
        assert client.get("/api/lab").json()["revision"] == 0
        assert client.post("/api/gns3/deploy", json={}).status_code == 404
        assert client.get("/api/gns3/evidence").json()["latest"]["origin"] == "synthetic-rehearsal"


def live_fixture(tmp_path):
    data = json.loads((Path(__file__).parents[1] / "examples/gns3-inventory.example.json").read_text())
    seed = data["devices"][0]
    data["devices"] = [
        {**seed, "name": name, "node_id": f"00000000-0000-0000-0000-00000000000{index+2}",
         "management_ip": f"192.0.2.{10+index}", "username_env": f"PS008_{name}_USERNAME",
         "password_env": f"PS008_{name}_PASSWORD"}
        for index, name in enumerate(("R1", "R2", "R3"))
    ]
    data["probes"] = [{"source": "R1", "destination": "10.20.0.10"}]
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data))
    raw = {name: copy.deepcopy(item) for name, item in zip(("R1", "R2", "R3"), observations())}
    for name, item in raw.items():
        item["device"] = name
    writes = []

    def discoverer(inventory):
        return {"devices": [{"name": device.name, "status": "started"} for device in inventory.devices]}

    def observer(device):
        return copy.deepcopy(raw[device.name])

    def configurator(device, commands):
        writes.extend(commands)
        command = commands[0]
        if command.startswith("ip route "):
            _, _, address, mask, hop = command.split()
            prefix = str(__import__("ipaddress").IPv4Network(f"{address}/{mask}"))
            raw[device.name]["routes_raw"] += f"\nS {prefix} [1/0] via {hop}"
        elif command.startswith("no ip route "):
            tokens = command.split()
            prefix = str(__import__("ipaddress").IPv4Network(f"{tokens[3]}/{tokens[4]}"))
            raw[device.name]["routes_raw"] = "\n".join(
                line for line in raw[device.name]["routes_raw"].splitlines() if prefix not in line
            )
        return {"accepted": True, "command": command}

    console = LabConsole(path, tmp_path / "evidence.json", discoverer, observer,
                         configurator, lambda _device, destination: {"destination": destination, "success": True})
    return console, writes


def test_live_review_requires_explicit_approval_and_readback(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    assert review["status"] == "pending" and writes == []
    assert review["pre_change_evidence"]["origin"] == "device-observed"
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "verified"
    assert writes == ["ip route 10.20.0.0 255.255.255.0 10.0.1.2"]
    assert done["verification_result"]["probe"]["success"]
    assert (tmp_path / "gns3-operations.json").exists()
    assert console.operations.path != console.store.path


def test_live_review_rejection_never_writes_and_is_single_use(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    rejected = console.decide_operation(review["operation_id"], False)
    assert rejected["status"] == "rejected" and writes == []
    with pytest.raises(ValueError, match="already been decided"):
        console.decide_operation(review["operation_id"], True)


def test_live_review_stale_state_blocks_device_write(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    raw = console.observer
    # Drift after the human reviewed the captured state.
    original = raw
    def changed(device):
        value = original(device)
        if device.name == "R2":
            value["routes_raw"] += "\nS 172.16.0.0/16 [1/0] via 10.0.1.1"
        return value
    console.observer = changed
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "stale" and writes == []


def test_agent_owned_route_can_be_removed_after_verified_add(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    added = console.prepare_operation(ADD_REQUEST)
    assert console.decide_operation(added["operation_id"], True)["status"] == "verified"
    console.capture("device")
    remove = parse_request({**ADD_REQUEST, "intent_type": "remove_static_route", "next_hop": None,
                            "request_id": "remove-after-agent-add"})
    review = console.prepare_operation({**remove.__dict__})
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "verified"
    assert writes[-1] == "no ip route 10.20.0.0 255.255.255.0 10.0.1.2"


def test_unmanaged_route_cannot_be_removed(tmp_path):
    console, _writes = live_fixture(tmp_path)
    console.capture("device")
    raw = console.observer
    def with_unmanaged_route(device):
        value = raw(device)
        if device.name == "R1":
            value["routes_raw"] += "\nS 10.20.0.0/24 [1/0] via 10.0.1.2"
        return value
    console.observer = with_unmanaged_route
    console.capture("device")
    request = {**ADD_REQUEST, "intent_type": "remove_static_route", "next_hop": None,
               "request_id": "remove-unmanaged"}
    with pytest.raises(ValueError, match="previously added and verified"):
        console.prepare_operation(request)


def test_failed_probe_recovers_only_the_candidate_route(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.prober = lambda *_args: {"success": False, "output": "Success rate is 0 percent (0/5)"}
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "failed"
    assert done["recovery_result"]["success"] is True
    assert done["recovery_result"]["outcome"] == "restored"
    assert writes == ["ip route 10.20.0.0 255.255.255.0 10.0.1.2",
                      "no ip route 10.20.0.0 255.255.255.0 10.0.1.2"]
    assert not any(route["network"] == "10.20.0.0/24"
                   for route in normalize(console.observer(Inventory.load(console.inventory_path).devices[0]))["routes"])


def test_failed_recovery_is_visible_and_blocks_new_writes(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.prober = lambda *_args: {"success": False, "output": "Success rate is 0 percent (0/5)"}
    original = console.configurator
    def fail_inverse(device, commands):
        if commands[0].startswith("no ip route"):
            raise LabUnavailable("recovery SSH unavailable")
        return original(device, commands)
    console.configurator = fail_inverse
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "recovery_required"
    assert done["recovery_result"]["outcome"] == "recovery_required"
    assert writes == ["ip route 10.20.0.0 255.255.255.0 10.0.1.2"]
    with pytest.raises(ValueError, match="unresolved device operation"):
        console.prepare_operation({**ADD_REQUEST, "request_id": "blocked-by-recovery"})


def test_command_timeout_after_write_is_reconciled_without_retry(tmp_path):
    console, writes = live_fixture(tmp_path)
    original = console.configurator
    def write_then_timeout(device, commands):
        original(device, commands)
        raise LabUnavailable("timeout after config")
    console.configurator = write_then_timeout
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "verified"
    assert len(writes) == 1


def test_command_timeout_before_write_reconciles_as_failed(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.configurator = lambda *_args: (_ for _ in ()).throw(LabUnavailable("timeout before write"))
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    done = console.decide_operation(review["operation_id"], True)
    assert done["status"] == "failed"
    assert done["verification_result"]["route_state"] == "unchanged"
    assert writes == []


def test_interrupted_operation_can_be_read_only_reconciled(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    console.configurator(Inventory.load(console.inventory_path).devices[0], review["candidate"]["commands"])
    console._update_operation(review["operation_id"], status="outcome_unknown")
    reconciled = console.reconcile_operation(review["operation_id"])
    assert reconciled["status"] == "verified"
    assert reconciled["recovery_result"]["outcome"] == "reconciled"
    assert len(writes) == 1


def test_interrupted_operation_unchanged_state_reconciles_failed(tmp_path):
    console, writes = live_fixture(tmp_path)
    console.capture("device")
    review = console.prepare_operation(ADD_REQUEST)
    console._update_operation(review["operation_id"], status="applying")
    reconciled = console.reconcile_operation(review["operation_id"])
    assert reconciled["status"] == "failed"
    assert writes == []
