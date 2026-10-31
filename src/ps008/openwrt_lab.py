"""Read-only evidence from the local, isolated OpenWrt GNS3 demonstration lab."""

import configparser
import hashlib
import json
import os
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ID = os.environ.get("PS008_OPENWRT_PROJECT_ID", "768343e9-a388-4110-a6d6-8b8f40bd4e48")
PROJECT_NAME = "AIORI-PS008-Three-Router-Lab"
NODE_NAMES = ("R1", "R2", "R3")


def _controller():
    import httpx
    settings = configparser.RawConfigParser()
    path = Path.home() / "AppData/Roaming/GNS3/2.2/gns3_server.ini"
    if not settings.read(path, encoding="utf-8"):
        raise ValueError("Local GNS3 server settings are unavailable")
    server = settings["Server"]
    if server.get("auth", "false").lower() != "true":
        raise ValueError("Expected authenticated local GNS3 controller")
    return httpx.Client(base_url=f"http://127.0.0.1:{server.getint('port', 3080)}/v2/",
                        auth=(server.get("user"), server.get("password")),
                        timeout=8, trust_env=False)


def _command(port, command):
    """Send one fixed observation command to a localhost GNS3 console."""
    with socket.create_connection(("127.0.0.1", port), timeout=4) as stream:
        stream.settimeout(0.3)
        def read_for(seconds):
            end = time.monotonic() + seconds
            parts = []
            while time.monotonic() < end:
                try:
                    part = stream.recv(4096)
                except socket.timeout:
                    continue
                if not part:
                    break
                parts.append(part)
                if sum(map(len, parts)) > 24000:
                    break
            return b"".join(parts).decode("utf-8", "replace")
        stream.sendall(b"\n")
        read_for(1)
        stream.sendall((command + "\n").encode("ascii"))
        return read_for(7 if command.startswith("ping ") else 2).strip()[-12000:]


def capture(ping=False):
    checked_at = datetime.now(timezone.utc).isoformat()
    with _controller() as client:
        project_response = client.get(f"projects/{PROJECT_ID}")
        project_response.raise_for_status()
        if project_response.json().get("name") != PROJECT_NAME:
            raise ValueError("Configured GNS3 project identity does not match")
        response = client.get(f"projects/{PROJECT_ID}/nodes")
        response.raise_for_status()
        nodes = response.json()
    observed = {}
    for name in NODE_NAMES:
        matches = [n for n in nodes if n.get("name") == name]
        if len(matches) != 1 or matches[0].get("node_type") != "qemu":
            raise ValueError(f"{name} is missing or is not a QEMU router")
        node = matches[0]
        result = {"status": node.get("status"), "node_id": node["node_id"]}
        if node.get("status") == "started" and isinstance(node.get("console"), int):
            port = node["console"]
            result["interfaces"] = _command(port, "ip -4 address show")
            result["routes"] = _command(port, "ip -4 route show")
            if ping and name == "R1":
                result["ping"] = _command(port, "ping -c 4 -W 2 10.0.2.2")
                match = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", result["ping"])
                result["ping_replies"] = {"sent": int(match[1]), "received": int(match[2])} if match else None
        observed[name] = result
    payload = {"origin": "gns3-openwrt-console-observed", "project": PROJECT_NAME,
               "project_id": PROJECT_ID, "checked_at": checked_at, "routers": observed,
               "ping_requested": ping, "route_write_performed": False}
    payload["sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload
