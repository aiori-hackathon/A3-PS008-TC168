"""Create a small, isolated GNS3 VPCS connectivity lab and record real ping evidence.

This deliberately does not impersonate IOS routers or approve a route change.
"""

from __future__ import annotations

import configparser
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


PROJECT_NAME = "AIORI-PS008-Connectivity-Smoke"
NODE_ADDRESSES = {"PC-OPS": "10.99.0.10", "PC-ENG": "10.99.0.11"}


def local_controller():
    config_path = Path.home() / "AppData/Roaming/GNS3/2.2/gns3_server.ini"
    config = configparser.RawConfigParser()
    if not config.read(config_path, encoding="utf-8"):
        raise RuntimeError("GNS3 local server settings are unavailable")
    settings = config["Server"]
    if settings.get("auth", "false").lower() != "true":
        raise RuntimeError("This helper expects the authenticated local GNS3 controller")
    port = settings.getint("port", 3080)
    username, password = settings.get("user"), settings.get("password")
    if not username or not password:
        raise RuntimeError("GNS3 local controller credentials are not configured")
    return httpx.Client(base_url=f"http://127.0.0.1:{port}/v2/", auth=(username, password),
                        trust_env=False, timeout=12, follow_redirects=False)


def request(client, method, path, payload=None):
    response = client.request(method, path, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"GNS3 {method} {path.split('?')[0]} returned HTTP {response.status_code}")
    return response.json() if response.content else {}


def console_command(port, command, *, timeout=8):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        connection.settimeout(0.4)
        def drain(deadline):
            chunks = []
            while time.monotonic() < deadline:
                try:
                    part = connection.recv(8192)
                except socket.timeout:
                    continue
                if not part:
                    break
                chunks.append(part)
                if b"VPCS>" in part or b"PC1>" in part:
                    break
            return b"".join(chunks).decode("utf-8", "replace")

        drain(time.monotonic() + 2)
        connection.sendall((command + "\r\n").encode("ascii"))
        return drain(time.monotonic() + timeout)


def main():
    with local_controller() as client:
        version = request(client, "GET", "version").get("version")
        projects = request(client, "GET", "projects")
        matches = [p for p in projects if p.get("name") == PROJECT_NAME]
        if len(matches) > 1:
            raise RuntimeError("Multiple smoke projects have the same name")
        project = matches[0] if matches else request(client, "POST", "projects", {"name": PROJECT_NAME})
        project_id = project["project_id"]
        prefix = f"projects/{project_id}"
        request(client, "POST", f"{prefix}/open", {})
        nodes = request(client, "GET", f"{prefix}/nodes")
        selected = {}
        for index, name in enumerate(NODE_ADDRESSES):
            matching = [n for n in nodes if n.get("name") == name]
            if len(matching) > 1 or (matching and matching[0].get("node_type") != "vpcs"):
                raise RuntimeError(f"Unexpected existing node named {name}")
            if matching:
                node = matching[0]
            else:
                node = request(client, "POST", f"{prefix}/nodes", {
                    "name": name, "node_type": "vpcs", "compute_id": "local",
                    "x": -180 + 360 * index, "y": 0,
                })
            selected[name] = node
        links = request(client, "GET", f"{prefix}/links")
        ids = {node["node_id"] for node in selected.values()}
        has_link = any({endpoint.get("node_id") for endpoint in link.get("nodes", [])} == ids
                       for link in links)
        if not has_link:
            request(client, "POST", f"{prefix}/links", {"nodes": [
                {"node_id": selected[name]["node_id"], "adapter_number": 0, "port_number": 0}
                for name in NODE_ADDRESSES
            ]})
        for name, node in selected.items():
            if node.get("status") != "started":
                request(client, "POST", f"{prefix}/nodes/{node['node_id']}/start", {})
        nodes = request(client, "GET", f"{prefix}/nodes")
        selected = {n["name"]: n for n in nodes if n.get("name") in NODE_ADDRESSES}
        if len(selected) != 2 or any(n.get("status") != "started" for n in selected.values()):
            raise RuntimeError("The VPCS nodes did not start")

        for name, address in NODE_ADDRESSES.items():
            port = selected[name]["console"]
            reply = console_command(port, f"ip {address} 255.255.255.0")
            if address not in reply:
                raise RuntimeError(f"Address configuration was not confirmed on {name}")
            console_command(port, "save")
        ping = console_command(selected["PC-OPS"]["console"], "ping 10.99.0.11", timeout=14)
        replies = len(re.findall(r"bytes from 10\.99\.0\.11", ping, flags=re.I))
        evidence = {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": "controller-and-VPCS-observed", "controller_version": version,
            "project_name": PROJECT_NAME, "project_id": project_id,
            "nodes": {name: {"node_id": n["node_id"], "status": n["status"],
                             "ip": NODE_ADDRESSES[name]} for name, n in selected.items()},
            "link_present": True, "ping_replies": replies,
            "ping_passed": replies >= 1,
            "limitation": "VPCS connectivity checks the GNS3 controller and data link, not IOS route deployment",
        }
        destination = Path(".ps008/gns3-smoke-evidence.json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))
        if not evidence["ping_passed"]:
            raise SystemExit("No VPCS ping replies were observed")


if __name__ == "__main__":
    main()
