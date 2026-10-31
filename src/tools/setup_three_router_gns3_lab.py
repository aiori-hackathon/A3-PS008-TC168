from pathlib import Path

from setup_gns3_smoke_lab import local_controller, request
from try_openwrt_gns3 import IMAGE, QEMU


PROJECT_NAME = "AIORI-PS008-Three-Router-Lab"


def main():
    if not (Path.home() / "GNS3/images/QEMU" / IMAGE).is_file():
        raise RuntimeError("OpenWrt router image is missing")
    with local_controller() as client:
        projects = [p for p in request(client, "GET", "projects") if p.get("name") == PROJECT_NAME]
        if len(projects) > 1:
            raise RuntimeError("Ambiguous lab project name")
        project = projects[0] if projects else request(client, "POST", "projects", {"name": PROJECT_NAME})
        base = f"projects/{project['project_id']}"
        request(client, "POST", f"{base}/open", {})
        existing = request(client, "GET", f"{base}/nodes")
        nodes = {}
        for index, name in enumerate(("R1", "R2", "R3")):
            matches = [n for n in existing if n.get("name") == name]
            if len(matches) > 1 or (matches and matches[0].get("node_type") != "qemu"):
                raise RuntimeError(f"Unexpected existing node: {name}")
            nodes[name] = matches[0] if matches else request(client, "POST", f"{base}/nodes", {
                "name": name, "node_type": "qemu", "compute_id": "local", "x": index * 260 - 260, "y": 0,
                "properties": {"hda_disk_image": IMAGE, "hda_disk_interface": "ide",
                               "qemu_path": QEMU, "platform": "x86_64", "ram": 256,
                               "adapters": 4, "adapter_type": "virtio-net-pci",
                               "console_type": "telnet", "options": "-nographic"},
            })
        links = request(client, "GET", f"{base}/links")
        for left, right, left_port, right_port in (("R1", "R2", 1, 1), ("R2", "R3", 2, 1)):
            endpoints = {nodes[left]["node_id"], nodes[right]["node_id"]}
            if not any({end.get("node_id") for end in link.get("nodes", [])} == endpoints for link in links):
                request(client, "POST", f"{base}/links", {"nodes": [
                    {"node_id": nodes[left]["node_id"], "adapter_number": left_port, "port_number": 0},
                    {"node_id": nodes[right]["node_id"], "adapter_number": right_port, "port_number": 0},
                ]})
        for name, node in nodes.items():
            if node.get("status") != "started":
                request(client, "POST", f"{base}/nodes/{node['node_id']}/start", {})
        observed = request(client, "GET", f"{base}/nodes")
        print({"project": PROJECT_NAME, "project_id": project["project_id"],
               "nodes": {node["name"]: {"id": node["node_id"], "status": node.get("status")}
                         for node in observed if node.get("name") in nodes},
               "links": len(request(client, "GET", f"{base}/links")),
               "router_image": IMAGE})


if __name__ == "__main__":
    main()
