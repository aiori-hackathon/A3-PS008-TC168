import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from ps008.generator import generate_candidate, parse_request
from ps008.lab import DigitalTwin, Route
from ps008.reviews import ReviewConflict, ReviewService
from ps008.state import StateStore


def request(**changes):
    result = {"intent_type": "add_static_route", "source": "R1", "destination": "10.20.0.0/24",
              "next_hop": "10.0.1.2", "protocol": "static", "requested_change": "Connect engineering",
              "request_id": "review-test-001"}
    result.update(changes)
    return result


def test_baseline_trace_and_connected_network():
    twin = DigitalTwin.baseline()
    assert twin.trace("R1", "10.10.0.10")["outcome"] == "reached"
    assert twin.trace("R1", "10.20.0.10")["outcome"] == "missing_route"
    assert twin.trace("R2", "10.20.0.10")["path"] == ["R2", "R3"]


def test_preview_does_not_apply_and_reports_forwarding_change():
    service = ReviewService()
    before = service.lab()
    review = service.prepare_review(request())
    assert service.lab() == before
    traces = review["validation"]["impact"]["traces"]
    assert all(t["before"]["outcome"] == "missing_route" for t in traces)
    assert all(t["after"]["path"] == ["R1", "R2", "R3"] for t in traces)
    assert review["status"] == "pending"


@pytest.mark.parametrize("network", ["192.0.2.0/24", "10.0.0.0/8", "0.0.0.0/0"])
def test_unmodeled_destination_or_partial_reachability_is_rejected(network):
    review = ReviewService().prepare_review(request(destination=network))
    assert review["status"] == "invalid"
    assert any("reachable" in e for e in review["validation"]["errors"])


def test_probe_partition_detects_hidden_more_specific_loop():
    twin = DigitalTwin.baseline()
    twin.routes["R2"].append(Route("10.20.0.128/26", "10.0.1.1", "R2"))
    result = twin.validate(generate_candidate(parse_request(request())))
    assert not result.valid
    assert any(t["after"]["outcome"] == "loop" for t in result.impact["traces"])
    assert "10.20.0.128" in twin.probes("10.20.0.0/24")


def test_longest_prefix_overrides_bootstrap_route():
    twin = DigitalTwin.baseline()
    twin.routes["R2"].append(Route("10.20.0.128/25", "10.0.1.1", "R2"))
    assert twin.trace("R2", "10.20.0.10")["outcome"] == "reached"
    assert twin.trace("R2", "10.20.0.130")["outcome"] == "missing_route"
    assert twin.trace("R2", "10.20.0.130")["path"] == ["R2", "R1"]


def test_invalid_adjacency_trace():
    twin = DigitalTwin.baseline()
    twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.2.2", "R1"))
    assert twin.trace("R1", "10.20.0.10")["outcome"] == "invalid_adjacency"


def test_removal_warns_about_lost_reachability():
    service = ReviewService()
    added = service.prepare_review(request())
    assert service.approve_review(added["review_id"])["status"] == "deployed"
    remove = service.prepare_review(request(intent_type="remove_static_route", next_hop=None, request_id="remove-001"))
    assert remove["validation"]["valid"]
    assert any("loss of reachability" in w for w in remove["validation"]["warnings"])
    assert service.approve_review(remove["review_id"])["status"] == "deployed"
    assert service.trace("R1", "10.20.0.10")["outcome"] == "missing_route"


def test_removal_revealing_loop_is_rejected():
    twin = DigitalTwin.baseline()
    twin.routes["R1"] = [Route("10.20.0.0/24", "10.0.1.2", "R1")]
    twin.routes["R2"] = [Route("10.20.0.0/24", "10.0.1.1", "R2")]
    # Remove a more-specific route that had forwarded to a connected network.
    twin.routes["R2"].append(Route("10.20.0.0/25", "10.0.2.2", "R2"))
    review = ReviewService(twin=twin).prepare_review(request(intent_type="remove_static_route", source="R2",
                                                         destination="10.20.0.0/25", next_hop=None))
    assert review["status"] == "invalid"
    assert any("routing loop" in e for e in review["validation"]["errors"])


def test_duplicate_approval_is_consumed(tmp_path):
    service = ReviewService(StateStore(tmp_path / "state.json"))
    review = service.prepare_review(request())
    service.approve_review(review["review_id"])
    with pytest.raises(ReviewConflict, match="deployed"):
        service.approve_review(review["review_id"])
    assert service.lab()["revision"] == 1


def test_rejection_persists_without_changing_lab(tmp_path):
    service = ReviewService(StateStore(tmp_path / "state.json"))
    before = service.lab()
    review = service.prepare_review(request())
    service.reject_review(review["review_id"])
    restarted = ReviewService(service.store)
    assert restarted.get_review(review["review_id"])["status"] == "rejected"
    assert restarted.lab() == before
    with pytest.raises(ReviewConflict):
        restarted.approve_review(review["review_id"])


