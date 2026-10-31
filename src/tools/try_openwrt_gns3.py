"""Check whether this Windows GNS3 compute can boot a verified OpenWrt image.

Creates one isolated R1 node. A boot does not prove PS-008 device compatibility.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

from setup_gns3_smoke_lab import local_controller, request


PROJECT_NAME = "AIORI-PS008-OpenWrt-Probe"
IMAGE = "openwrt-25.12.0-x86-64-generic-ext4-combined.img"
QEMU = r"C:\Program Files\GNS3\qemu-3.1.0\qemu-system-x86_64.exe"


def main():
    if not (Path.home() / "GNS3/images/QEMU" / IMAGE).is_file():
        raise RuntimeError("Verified OpenWrt image is missing")
    with local_controller() as client:
        matches = [p for p in request(client, "GET", "projects") if p.get("name") == PROJECT_NAME]
        if len(matches) > 1:
            raise RuntimeError("Ambiguous OpenWrt probe projects")
        project = matches[0] if matches else request(client, "POST", "projects", {"name": PROJECT_NAME})
        project_id = project["project_id"]
        base = f"projects/{project_id}"
        request(client, "POST", f"{base}/open", {})
        matches = [n for n in request(client, "GET", f"{base}/nodes") if n.get("name") == "R1"]
        if len(matches) > 1 or (matches and matches[0].get("node_type") != "qemu"):
            raise RuntimeError("Unexpected existing R1 node")
        node = matches[0] if matches else request(client, "POST", f"{base}/nodes", {
            "name": "R1", "node_type": "qemu", "compute_id": "local", "x": 0, "y": 0,
            "properties": {"hda_disk_image": IMAGE, "hda_disk_interface": "ide",
                           "qemu_path": QEMU, "platform": "x86_64", "ram": 256,
                           "adapters": 4, "adapter_type": "virtio-net-pci",
                           "console_type": "telnet", "options": "-nographic"},
        })
        if node.get("status") != "started":
            request(client, "POST", f"{base}/nodes/{node['node_id']}/start", {})
        time.sleep(8)
        node = request(client, "GET", f"{base}/nodes/{node['node_id']}")
        print({"project_id": project_id, "node_id": node["node_id"],
               "status": node.get("status"), "console": node.get("console")})
        if node.get("status") != "started" or not isinstance(node.get("console"), int):
            raise RuntimeError("OpenWrt did not start in the local GNS3 QEMU compute")
        with socket.create_connection(("127.0.0.1", node["console"]), timeout=5) as sock:
            sock.settimeout(1)
            chunks = []
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                try:
                    chunks.append(sock.recv(4096))
                except socket.timeout:
                    continue
            output = b"".join(chunks).decode("utf-8", "replace")
            print("console_tail:", output[-2000:])


if __name__ == "__main__":
    main()
