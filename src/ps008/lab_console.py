"""Persistent evidence console, deliberately separate from simulated deployment state."""
import json
import ipaddress
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .device_observation import observe_ios
from .device_observation import configure_ios, ping_ios
from .generator import parse_request
from .gns3 import Inventory, LabUnavailable, discover
from .observed_network import digest, normalize, preview
from .rehearsal import observations
from .state import StateStore


class LabConsole:
    def __init__(self, inventory_path, evidence_path, discoverer=discover, observer=observe_ios,
                 configurator=configure_ios, prober=ping_ios):
        self.inventory_path = Path(inventory_path) if inventory_path else None
        self.store = StateStore(evidence_path)
        self.operations = StateStore(self.store.path.with_name("gns3-operations.json"))
        self.execution = StateStore(self.store.path.with_name("gns3-device-execution.lock"))
        self.discoverer, self.observer = discoverer, observer
        self.configurator, self.prober = configurator, prober

    def _load(self):
        if not self.store.path.exists():
            return {"history": [], "latest": None, "last_attempt": None}
        return json.loads(self.store.path.read_text(encoding="utf-8"))

    def status(self):
        with self.store.locked():
            state = self._load()
        journal = self._operations()
        latest = state.get("latest")
        fresh_device_evidence = False
        if latest and latest.get("origin") == "device-observed" and latest.get("preview_ready") and state.get("last_attempt", {}).get("success"):
            fresh_device_evidence = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["captured_at"])).total_seconds() <= 900
        blocked = any(row.get("status") in {"applying", "recovery_required", "outcome_unknown"} for row in journal["items"])
        configured = bool(self.inventory_path and self.inventory_path.exists())
        all_routers = bool(latest and {n.get("device") for n in latest.get("nodes", [])} == {"R1", "R2", "R3"})
        return {**state, "configured": configured,
                "deployment_enabled": bool(configured and all_routers and fresh_device_evidence and not blocked),
                "device_deployment_implemented": True, "operation_journal": journal}

    def _operations(self):
        if not self.operations.path.exists():
            return {"version": 1, "items": [], "managed_routes": {}}
        value = json.loads(self.operations.path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("version") != 1 or
                not isinstance(value.get("items"), list) or not isinstance(value.get("managed_routes"), dict)):
            raise ValueError("Device operation journal is corrupt; device writes are blocked")
        return value

    def _fresh_device_state(self):
        if not self.inventory_path or not self.inventory_path.exists():
            raise ValueError("Live inventory is not configured")
        inventory = Inventory.load(self.inventory_path)
        if {device.name for device in inventory.devices} != {"R1", "R2", "R3"}:
            raise ValueError("Approved live changes require configured R1, R2 and R3 devices")
        controller = self.discoverer(inventory)
        if any(row.get("status") != "started" for row in controller.get("devices", [])):
            raise ValueError("All configured GNS3 routers must be started")
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            raw = list(pool.map(self.observer, inventory.devices))
        nodes = [normalize(row) for row in raw]
        if len(nodes) != 3 or {node["device"] for node in nodes} != {"R1", "R2", "R3"}:
            raise ValueError("Complete R1/R2/R3 device evidence is required")
        return inventory, nodes

    def capture(self, mode):
        if mode not in ("controller", "device", "rehearsal"):
            raise ValueError("Unknown observation mode")
        with self.store.locked():
            state = self._load()
            now = datetime.now(timezone.utc).isoformat()
            try:
                inventory = None
                if mode == "rehearsal":
                    controller = None
                    raw = observations()
                    origin = "synthetic-rehearsal"
                    identity = "rehearsal-v1"
                else:
                    if not self.inventory_path:
                        raise LabUnavailable("Start the dashboard with --gns3-inventory pointing to your local inventory")
                    inventory = Inventory.load(self.inventory_path)
                    controller = self.discoverer(inventory)
                    origin = "controller-observed" if mode == "controller" else "device-observed"
                    identity = digest(asdict(inventory))
                    raw = []
                    if mode == "device":
                        if any(d["status"] != "started" for d in controller["devices"]):
                            raise LabUnavailable("Start all configured routers in GNS3 first")
                        with ThreadPoolExecutor(max_workers=3) as pool:
                            raw = list(pool.map(self.observer, inventory.devices))
                nodes, issues = [], []
                for item in raw:
                    try:
                        nodes.append(normalize(item))
                    except ValueError as exc:
                        issues.append({"device": item["device"], "error": str(exc)})
                addresses = [i["address"] for n in nodes for i in n["interfaces"] if i["up"] and i["address"] != "unassigned"]
                if len(addresses) != len(set(addresses)):
                    issues.append({"device": "lab", "error": "Duplicate active interface IPs"})
                old = state["latest"]
                comparable = bool(old and old["origin"] == origin and old["inventory_fingerprint"] == identity and old["nodes"] and nodes and not issues and not old["issues"])
                previous = {n["device"]: n["fingerprint"] for n in old["nodes"]} if comparable else {}
                changed = [n["device"] for n in nodes if n["fingerprint"] != previous.get(n["device"])] if comparable else []
                evidence = {"captured_at": now, "origin": origin, "inventory_fingerprint": identity,
                            "controller": controller, "nodes": nodes, "raw": raw, "issues": issues,
                            "preview_ready": bool(nodes) and not issues and len(nodes) == len(raw),
                            "drift": {"comparable": comparable, "changed_devices": changed},
                            "deployment_enabled": False}
                evidence["digest"] = digest(evidence)
                state["latest"] = evidence
                state["history"] = (state["history"] + [{"at": now, "origin": origin, "digest": evidence["digest"], "changed_devices": changed}])[-20:]
                state["last_attempt"] = {"at": now, "mode": mode, "success": True}
                self.store._write(self.store.path, state)
                return evidence
            except (ValueError, OSError) as exc:
                state["last_attempt"] = {"at": now, "mode": mode, "success": False}
                self.store._write(self.store.path, state)
                raise LabUnavailable("Observation failed. Previous evidence is historical. Check inventory, GNS3, SSH host keys and credentials.") from exc

    def preview(self, data):
        request = parse_request(data)
        with self.store.locked():
            state = self._load()
        evidence = state["latest"]
        if not evidence or not evidence["preview_ready"] or not state["last_attempt"]["success"]:
            raise ValueError("Capture complete, supported evidence before creating a preview")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(evidence["captured_at"])).total_seconds()
        if age > 900:
            raise ValueError("Evidence is older than 15 minutes; capture a fresh snapshot")
        if evidence["origin"] != "synthetic-rehearsal":
            if not self.inventory_path or digest(asdict(Inventory.load(self.inventory_path))) != evidence["inventory_fingerprint"]:
                raise ValueError("Inventory changed; capture fresh evidence")
        return {**preview(evidence["nodes"], request), "origin": evidence["origin"],
                "captured_at": evidence["captured_at"], "evidence_digest": evidence["digest"]}

    def prepare_operation(self, data):
        request = parse_request(data)
        result = self.preview(data)
        if result["origin"] != "device-observed" or not result["valid"]:
            raise ValueError("Only a valid preview from live device-observed evidence can enter approval")
        inventory = Inventory.load(self.inventory_path)
        network = ipaddress.IPv4Network(request.destination)
        probes = [p for p in inventory.probes if p["source"] == request.source and
                  (request.intent_type == "remove_static_route" or
                   ipaddress.IPv4Address(p["destination"]) in network)]
        if request.intent_type == "add_static_route" and not probes:
            raise ValueError("Inventory must define a source-router endpoint probe inside the destination network")
        key = f"{request.source}|{request.destination}"
        with self.operations.locked():
            journal = self._operations()
            if any(row["status"] in {"applying", "recovery_required", "outcome_unknown"} for row in journal["items"]):
                raise ValueError("An unresolved device operation blocks further changes")
            if any(row["request_id"] == request.request_id for row in journal["items"]):
                raise ValueError("Duplicate request_id; reviews cannot be replayed")
            current = next(n for n in self.status()["latest"]["nodes"] if n["device"] == request.source)
            existing = next((r for r in current["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
            managed_record = journal["managed_routes"].get(key)
            managed_hop = managed_record.get("next_hop") if isinstance(managed_record, dict) else managed_record
            if request.intent_type == "remove_static_route":
                if not existing or managed_hop != existing["next_hop"]:
                    raise ValueError("Only an unchanged route previously added and verified by this agent can be removed")
            elif existing:
                raise ValueError("Route already exists; agent will not adopt or overwrite unmanaged routes")
            op_id = secrets.token_urlsafe(18)
            entry = {"operation_id": op_id, "request_id": request.request_id, "status": "pending",
                     "created_at": datetime.now(timezone.utc).isoformat(), "request": data,
                     "candidate": {"device": request.source, "node_id": next(d.node_id for d in inventory.devices if d.name == request.source),
                                   "commands": result["commands"]},
                     "preview": result, "pre_change_evidence": self.status()["latest"],
                     "evidence_timestamp": self.status()["latest"]["captured_at"],
                     "evidence_fingerprint": result["evidence_digest"], "inventory_fingerprint": digest(asdict(inventory)),
                     "probe": probes[0] if probes else None, "command_result": None, "verification_result": None,
                     "recovery_result": None}
            journal["items"].append(entry)
            self.operations._write(self.operations.path, journal)
            return entry

    def get_operation(self, operation_id):
        journal = self._operations()
        item = next((row for row in journal["items"] if row["operation_id"] == operation_id), None)
        if not item:
            raise ValueError("Operation not found")
        return item

    def reconcile_operation(self, operation_id):
        """Resolve an interrupted/uncertain journal entry using read-only device evidence."""
        with self.execution.locked():
            item = self.get_operation(operation_id)
            if item["status"] not in {"applying", "recovery_required", "outcome_unknown"}:
                raise ValueError("Only an unresolved device operation can be reconciled")
            try:
                inventory, nodes = self._fresh_device_state()
                if digest(asdict(inventory)) != item["inventory_fingerprint"]:
                    raise ValueError("Inventory changed; resolve the lab configuration manually")
                request = parse_request(item["request"])
                target = next(node for node in nodes if node["device"] == request.source)
                route = next((r for r in target["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
                before = next(node for node in item["pre_change_evidence"]["nodes"] if node["device"] == request.source)
                prior = next((r for r in before["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
                desired = ((route is not None and route["next_hop"] == request.next_hop)
                           if request.intent_type == "add_static_route" else route is None)
                unchanged = ((route is None and prior is None) or
                             (route is not None and prior is not None and route["next_hop"] == prior["next_hop"]))
                if desired:
                    probe = item.get("probe")
                    probe_result = self.prober(next(d for d in inventory.devices if d.name == request.source),
                                               probe["destination"]) if probe and request.intent_type == "add_static_route" else None
                    if request.intent_type == "add_static_route" and not (probe_result and probe_result.get("success") is True):
                        return self._update_operation(operation_id, status="recovery_required",
                            verification_result={"route_readback": "present", "probe": probe_result, "success": False},
                            recovery_result={"outcome": "recovery_required", "detail": "Route is present but configured probe is not confirmed; no command sent during reconcile"})
                    journal = self._operations()
                    key = f"{request.source}|{request.destination}"
                    if request.intent_type == "add_static_route":
                        journal["managed_routes"][key] = {"next_hop": request.next_hop,
                                                           "added_by_operation_id": operation_id}
                    else:
                        journal["managed_routes"].pop(key, None)
                    entry = next(row for row in journal["items"] if row["operation_id"] == operation_id)
                    entry.update(status="verified", verification_result={"route_readback": "present" if route else "absent",
                                 "probe": probe_result, "success": True},
                                 recovery_result={"outcome": "reconciled", "detail": "Observed state matches the approved operation"})
                    self.operations._write(self.operations.path, journal)
                    return entry
                if unchanged:
                    return self._update_operation(operation_id, status="failed",
                        verification_result={"route_state": "unchanged", "success": False},
                        recovery_result={"outcome": "not_required", "detail": "Fresh read-back confirms the approved change did not occur"})
                return self._update_operation(operation_id, status="recovery_required",
                    verification_result={"route_state": "unexpected", "success": False},
                    recovery_result={"outcome": "recovery_required", "detail": "Router state differs from both approved and prior state; manual repair required"})
            except Exception:
                return self._update_operation(operation_id, status="outcome_unknown",
                    recovery_result={"outcome": "outcome_unknown", "detail": "Fresh observation failed; no command sent during reconcile"})

    def _update_operation(self, operation_id, **changes):
        with self.operations.locked():
            journal = self._operations()
            item = next(row for row in journal["items"] if row["operation_id"] == operation_id)
            item.update(changes)
            self.operations._write(self.operations.path, journal)
            return item

    def decide_operation(self, operation_id, approve):
        with self.execution.locked():
            return self._decide_operation_locked(operation_id, approve)

    def _decide_operation_locked(self, operation_id, approve):
        if type(approve) is not bool:
            raise ValueError("approve must be a boolean")
        with self.operations.locked():
            journal = self._operations()
            item = next((row for row in journal["items"] if row["operation_id"] == operation_id), None)
            if not item or item["status"] != "pending":
                raise ValueError("Operation is missing or has already been decided")
            if not approve:
                item["status"] = "rejected"
                item["decided_at"] = datetime.now(timezone.utc).isoformat()
                self.operations._write(self.operations.path, journal)
                return item
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["created_at"])).total_seconds()
            if age > 900:
                item.update(status="stale", recovery_result={"outcome": "not_required", "detail": "Approval review expired after 15 minutes; no command sent"})
                self.operations._write(self.operations.path, journal)
                return item
            item["status"] = "applying"
            item["approved_at"] = datetime.now(timezone.utc).isoformat()
            self.operations._write(self.operations.path, journal)
        try:
            inventory, before_nodes = self._fresh_device_state()
            if digest(asdict(inventory)) != item["inventory_fingerprint"] or digest(before_nodes) != digest(item["pre_change_evidence"]["nodes"]):
                self._update_operation(operation_id, status="stale", recovery_result={"outcome": "not_required", "detail": "Evidence or inventory changed; no command sent"})
                return self.get_operation(operation_id)
            request = parse_request(item["request"])
            candidate = preview(before_nodes, request)
            if not candidate["valid"] or candidate["commands"] != item["candidate"]["commands"]:
                self._update_operation(operation_id, status="stale", recovery_result={"outcome": "not_required", "detail": "Candidate no longer matches observed state"})
                return self.get_operation(operation_id)
            device = next(d for d in inventory.devices if d.name == item["candidate"]["device"])
            try:
                command_result = self.configurator(device, item["candidate"]["commands"])
            except Exception as exc:
                command_result = {"error": str(exc), "result": "uncertain"}
            self._update_operation(operation_id, command_result=command_result)
            after_nodes = [normalize(x) for x in [self.observer(d) for d in inventory.devices]]
            target = next(n for n in after_nodes if n["device"] == request.source)
            route = next((r for r in target["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
            desired = (route is not None and route["next_hop"] == request.next_hop
                       if request.intent_type == "add_static_route" else route is None)
            if not desired:
                before_target = next(n for n in before_nodes if n["device"] == request.source)
                before_route = next((r for r in before_target["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
                if (route is None and before_route is None) or (route and before_route and route["next_hop"] == before_route["next_hop"]):
                    self._update_operation(operation_id, status="failed", verification_result={"route_state": "unchanged", "success": False})
                else:
                    self._update_operation(operation_id, status="recovery_required", verification_result={"route_state": "unexpected", "success": False}, recovery_result={"outcome": "recovery_required", "detail": "Observed route differs from both intended and prior state; no speculative command sent"})
                return self.get_operation(operation_id)
            probe = item["probe"]
            try:
                probe_result = self.prober(device, probe["destination"]) if probe and request.intent_type == "add_static_route" else None
            except Exception as exc:
                probe_result = {"success": False, "error": str(exc)}
            verified = request.intent_type == "remove_static_route" or bool(probe_result and probe_result.get("success") is True)
            verification = {"route_readback": "present" if request.intent_type == "add_static_route" else "absent",
                            "probe": probe_result, "success": verified}
            if not verified:
                recovered = self._recover(item, inventory, after_nodes)
                self._update_operation(operation_id, status="failed" if recovered["success"] else "recovery_required",
                                       verification_result=verification, recovery_result=recovered)
                return self.get_operation(operation_id)
            key = f"{request.source}|{request.destination}"
            with self.operations.locked():
                journal = self._operations()
                if request.intent_type == "add_static_route":
                    journal["managed_routes"][key] = {"next_hop": request.next_hop,
                                                       "added_by_operation_id": operation_id}
                else:
                    journal["managed_routes"].pop(key, None)
                entry = next(row for row in journal["items"] if row["operation_id"] == operation_id)
                entry.update(status="verified", verification_result=verification,
                             completed_at=datetime.now(timezone.utc).isoformat())
                self.operations._write(self.operations.path, journal)
            return self.get_operation(operation_id)
        except Exception as exc:
            current = self.get_operation(operation_id)
            status = "failed" if current.get("command_result") is None else "outcome_unknown"
            self._update_operation(operation_id, status=status,
                                   recovery_result={"outcome": status, "detail": "No command was sent" if current.get("command_result") is None else "Fresh router read-back failed; inspect the device manually before retrying"})
            return self.get_operation(operation_id)

    def _recover(self, item, inventory, observed):
        request = parse_request(item["request"])
        before = next(n for n in item["pre_change_evidence"]["nodes"] if n["device"] == request.source)
        prior = next((r for r in before["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
        device = next(d for d in inventory.devices if d.name == request.source)
        if request.intent_type == "add_static_route":
            command = f"no ip route {request.destination.split('/')[0]} {ipaddress.IPv4Network(request.destination).netmask} {request.next_hop}"
        elif prior:
            command = f"ip route {request.destination.split('/')[0]} {ipaddress.IPv4Network(request.destination).netmask} {prior['next_hop']}"
        else:
            return {"success": False, "outcome": "recovery_required", "detail": "Prior route evidence is missing"}
        try:
            self.configurator(device, [command])
            after = normalize(self.observer(device))
            route = next((r for r in after["routes"] if r["network"] == request.destination and r["kind"] == "static"), None)
            success = (route is None) if request.intent_type == "add_static_route" else bool(route and route["next_hop"] == prior["next_hop"])
            return {"success": success, "outcome": "restored" if success else "recovery_required", "command": command}
        except Exception:
            return {"success": False, "outcome": "recovery_required", "command": command}
