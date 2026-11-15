"""Capture both live GNS3 links during a verified R1-to-R3 ICMP probe."""

import json
from pathlib import Path

from ps008.openwrt_lab import PROJECT_ID, _controller, capture


def main():
    with _controller() as client:
        base = f"projects/{PROJECT_ID}/links"
        response = client.get(base)
        response.raise_for_status()
        links = response.json()
        if len(links) != 2 or any(link.get("capturing") for link in links):
            raise RuntimeError("Expected exactly two idle GNS3 links in the isolated lab")
        started = []
        files = []
        try:
            for link in links:
                result = client.post(f"{base}/{link['link_id']}/start_capture", json={})
                result.raise_for_status()
                started.append(link["link_id"])
                files.append(result.json().get("capture_file_path"))
            evidence = capture(ping=True)
        finally:
            for link_id in started:
                response = client.post(f"{base}/{link_id}/stop_capture", json={})
                response.raise_for_status()
        replies = evidence["routers"]["R1"].get("ping_replies")
    destination = Path(".ps008/openwrt-packet-demo.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"evidence": evidence, "pcap_paths": files}, indent=2), encoding="utf-8")
    print({"ping_replies": replies, "pcap_paths": files, "evidence": str(destination)})
    if not replies or replies["received"] != replies["sent"]:
        raise RuntimeError("Packet demo ping did not receive all replies")
    if len(files) != 2 or any(not path or not Path(path).is_file() for path in files):
        raise RuntimeError("GNS3 packet capture files were not found")


if __name__ == "__main__":
    main()
