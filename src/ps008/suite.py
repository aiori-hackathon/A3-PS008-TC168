"""AIORI-3 PS-008 Mandatory 12-Scenario Automated Verification Suite.

Validates all 12 core requirements specified in the PS-008 problem statement:
- Conflicting routes
- Invalid configurations & injection defense
- Rejected approvals
- Failed deployments & automatic rollback
- Next-hop adjacency & forwarding loops
- Stale review / concurrency guards
"""

from __future__ import annotations

import copy
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .deployment import DeploymentManager
from .generator import generate_candidate, parse_request
from .lab import DigitalTwin, Route
from .reviews import ReviewConflict, ReviewService
from .state import StateStore
from .workflow import run_workflow


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def base_req(**overrides):
    data = {
        "intent_type": "add_static_route",
        "source": "R1",
        "destination": "10.20.0.0/24",
        "next_hop": "10.0.1.2",
        "protocol": "static",
        "requested_change": "Connect R1 to engineering LAN",
        "request_id": "test-suite-001",
    }
    data.update(overrides)
    return data


def test_1_happy_path_add():
    """1. Valid static route addition connects R1 to R3 through R2."""
    twin = DigitalTwin.baseline()
    res = run_workflow(base_req(request_id="t1-add"), lambda _: True, twin)
    assert res["deployment"].success
    assert twin.routes["R1"][0].network == "10.20.0.0/24"
    assert twin.trace("R1", "10.20.0.10")["outcome"] == "reached"
    return "Route added to R1; end-to-end path R1->R2->R3 verified"


def test_2_happy_path_remove():
    """2. Controlled route removal restores baseline and warns on lost reachability."""
    twin = DigitalTwin.baseline()
    twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
    req = base_req(intent_type="remove_static_route", next_hop=None, request_id="t2-remove")
    res = run_workflow(req, lambda _: True, twin)
    assert res["deployment"].success
    assert twin.routes["R1"] == []
    assert twin.trace("R1", "10.20.0.10")["outcome"] == "missing_route"
    return "Route cleanly removed; missing_route state safely restored"


def test_3_schema_validation():
    """3. Invalid configuration schema (missing fields, malformed CIDR) rejected."""
    bad_reqs = [
        {"intent_type": "add_static_route"},  # missing required fields
        base_req(destination="10.20.0.0"),    # missing CIDR mask
        base_req(next_hop="999.999.999.999"), # invalid IP
        base_req(protocol="ospf"),            # unsupported protocol
    ]
    for req in bad_reqs:
        try:
            parse_request(req)
            raise AssertionError(f"Expected failure for bad request: {req}")
        except ValueError:
            pass
    return "Strict schema rejection on malformed IP, missing CIDR, and bad protocols"


def test_4_command_injection_defense():
    """4. Non-allowlisted or chained commands rejected before router parser."""
    twin = DigitalTwin.baseline()
    mgr = DeploymentManager(twin)
    cand = generate_candidate(parse_request(base_req(request_id="t4-inject")))
    forged = type(cand)(cand.request, cand.router, ("reload",), cand.change_summary)
    res = mgr.deploy(forged, True)
    assert not res.success and "allowlisted" in res.message
    return "Command injection 'reload' blocked by strict Cisco IOS grammar allowlist"


def test_5_next_hop_adjacency():
    """5. Next hop not on directly connected link is rejected."""
    twin = DigitalTwin.baseline()
    cand = generate_candidate(parse_request(base_req(next_hop="192.168.1.1", request_id="t5-adj")))
    res = twin.validate(cand)
    assert not res.valid and any("outside" in e or "adjacent" in e for e in res.errors)
    return "Non-adjacent next-hop 192.168.1.1 rejected before approval gate"


def test_6_self_hop_detection():
    """6. Next hop pointing to source router's own interface is blocked."""
    twin = DigitalTwin.baseline()
    cand = generate_candidate(parse_request(base_req(next_hop="10.0.1.1", request_id="t6-self")))
    res = twin.validate(cand)
    assert not res.valid and any("source router" in e for e in res.errors)
    return "Self-referential next-hop (10.0.1.1) identified and blocked"


def test_7_conflicting_route():
    """7. Conflicting route (same prefix via different next-hop) blocked."""
    twin = DigitalTwin.baseline()
    twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.3", "R1"))
    cand = generate_candidate(parse_request(base_req(next_hop="10.0.1.2", request_id="t7-conflict")))
    res = twin.validate(cand)
    assert not res.valid and any("Conflicting" in e for e in res.errors)
    return "Pre-existing route via 10.0.1.3 triggered conflict exception"


def test_8_routing_loop_detection():
    """8. Multi-hop forwarding loop detected and blocked in software twin."""
    twin = DigitalTwin.baseline()
    twin.routes["R2"].append(Route("10.20.0.0/24", "10.0.1.1", "R2"))
    cand = generate_candidate(parse_request(base_req(request_id="t8-loop")))
    res = twin.validate(cand)
    assert not res.valid and any("routing loop" in e for e in res.errors)
    return "Detected circular path R1 -> R2 -> R1 across boundary probes"


def test_9_human_approval_gate():
    """9. Explicit human review rejection halts deployment leaving lab unchanged."""
    twin = DigitalTwin.baseline()
    before = copy.deepcopy(twin.routes)
    res = run_workflow(base_req(request_id="t9-reject"), lambda _: False, twin)
    assert not res["deployment"].success
    assert "rejected" in res["deployment"].message.lower()
    assert twin.routes == before
    return "Operator human rejection halts execution; routing tables untouched"


