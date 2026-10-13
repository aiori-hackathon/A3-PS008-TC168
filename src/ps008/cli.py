import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .deployment import DeploymentManager
from .lab import DigitalTwin, Route
from .state import StateStore
from .workflow import run_workflow

ADD_REQUEST = {
    "intent_type": "add_static_route", "source": "R1", "destination": "10.20.0.0/24",
    "next_hop": "10.0.1.2", "protocol": "static", "requested_change": "Connect R1 to the engineering network",
    "request_id": "demo-add-001",
}


def _present(result: dict) -> dict:
    return {
        "stage": result["stage"],
        "candidate": {
            "request_id": result["candidate"].request.request_id,
            "router": result["candidate"].router,
            "commands": list(result["candidate"].commands),
            "change_summary": result["candidate"].change_summary,
            "base_revision": result["candidate"].base_revision,
        },
        "validation": asdict(result["validation"]),
        "deployment": asdict(result["deployment"]) if result["deployment"] else None,
        "state": result["state"],
        "revision": result["revision"],
    }


def run_scenarios(scenario: str) -> list[dict]:
    selected = {"success", "remove", "rejected", "invalid", "rollback"} if scenario == "full" else {scenario}
    results: list[dict] = []
    for name in ("success", "remove", "rejected", "invalid", "rollback"):
        if name not in selected:
            continue
        twin = DigitalTwin.baseline()
        manager = DeploymentManager(twin)
        data = dict(ADD_REQUEST)
        approved = True
        if name == "remove":
            twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
            data.update({
                "intent_type": "remove_static_route",
                "next_hop": None,
                "requested_change": "Remove the retired engineering network route",
                "request_id": "demo-remove-001",
            })
        elif name == "rejected":
            approved = False
            data["request_id"] = "demo-rejected-001"
        elif name == "invalid":
            data["next_hop"] = "192.168.1.1"
            data["request_id"] = "demo-invalid-001"
        elif name == "rollback":
            manager.fail_next = True
            data["request_id"] = "demo-rollback-001"
        result = run_workflow(data, lambda _: approved, twin, manager)
        results.append({"scenario": name, **_present(result)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="PS-008 reviewed network configuration agent")
    parser.add_argument("request", nargs="?", help="JSON request file")
    parser.add_argument("--state-file", default=".ps008/state.json", help="local simulated lab state file")
    parser.add_argument("--demo", action="store_true", help="run all demonstration scenarios")
    parser.add_argument("--web", action="store_true", help="serve the local review dashboard")
    parser.add_argument("--port", type=int, default=8000, help="local dashboard port (default 8000)")
    parser.add_argument("--gns3-inventory", help="local inventory for GNS3 dashboard/device review or discovery")
    parser.add_argument("--observe-ios", action="store_true", help="collect read-only IOS evidence over SSH in CLI mode")
    parser.add_argument(
        "--scenario",
        choices=("success", "remove", "rejected", "invalid", "rollback", "full"),
        help="run a specific non-interactive scenario",
    )
    args = parser.parse_args()

    if args.observe_ios and not args.gns3_inventory:
        parser.error("--observe-ios requires --gns3-inventory")
    if args.gns3_inventory and not args.web:
        if args.web or args.demo or args.scenario or args.request:
            parser.error("GNS3 observation cannot be combined with simulator actions")
        from .gns3 import Inventory, discover
        from .device_observation import observe_ios
        try:
            inventory = Inventory.load(args.gns3_inventory)
            result = discover(inventory)
            if args.observe_ios:
                if any(d["status"] != "started" for d in result["devices"]):
                    raise ValueError("All selected nodes must be started before SSH observation")
                result["observations"] = [observe_ios(d) for d in inventory.devices]
        except (OSError, ValueError):
            parser.exit(2, "error: GNS3 observation unavailable; check inventory, dependencies, connection and credentials. No deployment attempted.\n")
        print(json.dumps(result, indent=2))
        return 0

    if args.web:
        import uvicorn
        from .web import create_app
        if args.observe_ios:
            parser.error("Use the GNS3 dashboard observation button with --web")
        uvicorn.run(create_app(args.state_file, gns3_inventory=args.gns3_inventory), host="127.0.0.1", port=args.port)
        return 0

    if args.demo or args.scenario:
        print(json.dumps(run_scenarios(args.scenario or "full"), indent=2))
        return 0
    if not args.request:
        parser.error("provide a JSON request file or use --demo/--scenario")

    try:
        data = json.loads(Path(args.request).read_text(encoding="utf-8"))
        store = StateStore(args.state_file)
        twin = store.load()
        manager = DeploymentManager(twin, store)
        result = run_workflow(
            data,
            lambda summary: input(f"{summary}\nType yes to approve: ").strip().lower() in {"y", "yes"},
            twin,
            manager,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(_present(result), indent=2))
    return 0 if result["deployment"] and result["deployment"].success else 1


if __name__ == "__main__":
    raise SystemExit(main())
