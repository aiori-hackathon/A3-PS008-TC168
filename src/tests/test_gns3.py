import copy
import json
from pathlib import Path

import httpx
import pytest

from ps008.gns3 import Inventory, LabUnavailable, discover
from ps008.device_observation import configure_ios, observe_ios, ping_ios


@pytest.fixture
def data():
    return json.loads((Path(__file__).parents[1] / "examples/gns3-inventory.example.json").read_text())


@pytest.mark.parametrize("url", ["http://example.com:3080", "https://u:secret@example.com", "https://example.com/path", "file:///tmp/lab"])
def test_reject_unsafe_controller(data, url):
    data["controller_url"] = url
    with pytest.raises(ValueError):
        Inventory.parse(data)


def test_reject_duplicate_nodes_and_secrets(data):
    data["devices"].append(copy.deepcopy(data["devices"][0]))
    with pytest.raises(ValueError):
        Inventory.parse(data)
    data["devices"].pop()
    data["devices"][0]["password"] = "must-not-be-in-inventory"
    with pytest.raises(ValueError):
        Inventory.parse(data)


def test_local_controller_basic_auth_uses_environment_only(data, monkeypatch):
    data["controller_username_env"] = "PS008_GNS3_USERNAME"
    data["controller_password_env"] = "PS008_GNS3_PASSWORD"
    monkeypatch.setenv("PS008_GNS3_USERNAME", "local-user")
    monkeypatch.setenv("PS008_GNS3_PASSWORD", "local-secret")
    inventory = Inventory.parse(data)
    seen = []

    def handle(request):
        assert request.method == "GET"
        assert request.headers["Authorization"].startswith("Basic ")
        seen.append(request.url.path)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"version": "2.2.61"})
        if request.url.path.endswith("/nodes"):
            return httpx.Response(200, json=[{"name": "R1", "node_id": inventory.devices[0].node_id,
                                              "status": "started"}])
        return httpx.Response(200, json={"project_id": inventory.project_id})

    result = discover(inventory, transport=httpx.MockTransport(handle))
    assert len(seen) == 3
    assert "local-secret" not in json.dumps(result)


def test_basic_auth_requires_both_refs_and_credentials(data, monkeypatch):
    data["controller_username_env"] = "PS008_GNS3_USERNAME"
    with pytest.raises(ValueError, match="both"):
        Inventory.parse(data)
    data["controller_password_env"] = "PS008_GNS3_PASSWORD"
    inventory = Inventory.parse(data)
    monkeypatch.delenv("PS008_GNS3_PASSWORD", raising=False)
    with pytest.raises(LabUnavailable, match="credential environment"):
        discover(inventory, transport=httpx.MockTransport(lambda _: pytest.fail("must not call")))
    data["controller_url"] = "https://localhost:3080"
    data["token_env"] = "PS008_GNS3_TOKEN"
    with pytest.raises(ValueError, match="one controller"):
        Inventory.parse(data)


@pytest.mark.parametrize("api", ["v2", "v3"])
def test_discovery_only_gets_configured_identity(data, api):
    data["api_version"] = api
    inventory = Inventory.parse(data)
    paths = []
    def handle(request):
        assert request.method == "GET"
        paths.append(request.url.path)
        if request.url.path.endswith("/version"):
            value = {"version": "test-version"}
        elif request.url.path.endswith("/nodes"):
            value = [{"name": "R1", "node_id": inventory.devices[0].node_id, "status": "started"}]
        else:
            value = {"project_id": inventory.project_id}
        return httpx.Response(200, json=value)
    result = discover(inventory, transport=httpx.MockTransport(handle))
    assert len(paths) == 3 and all(p.startswith(f"/{api}/") for p in paths)
    assert result["deployment_enabled"] is False
    assert result["evidence"] == "controller-observed"


@pytest.mark.parametrize("status", [302, 401, 500])
def test_http_failure_is_not_simulated_success(data, status):
    def handle(request):
        return httpx.Response(status, headers={"Location": "https://example.com"}, text="SECRET")
    with pytest.raises(LabUnavailable) as caught:
        discover(Inventory.parse(data), transport=httpx.MockTransport(handle))
    assert "SECRET" not in str(caught.value)


def test_wrong_project_fails(data):
    def handle(request):
        return httpx.Response(200, json={"version": "test", "project_id": "wrong"})
    with pytest.raises(LabUnavailable):
        discover(Inventory.parse(data), transport=httpx.MockTransport(handle))


def test_readonly_ssh_and_host_key_enforcement(data, monkeypatch):
    monkeypatch.setenv("PS008_R1_USERNAME", "test")
    monkeypatch.setenv("PS008_R1_PASSWORD", "SECRET")
    commands = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_command(self, command, **kwargs):
            commands.append(command)
            return "Cisco IOS Software" if command == "show version" else "sample evidence"
    def connector(**kwargs):
        assert kwargs["ssh_strict"] and kwargs["system_host_keys"]
        assert kwargs["conn_timeout"] == 5
        return Connection()
    result = observe_ios(Inventory.parse(data).devices[0], connector=connector)
    assert commands == ["show version", "show ip interface brief", "show ip route"]
    assert not result["preview_ready"] and not result["deployment_enabled"]
    assert "SECRET" not in json.dumps(result)


def test_missing_credentials_prevent_connection(data, monkeypatch):
    monkeypatch.delenv("PS008_R1_PASSWORD", raising=False)
    def connector(**kwargs):
        pytest.fail("must not connect")
    with pytest.raises(LabUnavailable):
        observe_ios(Inventory.parse(data).devices[0], connector=connector)


def test_configuration_is_allowlisted_and_uses_strict_ssh(data, monkeypatch):
    device = Inventory.parse(data).devices[0]
    monkeypatch.setenv("PS008_R1_USERNAME", "test")
    monkeypatch.setenv("PS008_R1_PASSWORD", "secret")
    commands, options = [], []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_config_set(self, items, **kwargs):
            commands.extend(items)
            return "R1(config)# " + items[0]
    def connector(**kwargs):
        options.append(kwargs)
        return Connection()
    with pytest.raises(ValueError, match="allowlist"):
        configure_ios(device, ["show running-config"], connector=connector)
    result = configure_ios(device, ["ip route 10.20.0.0 255.255.255.0 10.0.1.2"], connector=connector)
    assert result["accepted"] and len(commands) == 1
    assert options[0]["ssh_strict"] and options[0]["system_host_keys"]


def test_ping_requires_explicit_all_success_result(data, monkeypatch):
    device = Inventory.parse(data).devices[0]
    monkeypatch.setenv("PS008_R1_USERNAME", "test")
    monkeypatch.setenv("PS008_R1_PASSWORD", "secret")
    class Connection:
        def __init__(self, result): self.result = result
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def send_command(self, command, **kwargs): return self.result
    assert ping_ios(device, "10.20.0.10", connector=lambda **_: Connection("Success rate is 100 percent (5/5)"))["success"]
    assert not ping_ios(device, "10.20.0.10", connector=lambda **_: Connection("Success rate is 80 percent (4/5)"))["success"]
