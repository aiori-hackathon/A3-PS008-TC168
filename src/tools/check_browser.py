"""Exercise actual browser decisions and capture genuine UI evidence."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def check_routing_layout(page, url, screenshots):
    """Regression: empty reviews must not let the intent card cover routing state."""
    viewports = [(1820, 900), (1440, 900), (1280, 720), (1024, 768), (390, 844)]
    for width, height in viewports:
        page.set_viewport_size({"width": width, "height": height})
        page.goto(url)
        expect(page.locator("#revision")).to_have_text("0")
        expect(page.locator("#workspace")).to_be_enabled()
        if width > 900:
            gap = page.evaluate("""() => {
                const form = document.querySelector('.intent-card').getBoundingClientRect();
                const routes = document.querySelector('.routes-card').getBoundingClientRect();
                return routes.top - form.bottom;
            }""")
            assert 21 <= gap <= 23, f"Unexpected card gap: {gap}px at {width}px"
        for fraction in (0, 0.5, 1):
            page.evaluate("fraction => scrollTo(0, fraction * (document.documentElement.scrollHeight - innerHeight))", fraction)
            assert page.evaluate("""() => {
                const a = document.querySelector('.intent-card').getBoundingClientRect();
                const b = document.querySelector('.routes-card').getBoundingClientRect();
                return !(Math.min(a.right, b.right) > Math.max(a.left, b.left)
                    && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top));
            }"""), f"Intent form overlaps routing card at {width}x{height}, scroll {fraction}"
        panel = page.get_by_role("region", name="Routing tables.", exact=True)
        panel.scroll_into_view_if_needed()
        assert panel.evaluate("el => el.scrollHeight > el.clientHeight"), "Routing panel has no vertical scroll area"
        panel.focus()
        panel.press("End")
        page.wait_for_function("() => document.getElementById('route-tables').scrollTop > 0")
        expect(panel.locator("tbody tr").last).to_be_in_viewport()
        panel.press("Home")
        page.wait_for_function("() => document.getElementById('route-tables').scrollTop === 0")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Routing layout overflows the page"
        if screenshots and width == 1820:
            page.screenshot(path=str(ROOT / "docs" / "images" / "routing-scroll.png"))
    page.set_viewport_size({"width": 1440, "height": 1100})
    page.goto(url)
    expect(page.locator("#workspace")).to_be_enabled()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", action="store_true")
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="ps008-browser-") as temp:
        process = subprocess.Popen([sys.executable, "-m", "ps008", "--web", "--port", str(port),
                                    "--state-file", str(Path(temp) / "state.json")],
                                   cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(url + "/api/session", timeout=1):
                        break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read().decode())
                    time.sleep(0.1)
            else:
                raise RuntimeError("Dashboard did not start")
            images = ROOT / "docs" / "images"
            images.mkdir(exist_ok=True)
            with sync_playwright() as playwright:
                # Capture scrollbars as users see them; headless Chromium hides them by default.
                browser = playwright.chromium.launch(ignore_default_args=["--hide-scrollbars"])
                page = browser.new_page(viewport={"width":1440, "height":1100}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.goto(url)
                expect(page.locator("#revision")).to_have_text("0")
                check_routing_layout(page, url, args.screenshots)
                # Keyboard users can move directly to the intent form.
                page.keyboard.press("Tab")
                expect(page.locator(".skip-link")).to_be_focused()
                page.keyboard.press("Enter")
                page.get_by_role("button", name="Generate & validate").click()
                expect(page.locator("#review-status")).to_have_text("pending")
                expect(page.locator("#commands")).to_contain_text("ip route 10.20.0.0 255.255.255.0 10.0.1.2")
                expect(page.locator("#approve")).to_be_enabled()
                page.reload()
                expect(page.locator("#revision")).to_have_text("0")
                page.locator("#audit [data-review]").first.click()
                expect(page.locator("#review-status")).to_have_text("pending")
                expect(page.locator("#approve")).to_be_enabled()
                if args.screenshots:
                    page.screenshot(path=str(images / "dashboard.png"), full_page=True)
                    page.locator(".review-card").screenshot(path=str(images / "review.png"))
                page.get_by_role("button", name="Approve & deploy").click()
                expect(page.locator("#review-status")).to_have_text("deployed")
                expect(page.locator("#trace-result")).to_contain_text("R1 → R2 → R3")
                expect(page.locator("#approve")).to_be_disabled()
                page.get_by_label("Workspace", exact=True).select_option("demo")
                expect(page.locator("#demo-panel")).to_be_visible()
                page.get_by_role("button", name="Rejection", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("pending")
                page.get_by_role("button", name="Reject change", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("rejected")
                for scenario in ("Conflict", "Loop"):
                    page.get_by_role("button", name=scenario, exact=True).click()
                    expect(page.locator("#review-status")).to_have_text("invalid")
                    expect(page.locator("#approve")).to_be_disabled()
                page.get_by_role("button", name="Rollback", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("pending")
                expect(page.locator("#approve")).to_be_enabled()
                revision = page.locator("#revision").inner_text()
                page.get_by_role("button", name="Approve & deploy").click()
                expect(page.locator("#review-status")).to_have_text("rolled back")
                expect(page.locator("#revision")).to_have_text(revision)
                expect(page.locator("#deployment-result")).to_contain_text("Snapshot restored")
                expect(page.locator("#workspace")).to_be_enabled()
                if args.screenshots:
                    page.screenshot(path=str(images / "rollback.png"), full_page=True)
                    page.locator(".review-card").screenshot(path=str(images / "rollback-review.png"))
                page.get_by_role("button", name="Verification failure", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("pending")
                page.get_by_role("button", name="Approve & deploy").click()
                expect(page.locator("#review-status")).to_have_text("rolled back")
                page.get_by_role("button", name="Remove route", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("pending")
                expect(page.locator("#validation")).to_contain_text("loss of reachability")
                page.get_by_role("button", name="Approve & deploy").click()
                expect(page.locator("#review-status")).to_have_text("deployed")
                # Persistent lab was never reset by these demo scenarios.
                page.get_by_label("Workspace", exact=True).select_option("lab")
                expect(page.locator("#trace-result")).to_contain_text("R1 → R2 → R3")
                page.reload()
                expect(page.locator("#revision")).to_have_text("1")
                # Exercise a complete decision on a narrow viewport too.
                page.set_viewport_size({"width":390, "height":844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows horizontally"
                page.get_by_label("Workspace", exact=True).select_option("demo")
                page.get_by_role("button", name="Add route", exact=True).click()
                expect(page.locator("#review-status")).to_have_text("pending")
                page.get_by_role("button", name="Approve & deploy").click()
                expect(page.locator("#review-status")).to_have_text("deployed")
                expect(page.locator("#workspace")).to_be_enabled()
                expect(page.locator("#trace-result")).to_contain_text("R1 → R2 → R3")
                if args.screenshots:
                    page.screenshot(path=str(images / "mobile.png"), full_page=True)
                assert not errors, errors
                browser.close()
            result = {"at":datetime.now(timezone.utc).isoformat(), "browser":"Chromium", "flows":["preview","review_reopen","approval","rejection","conflict","loop", "rollback", "verification_failure", "removal", "workspace_isolation", "reload", "keyboard_skip", "mobile_approval"], "layout_checks":{"viewports":["1820x900","1440x900","1280x720","1024x768","390x844"], "intent_route_overlap":False,"keyboard_route_scroll":"passed"}, "console_errors":errors, "result":"passed"}
        finally:
            if os.name == "nt" and process.poll() is None:
                # Windows venv launchers may own a second Python process.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    output = ROOT / "output" / "browser-results.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