def test_expiry_is_recorded_and_cannot_deploy(tmp_path):
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    service = ReviewService(StateStore(tmp_path / "state.json"), clock=lambda: now)
    review = service.prepare_review(request())
    now += timedelta(minutes=15)
    with pytest.raises(ReviewConflict, match="expired"):
        service.approve_review(review["review_id"])
    assert service.audit()[-1]["action"] == "expired"
    assert service.lab()["revision"] == 0


def test_stale_review_cannot_apply_after_another_commit(tmp_path):
    service = ReviewService(StateStore(tmp_path / "state.json"))
    a = service.prepare_review(request())
    b = service.prepare_review(request(destination="10.30.0.0/24", request_id="second"))
    service.approve_review(b["review_id"])
    with pytest.raises(ReviewConflict, match="Lab changed"):
        service.approve_review(a["review_id"])
    assert service.get_review(a["review_id"])["status"] == "stale"
    assert service.lab()["routes"]["R1"][0]["network"] == "10.30.0.0/24"


def test_concurrent_approvals_commit_at_most_one(tmp_path):
    store = StateStore(tmp_path / "state.json")
    first = ReviewService(store)
    reviews = [first.prepare_review(request(request_id=f"concurrent-{i}")) for i in range(2)]
    def decide(review):
        try:
            return ReviewService(store).approve_review(review["review_id"])["status"]
        except ReviewConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, reviews))
    assert sorted(results) == ["conflict", "deployed"]
    assert store.load().revision == 1


def test_tampered_candidate_cannot_apply(tmp_path):
    store = StateStore(tmp_path / "state.json")
    service = ReviewService(store)
    review = service.prepare_review(request())
    with store.locked():
        twin = store._load_unlocked()
        twin.reviews[review["review_id"]]["candidate"]["commands"] = ["reload"]
        store.save(twin)
    with pytest.raises(ReviewConflict, match="integrity"):
        service.approve_review(review["review_id"])
    assert service.lab()["revision"] == 0


@pytest.mark.parametrize("failure", ["apply", "verification"])
def test_failure_restores_full_lab_and_persists_evidence(tmp_path, failure):
    store = StateStore(tmp_path / "state.json")
    service = ReviewService(store)
    before = store.load()
    snapshot = (before.state(), before.revision, before.applied_request_ids)
    review = service.prepare_review(request(), failure=failure)
    result = service.approve_review(review["review_id"])
    assert result["status"] == "rolled_back"
    after = store.load()
    assert (after.state(), after.revision, after.applied_request_ids) == snapshot
    assert ReviewService(store).get_review(review["review_id"])["deployment"]["rolled_back"]
    assert service.audit()[-1]["action"] == "rolled_back"


def test_recovery_marks_interrupted_review(tmp_path):
    store = StateStore(tmp_path / "state.json")
    service = ReviewService(store)
    review = service.prepare_review(request())
    with store.locked():
        twin = store._load_unlocked()
        store.begin(twin, review["review_id"])
        twin.routes["R1"].append(Route("10.20.0.0/24", "10.0.1.2", "R1"))
        store.save(twin)
    recovered = store.load()
    assert recovered.state()["R1"] == []
    assert recovered.reviews[review["review_id"]]["status"] == "rolled_back"
    assert recovered.audit[-1]["action"] == "recovered"


def test_version_one_state_loads_and_upgrades_on_review(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "revision": 0, "routes": {r: [] for r in ("R1", "R2", "R3")},
                                "applied_request_ids": []}), encoding="utf-8")
    service = ReviewService(StateStore(path))
    assert service.prepare_review(request())["status"] == "pending"
    assert json.loads(path.read_text())["version"] == 2


def test_connected_destination_cannot_be_overridden():
    result = ReviewService().prepare_review(request(destination="10.10.0.0/24"))
    assert result["status"] == "invalid"


@pytest.mark.parametrize("changes", [{"request_id": "x" * 121}, {"requested_change": "x" * 2001}])
def test_oversized_identifier_and_reason_are_rejected(changes):
    with pytest.raises(ValueError, match="exceeds"):
        ReviewService().prepare_review(request(**changes))


def test_non_boolean_approval_callback_cannot_authorize():
    from ps008.workflow import run_workflow
    result = run_workflow(request(), lambda _: "yes")
    assert not result["deployment"].success


def test_pending_review_can_be_approved_after_restart(tmp_path):
    store = StateStore(tmp_path / "state.json")
    review = ReviewService(store).prepare_review(request())
    restarted = ReviewService(StateStore(store.path))
    assert restarted.approve_review(review["review_id"])["status"] == "deployed"
    assert restarted.trace("R1", "10.20.0.10")["outcome"] == "reached"


def test_commit_storage_failure_rolls_back_without_false_recovery_event(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    service = ReviewService(store)
    before = service.lab()
    review = service.prepare_review(request())
    def fail_commit(twin):
        raise OSError("Disk write failed")
    monkeypatch.setattr(store, "commit", fail_commit)
    result = service.approve_review(review["review_id"])
    assert result["status"] == "rolled_back"
    assert "Disk write failed" in result["deployment"]["message"]
    assert service.lab() == before
    assert all(e["action"] != "recovered" for e in service.audit())
