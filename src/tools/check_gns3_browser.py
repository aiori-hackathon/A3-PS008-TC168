"""Browser checks for rehearsal, source labels, preview, export and narrow layouts."""
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="ps008-gns3-browser-") as temp:
        process = subprocess.Popen([sys.executable, "-m", "ps008", "--web", "--port", str(port),
                                    "--state-file", str(Path(temp) / "state.json")], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(base + "/api/session", timeout=1): break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("Test server did not start")
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/gns3")
                expect(page.locator("#connection")).to_contain_text("No inventory configured")
                expect(page.locator("#discover")).to_be_disabled()
                expect(page.locator("#preview")).to_be_disabled()
                page.locator("#rehearse").click()
                expect(page.locator("#origin")).to_have_text("SYNTHETIC REHEARSAL")
                expect(page.locator(".observed-node")).to_have_count(3)
                page.locator("#preview").click()
                expect(page.locator("#preview-result")).to_contain_text("R1 → R2 → R3")
                expect(page.locator("#preview-result")).to_contain_text("not live packet evidence")
                expect(page.locator("#preview-result")).to_contain_text("0 reachability regressions")
                page.locator("#hop").fill("192.0.2.20")
                page.locator("#preview").click()
                expect(page.locator("#notice")).to_contain_text("observed adjacent peer")
                expect(page.locator("#preview-result")).to_be_empty()
                page.locator("#rehearse").click()
                expect(page.locator("#drift")).to_contain_text("No normalized")
                with page.expect_download() as download:
                    page.locator("#export").click()
                data = json.loads(Path(download.value.path()).read_text())
                assert data["latest"]["origin"] == "synthetic-rehearsal"
                assert data["deployment_enabled"] is False
                page.reload()
                expect(page.locator(".observed-node")).to_have_count(3)
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Overflow at {width}px"
                page.set_viewport_size({"width": 1440, "height": 1000})
                page.locator("#hop").fill("10.0.1.2")
                page.locator("#preview").click()
                expect(page.locator("#preview-result")).to_contain_text("not live packet evidence")
                live_evidence = {"captured_at": "2026-10-12T08:00:00+00:00", "origin": "device-observed",
                    "inventory_fingerprint": "mock-inventory", "controller": {"project_id": "mock-project"},
                    "nodes": [], "raw": [], "issues": [], "preview_ready": True,
                    "drift": {"comparable": False, "changed_devices": []}, "deployment_enabled": True,
                    "digest": "mock-evidence-digest"}
                journal_items = []
                review_counter = 0
                def mocked_api(route):
                    nonlocal review_counter
                    request = route.request
                    path = request.url.split("?", 1)[0]
                    if path.endswith("/api/gns3/status"):
                        route.fulfill(json={"configured": True, "latest": live_evidence, "history": [],
                            "last_attempt": {"success": True}, "operation_journal": {"items": journal_items,
                            "managed_routes": {}}, "deployment_enabled": True})
                    elif path.endswith("/api/gns3/capture"):
                        route.fulfill(json=live_evidence)
                    elif path.endswith("/api/gns3/preview"):
                        route.fulfill(json={"valid": True, "origin": "device-observed",
                            "captured_at": live_evidence["captured_at"], "evidence_digest": live_evidence["digest"],
                            "summary": "Add static route 10.20.0.0/24 via 10.0.1.2 on R1",
                            "commands": ["ip route 10.20.0.0 255.255.255.0 10.0.1.2"],
                            "checks": [{"source": "R1", "destination": "10.20.0.10",
                                "before": {"path": ["R1"], "outcome": "missing_route"},
                                "after": {"path": ["R1", "R2", "R3"], "outcome": "connected_network"}}],
                            "regression_count": 0, "loop_count": 0, "scope": "Mocked modeled preview"})
                    elif path.endswith("/api/gns3/reviews") and request.method == "POST":
                        review_counter += 1
                        item = {"operation_id": f"mock-operation-{review_counter}", "status": "pending",
                            "candidate": {"device": "R1", "commands": ["ip route 10.20.0.0 255.255.255.0 10.0.1.2"]},
                            "evidence_timestamp": live_evidence["captured_at"], "command_result": None,
                            "verification_result": None, "recovery_result": None}
                        journal_items.append(item)
                        route.fulfill(json=item)
                    elif "/approve" in path:
                        item = journal_items[-1]
                        item.update(status="verified", command_result={"accepted": True, "mock": True},
                            verification_result={"route_readback": "present", "probe": {"success": True, "mock": True}, "success": True})
                        route.fulfill(json=item)
                    elif "/reject" in path:
                        item = journal_items[-1]
                        item.update(status="rejected")
                        route.fulfill(json=item)
                    else:
                        route.fulfill(status=404, json={"error": "unhandled mocked route"})
                for endpoint in ("status", "capture", "preview", "reviews", "reviews/**"):
                    page.route(f"**/api/gns3/{endpoint}", mocked_api)
                page.reload()
                page.locator("#observe").click()
                page.locator("#prefix").fill("10.20.0.0/24")
                page.locator("#hop").fill("10.0.1.2")
                page.locator("#preview").click()
                page.locator("#prepare-review").click()
                expect(page.locator("#review-result")).to_contain_text("PENDING")
                expect(page.locator("#review-result")).to_contain_text("Approve and apply to GNS3")
                page.locator("#approve-live").click()
                expect(page.locator("#review-result")).to_contain_text("VERIFIED")
                expect(page.locator("#review-result")).to_contain_text("route_readback")
                page.locator("#preview").click()
                page.locator("#prepare-review").click()
                page.locator("#reject-live").click()
                expect(page.locator("#review-result")).to_contain_text("REJECTED")
                (ROOT / "docs/images").mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(ROOT / "docs/images/gns3-evidence-dashboard.png"), full_page=True)
                assert not errors, errors
                browser.close()
            print("GNS3 dashboard browser checks passed: rehearsal, modeled preview, mocked device approval/rejection/read-back, invalid next hop, drift, export, 3 viewport sizes; zero page errors. No live device was contacted.")
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
