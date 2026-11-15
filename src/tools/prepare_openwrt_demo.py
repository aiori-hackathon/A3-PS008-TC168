"""Prepare the isolated three-router OpenWrt GNS3 demo and record actual ping results.

Run after setup_three_router_gns3_lab.py. The changes apply only to this GNS3
project's running routers and are repeated after a router restart.
"""

import json
import re
import time
from pathlib import Path

from ps008.openwrt_lab import PROJECT_ID, _command, _controller, capture


ADDRESSES = {
    "R1": (("eth1", "10.0.1.1/24"),),
    "R2": (("eth1", "10.0.1.2/24"), ("eth2", "10.0.2.1/24")),
    "R3": (("eth1", "10.0.2.2/24"),),
}


def wait_for_shell(port, name, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _command(port, "echo PS008_READY")
            return
        except (OSError, ValueError):
            print(f"{name}: waiting for OpenWrt console", flush=True)
            time.sleep(8)
    raise RuntimeError(f"{name} did not reach the OpenWrt shell within {timeout}s")


def main():
    with _controller() as client:
        response = client.get(f"projects/{PROJECT_ID}/nodes")
        response.raise_for_status()
        nodes = {node["name"]: node for node in response.json() if node.get("name") in ADDRESSES}
    if set(nodes) != set(ADDRESSES) or any(node.get("status") != "started" for node in nodes.values()):
        raise RuntimeError("Start R1, R2 and R3 in the three-router GNS3 project first")
    for name, addresses in ADDRESSES.items():
        port = nodes[name]["console"]
        wait_for_shell(port, name)
        print(f"{name}: configuring interfaces", flush=True)
        for interface, address in addresses:
            _command(port, f"ip link set {interface} up")
            existing = _command(port, f"ip -4 address show dev {interface}")
            if not re.search(rf"\binet {re.escape(address)}\b", existing):
                _command(port, f"ip address add {address} dev {interface}")
    # The experiment has no outside-facing link; permit transit across R2 only
    # for this isolated, ephemeral OpenWrt lab.
    r2 = nodes["R2"]["console"]
    print("R2: enabling forwarding", flush=True)
    _command(r2, "sysctl -w net.ipv4.ip_forward=1")
    _command(r2, "/etc/init.d/firewall stop")
    _command(nodes["R1"]["console"], "ip route replace 10.0.2.0/24 via 10.0.1.2")
    _command(nodes["R3"]["console"], "ip route replace 10.0.1.0/24 via 10.0.2.1")
    evidence = capture(ping=True)
    destination = Path(".ps008/openwrt-preparation-evidence.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    replies = evidence["routers"]["R1"].get("ping_replies")
    print({"project": evidence["project"], "ping_replies": replies,
           "evidence": str(destination), "sha256": evidence["sha256"]})
    if not replies or replies["received"] != replies["sent"]:
        raise RuntimeError("R1-to-R3 ping is not fully successful; inspect saved evidence before recording")


if __name__ == "__main__":
    main()
