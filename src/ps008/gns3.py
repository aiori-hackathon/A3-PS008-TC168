"""Read-only GNS3 discovery. This module cannot deploy configurations."""
import ipaddress
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID


class LabUnavailable(ValueError):
    """A lab could not be independently observed."""


def _uuid(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Project and node IDs must be UUID strings") from exc


def _env_name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", value):
        raise ValueError("Credential references must be environment variable names")
    return value


@dataclass(frozen=True)
class Device:
    name: str
    node_id: str
    management_ip: str
    username_env: str
    password_env: str


@dataclass(frozen=True)
class Inventory:
    controller_url: str
    api_version: str
    project_id: str
    devices: tuple[Device, ...]
    token_env: str | None = None
    probes: tuple[dict, ...] = ()
    controller_username_env: str | None = None
    controller_password_env: str | None = None

    @classmethod
    def load(cls, filename):
        return cls.parse(json.loads(Path(filename).read_text(encoding="utf-8")))

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) - {
            "controller_url", "api_version", "project_id", "devices", "token_env", "probes",
            "controller_username_env", "controller_password_env"
        } or not {"controller_url", "api_version", "project_id", "devices"} <= data.keys():
            raise ValueError("Invalid inventory fields")
        url = data["controller_url"]
        if not isinstance(url, str):
            raise ValueError("controller_url must be a string")
        parsed = urlsplit(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in {"", "/"}):
            raise ValueError("Controller URL must be an HTTP(S) origin without credentials")
        # Reject invalid ports immediately, before any connection is made.
        parsed.port
        local = parsed.hostname == "localhost"
        try:
            local = local or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme == "http" and not local:
            raise ValueError("Remote controllers require HTTPS")
        if data["api_version"] not in ("v2", "v3"):
            raise ValueError("api_version must explicitly be v2 or v3")
        rows = data["devices"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
            raise ValueError("Configure between one and three devices")
        devices = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "name", "node_id", "management_ip", "username_env", "password_env"
            }:
                raise ValueError("Invalid device fields; use credential references, not secrets")
            if row["name"] not in ("R1", "R2", "R3"):
                raise ValueError("Device name must be R1, R2 or R3")
            if not isinstance(row["management_ip"], str):
                raise ValueError("management_ip must be an IPv4 string")
            address = ipaddress.IPv4Address(row["management_ip"])
            if address.is_unspecified or address.is_multicast:
                raise ValueError("Invalid management IP")
            devices.append(Device(row["name"], _uuid(row["node_id"]), str(address),
                                  _env_name(row["username_env"]), _env_name(row["password_env"])))
        for attribute in ("name", "node_id", "management_ip"):
            if len({getattr(d, attribute) for d in devices}) != len(devices):
                raise ValueError(f"Duplicate device {attribute}")
        token = _env_name(data["token_env"]) if data.get("token_env") is not None else None
        if token and parsed.scheme != "https":
            raise ValueError("Authenticated controllers require HTTPS")
        basic_user = (_env_name(data["controller_username_env"])
                      if data.get("controller_username_env") is not None else None)
        basic_password = (_env_name(data["controller_password_env"])
                          if data.get("controller_password_env") is not None else None)
        if bool(basic_user) != bool(basic_password):
            raise ValueError("Controller Basic authentication needs both credential references")
        if token and basic_user:
            raise ValueError("Select one controller authentication method")
        if basic_user and parsed.scheme != "https" and not local:
            raise ValueError("Basic authentication over HTTP is limited to loopback")
        probes = data.get("probes", [])
        if not isinstance(probes, list):
            raise ValueError("probes must be a list")
        checked = []
        for probe in probes:
            if not isinstance(probe, dict) or set(probe) != {"source", "destination"}:
                raise ValueError("Each probe must specify only source and destination")
            if probe["source"] not in {d.name for d in devices}:
                raise ValueError("Probe source must be a configured router")
            try:
                destination = str(ipaddress.IPv4Address(probe["destination"]))
            except (ValueError, TypeError) as exc:
                raise ValueError("Probe destination must be an IPv4 host address") from exc
            checked.append({"source": probe["source"], "destination": destination})
        if len({(p["source"], p["destination"]) for p in checked}) != len(checked):
            raise ValueError("Duplicate connectivity probe")
        return cls(url.rstrip("/"), data["api_version"], _uuid(data["project_id"]),
                   tuple(devices), token, tuple(checked), basic_user, basic_password)


def discover(inventory, *, transport=None):
    """Observe exact configured nodes; never creates, starts or edits a lab."""
    try:
        import httpx
    except ImportError as exc:
        raise LabUnavailable('Install the optional dependency: pip install -e ".[gns3]"') from exc
    headers = {}
    if inventory.token_env:
        token = os.environ.get(inventory.token_env)
        if not token:
            raise LabUnavailable("Controller token environment variable is not configured")
        headers["Authorization"] = f"Bearer {token}"
    auth = None
    if inventory.controller_username_env:
        username = os.environ.get(inventory.controller_username_env)
        password = os.environ.get(inventory.controller_password_env)
        if not username or not password:
            raise LabUnavailable("Controller credential environment variables are not configured")
        auth = httpx.BasicAuth(username, password)
    base = f"{inventory.controller_url}/{inventory.api_version}"
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False,
                          transport=transport, headers=headers, auth=auth) as client:
            def read(path):
                response = client.get(base + path)
                response.raise_for_status()
                return response.json()
            version = read("/version")
            project = read(f"/projects/{inventory.project_id}")
            nodes = read(f"/projects/{inventory.project_id}/nodes")
        if not isinstance(version, dict) or not isinstance(version.get("version"), str):
            raise ValueError("Invalid controller version response")
        if not isinstance(project, dict) or project.get("project_id") != inventory.project_id:
            raise ValueError("Project identity does not match inventory")
        if not isinstance(nodes, list) or any(not isinstance(n, dict) for n in nodes):
            raise ValueError("Invalid node list")
        observed = []
        for device in inventory.devices:
            matching = [n for n in nodes if n.get("node_id") == device.node_id]
            if len(matching) != 1 or matching[0].get("name") != device.name:
                raise ValueError("Node identity does not match inventory")
            status = matching[0].get("status")
            if status not in {"started", "stopped", "suspended"}:
                raise ValueError("Unknown node status")
            observed.append({"name": device.name, "node_id": device.node_id,
                             "status": status, "management_ip": device.management_ip})
        return {"backend": "gns3", "evidence": "controller-observed",
                "controller_version": version["version"], "project_id": inventory.project_id,
                "devices": observed, "deployment_enabled": False,
                "message": "Controller discovery only; SSH and packet connectivity are not verified"}
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        # Never expose response bodies, URLs with injected credentials, or transport exception strings.
        raise LabUnavailable("GNS3 discovery failed: check server, API version, identity and authentication") from exc