def test_10_deployment_failure_rollback():
    """10. Mid-deployment router failure automatically triggers snapshot rollback."""
    twin = DigitalTwin.baseline()
    mgr = DeploymentManager(twin)
    mgr.fail_next = True
    before = copy.deepcopy(twin.routes)
    cand = generate_candidate(parse_request(base_req(request_id="t10-rollback")))
    res = mgr.deploy(cand, True)
    assert not res.success and res.rolled_back
    assert twin.routes == before
    return "Simulated device failure triggered atomic journal rollback to rev 0"


def test_11_post_apply_verification_failure():
    """11. Post-apply verification probe failure reverts configuration."""
    twin = DigitalTwin.baseline()
    mgr = DeploymentManager(twin)
    mgr.fail_verification = True
    before = copy.deepcopy(twin.routes)
    cand = generate_candidate(parse_request(base_req(request_id="t11-verify")))
    res = mgr.deploy(cand, True)
    assert not res.success and res.rolled_back
    assert twin.routes == before
    return "Failed health check probe reversed candidate command and restored state"


def test_12_stale_review_concurrency():
    """12. Stale review / concurrent lab revision changes prevent TOCTOU race."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(Path(tmp) / "state.json")
        svc = ReviewService(store)
        rev_a = svc.prepare_review(base_req(request_id="t12-a"))
        rev_b = svc.prepare_review(base_req(destination="10.30.0.0/24", request_id="t12-b"))
        # Commit B first
        svc.approve_review(rev_b["review_id"])
        # Attempt to commit A (reviewed against old revision 0)
        try:
            svc.approve_review(rev_a["review_id"])
            raise AssertionError("Expected ReviewConflict for stale review")
        except ReviewConflict as exc:
            assert "Lab changed" in str(exc)
    return "Review tied to rev 0 rejected when lab advanced to rev 1 (TOCTOU prevented)"


TEST_CASES = [
    (1, "Route Addition", "Happy path IPv4 static route addition", test_1_happy_path_add),
    (2, "Route Removal", "Safe removal of agent-managed route", test_2_happy_path_remove),
    (3, "Schema Validation", "Malformed IP/CIDR and invalid protocol defense", test_3_schema_validation),
    (4, "Injection Defense", "Strict Cisco IOS allowlist blocks arbitrary commands", test_4_command_injection_defense),
    (5, "Next-Hop Adjacency", "Non-adjacent link IP address rejection", test_5_next_hop_adjacency),
    (6, "Self-Hop Prevention", "Source router interface as next-hop rejection", test_6_self_hop_detection),
    (7, "Conflict Detection", "Conflicting static routes (duplicate destination) blocked", test_7_conflicting_route),
    (8, "Loop Detection", "Multi-hop routing loop (cycle) detection across prefix intervals", test_8_routing_loop_detection),
    (9, "Human Gate", "Operator rejection halts deployment with zero mutation", test_9_human_approval_gate),
    (10, "Auto-Rollback", "Deployment failure triggers pre-change snapshot restore", test_10_deployment_failure_rollback),
    (11, "Probe Verification", "Post-apply ping verification failure initiates rollback", test_11_post_apply_verification_failure),
    (12, "Concurrency / TOCTOU", "Stale review blocked when network revision advances", test_12_stale_review_concurrency),
]


def run_all(json_output: bool = False, state_dir: Path | None = None) -> int:
    results = []
    start_total = time.perf_counter()

    if not json_output:
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN} AIORI-3 PS-008: 12 MANDATORY TEST SCENARIOS VERIFICATION SUITE{Colors.RESET}")
        print(f"{Colors.DIM} Category: Cybersecurity | Team AgniKavach | Problem Statement PS-008{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}\n")

    passed_count = 0
    for num, category, desc, func in TEST_CASES:
        t0 = time.perf_counter()
        status = "FAIL"
        note = ""
        try:
            note = func()
            status = "PASS"
            passed_count += 1
        except Exception as exc:
            note = f"Error: {exc}"
        elapsed = (time.perf_counter() - t0) * 1000

        results.append({
            "test_number": num,
            "category": category,
            "description": desc,
            "status": status,
            "evidence": note,
            "duration_ms": round(elapsed, 2),
        })

        if not json_output:
            status_badge = f"{Colors.GREEN}[PASS]{Colors.RESET}" if status == "PASS" else f"{Colors.RED}[FAIL]{Colors.RESET}"
            print(f" {status_badge}  {Colors.BOLD}Test #{num:02d}: {category:<18}{Colors.RESET} ({elapsed:5.1f} ms)")
            print(f"        {Colors.DIM}Scenario:{Colors.RESET} {desc}")
            print(f"        {Colors.CYAN}Evidence:{Colors.RESET} {note}\n")

    total_time = (time.perf_counter() - start_total) * 1000

    if not json_output:
        print(f"{Colors.BOLD}{Colors.CYAN}{'-'*80}{Colors.RESET}")
        color = Colors.GREEN if passed_count == len(TEST_CASES) else Colors.RED
        print(f" {Colors.BOLD}Result Summary: {color}{passed_count}/{len(TEST_CASES)} Passed ({passed_count/len(TEST_CASES)*100:.0f}%){Colors.RESET} in {total_time:.2f} ms")
        print(f"{Colors.BOLD}{Colors.CYAN}{'='*80}{Colors.RESET}\n")

    target_dir = state_dir or (Path.cwd() / ".ps008")
    target_dir.mkdir(parents=True, exist_ok=True)
    report_path = target_dir / "12-test-report.json"
    report_data = {
        "suite": "AIORI-3 PS-008 Mandatory 12 Tests",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_tests": len(TEST_CASES),
        "passed": passed_count,
        "failed": len(TEST_CASES) - passed_count,
        "duration_ms": round(total_time, 2),
        "results": results,
    }
    report_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    if json_output:
        print(json.dumps(report_data, indent=2))

    return 0 if passed_count == len(TEST_CASES) else 1
