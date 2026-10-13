"""Launch the local GNS3 dashboard using the installed GNS3 credentials.

Credentials remain in the local server configuration and child-process
environment. The inventory contains only environment-variable references.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / ".ps008" / "gns3-openwrt-inventory.json"
SERVER_CONFIG = Path.home() / "AppData" / "Roaming" / "GNS3" / "2.2" / "gns3_server.ini"


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This launcher reads Windows GNS3 settings; configure controller credentials manually on other systems.")
    if not INVENTORY.is_file():
        raise SystemExit("Local GNS3 inventory is missing. Create it from docs/GNS3_SETUP.md before launching.")
    settings = configparser.RawConfigParser()
    if not settings.read(SERVER_CONFIG, encoding="utf-8") or "Server" not in settings:
        raise SystemExit("GNS3 local server settings were not found.")
    server = settings["Server"]
    if server.get("auth", "false").lower() != "true":
        raise SystemExit("GNS3 controller authentication is not enabled; refusing to guess its credentials.")
    username, password = server.get("user"), server.get("password")
    if not username or not password:
        raise SystemExit("GNS3 controller credentials are incomplete.")

    environment = os.environ.copy()
    environment["PS008_GNS3_USER"] = username
    environment["PS008_GNS3_PASS"] = password
    return subprocess.call(
        [sys.executable, "-m", "ps008", "--web", "--gns3-inventory", str(INVENTORY)],
        cwd=ROOT,
        env=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
