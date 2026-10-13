"""Bounded IOS observation and narrowly allowlisted route configuration."""
import hashlib
import os
import re
from datetime import datetime, timezone

from .gns3 import LabUnavailable


def _credentials(device):
    username = os.environ.get(device.username_env)
    password = os.environ.get(device.password_env)
    if not username or not password:
        raise LabUnavailable("Device credential environment variables are not configured")
    return username, password


def _connector(connector):
    if connector is not None:
        return connector
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:
        raise LabUnavailable('Install the optional dependency: pip install -e ".[gns3]"') from exc
    return ConnectHandler


def observe_ios(device, *, connector=None):
    username, password = _credentials(device)
    connector = _connector(connector)
    try:
        with connector(device_type="cisco_ios", host=device.management_ip,
                       username=username, password=password, ssh_strict=True,
                       system_host_keys=True, conn_timeout=5, auth_timeout=5,
                       banner_timeout=5, allow_agent=False, use_keys=False) as connection:
            version = connection.send_command("show version", read_timeout=10)
            if not isinstance(version, str) or "Cisco IOS" not in version:
                raise ValueError("Unsupported device OS")
            interfaces = connection.send_command("show ip interface brief", read_timeout=10)
            routes = connection.send_command("show ip route", read_timeout=10)
            for output in (interfaces, routes):
                if not isinstance(output, str) or not output.strip() or "%" in output or len(output) > 250000:
                    raise ValueError("Missing, rejected or excessive command output")
            return {"device": device.name, "node_id": device.node_id,
                    "evidence": "device-observed", "observed_at": datetime.now(timezone.utc).isoformat(),
                    "interfaces_raw": interfaces, "routes_raw": routes,
                    "evidence_digest": hashlib.sha256((interfaces + "\n" + routes).encode()).hexdigest(),
                    "preview_ready": False, "deployment_enabled": False,
                    "message": "IOS evidence observed through a verified SSH host key"}
    except Exception as exc:
        raise LabUnavailable("IOS observation failed: check host key, credentials, supported OS and connectivity") from exc


def configure_ios(device, commands, *, connector=None):
    """Execute one validated candidate via SSH. Callers must journal approval first."""
    if not isinstance(commands, (list, tuple)) or len(commands) != 1:
        raise ValueError("Exactly one reviewed route command is required")
    from .generator import is_allowlisted
    command = commands[0]
    if not is_allowlisted(command):
        raise ValueError("Command is outside the IPv4 static-route allowlist")
    username, password = _credentials(device)
    connector = _connector(connector)
    try:
        with connector(device_type="cisco_ios", host=device.management_ip,
                       username=username, password=password, ssh_strict=True,
                       system_host_keys=True, conn_timeout=5, auth_timeout=5,
                       banner_timeout=5, allow_agent=False, use_keys=False) as connection:
            output = connection.send_config_set([command], read_timeout=15)
            if not isinstance(output, str) or len(output) > 50000:
                raise ValueError("Invalid configuration response")
            if any(marker in output.lower() for marker in ("% invalid", "% incomplete", "% ambiguous", "% authorization failed")):
                raise ValueError("IOS rejected the reviewed command")
            return {"command": command, "output": output, "accepted": True}
    except Exception as exc:
        # Caller must reconcile by read-back; exceptions do not imply no write occurred.
        raise LabUnavailable("IOS command result is uncertain; reconcile device state before retrying") from exc


def ping_ios(device, destination, *, connector=None):
    """Run a source-defined IPv4 probe and accept only an explicit 100% result."""
    import ipaddress
    address = str(ipaddress.IPv4Address(destination))
    username, password = _credentials(device)
    connector = _connector(connector)
    try:
        with connector(device_type="cisco_ios", host=device.management_ip,
                       username=username, password=password, ssh_strict=True,
                       system_host_keys=True, conn_timeout=5, auth_timeout=5,
                       banner_timeout=5, allow_agent=False, use_keys=False) as connection:
            output = connection.send_command(f"ping {address}", read_timeout=15)
        if not isinstance(output, str) or len(output) > 50000:
            raise ValueError("Invalid ping response")
        match = re.search(r"Success rate is (\d+) percent \((\d+)/(\d+)\)", output)
        if not match:
            return {"destination": address, "success": False, "output": output}
        percent, sent, received = map(int, match.groups())
        return {"destination": address, "success": percent == 100 and sent > 0 and sent == received,
                "output": output}
    except Exception as exc:
        raise LabUnavailable("Connectivity probe failed or returned unsupported output") from exc
