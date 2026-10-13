"""Persistent prepare/decide workflow shared by the CLI and HTTP interface."""
import copy
import hashlib
import json
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .deployment import DeploymentManager
from .generator import generate_candidate, parse_request
from .lab import DigitalTwin
from .schema import Candidate
from .state import StateStore, record_event


class ReviewConflict(ValueError):
    pass


class ReviewNotFound(KeyError):
    pass


def fingerprint(candidate):
    return hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReviewService:
    def __init__(self, store: StateStore | None = None, twin=None, manager=None, clock=None):
        self.store = store
        self.twin = twin if twin is not None else DigitalTwin.baseline()
        self.manager = manager
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _locked(self):
        return self.store.locked() if self.store else nullcontext()

    def _load(self):
        if self.store:
            self.twin = self.store._load_unlocked()
        return self.twin

    def _save(self):
        if self.store:
            self.store.save(self.twin)

    def lab(self):
        with self._locked():
            twin = self._load()
            return {"revision": twin.revision, "routes": twin.state(), "topology": twin.topology()}

    def trace(self, source, destination):
        with self._locked():
            return self._load().trace(source, destination)

    def audit(self):
        with self._locked():
            return copy.deepcopy(self._load().audit)

    def get_review(self, review_id):
        with self._locked():
            twin = self._load()
            if review_id not in twin.reviews:
                raise ReviewNotFound(review_id)
            review = twin.reviews[review_id]
            self._expire(review)
            return copy.deepcopy(review)

    def _expire(self, review):
        if review["status"] == "pending" and self.clock() >= datetime.fromisoformat(review["expires_at"]):
            review["status"] = "expired"
            record_event(self.twin, "expired", review["review_id"])
            self._save()

    def prepare_review(self, data, failure=None):
        request = parse_request(data)
        with self._locked():
            twin = self._load()
            existing = twin.exact_route(request.source, request.destination)
            candidate = generate_candidate(request, twin.revision, existing.next_hop if existing else None)
            validation = twin.validate(candidate)
            now = self.clock()
            review_id = uuid4().hex
            payload = asdict(candidate)
            payload["commands"] = list(candidate.commands)
            review = {
                "review_id": review_id, "candidate": payload,
                "fingerprint": fingerprint(payload), "validation": asdict(validation),
                "status": "pending" if validation.valid else "invalid",
                "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=15)).isoformat(),
                "deployment": None, "failure": failure,
            }
            twin.reviews[review_id] = review
            record_event(twin, "prepared" if validation.valid else "validation_failed", review_id,
                         request_id=request.request_id, errors=validation.errors)
            self._save()
            return copy.deepcopy(review)

    def _pending(self, review_id):
        if review_id not in self.twin.reviews:
            raise ReviewNotFound(review_id)
        review = self.twin.reviews[review_id]
        self._expire(review)
        if review["status"] != "pending":
            raise ReviewConflict(f"Review is {review['status']}; create a fresh review")
        return review

    def reject_review(self, review_id):
        with self._locked():
            self._load()
            review = self._pending(review_id)
            review["status"] = "rejected"
            review["deployment"] = {"success": False, "message": "Deployment rejected by human approval gate", "rolled_back": False}
            record_event(self.twin, "rejected", review_id)
            self._save()
            return copy.deepcopy(review)

    def approve_review(self, review_id):
        with self._locked():
            twin = self._load()
            review = self._pending(review_id)
            payload = review["candidate"]
            if fingerprint(payload) != review["fingerprint"]:
                raise ReviewConflict("Reviewed candidate integrity check failed")
            if payload["base_revision"] != twin.revision:
                review["status"] = "stale"
                record_event(twin, "stale", review_id)
                self._save()
                raise ReviewConflict("Lab changed since review; create a fresh review")
            request = parse_request(payload["request"])
            candidate = Candidate(request, payload["router"], tuple(payload["commands"]),
                                  payload["change_summary"], payload["base_revision"])
            validation = twin.validate(candidate)
            if not validation.valid:
                review["status"] = "invalid"
                review["validation"] = asdict(validation)
                record_event(twin, "validation_failed", review_id, errors=validation.errors)
                self._save()
                raise ReviewConflict("Candidate no longer passes validation")
            manager = self.manager or DeploymentManager(twin, self.store)
            if manager.twin is not twin:
                raise ValueError("Deployment manager must use the current workflow digital twin")
            if review.get("failure") == "apply":
                manager.fail_next = True
            elif review.get("failure") == "verification":
                manager.fail_verification = True
            record_event(twin, "approved", review_id)

            def finish(result):
                item = twin.reviews[review_id]
                item["status"] = "deployed" if result.success else "rolled_back" if result.rolled_back else "failed"
                item["deployment"] = asdict(result)
                item["result_revision"] = twin.revision
                record_event(twin, item["status"], review_id, message=result.message)

            result = manager.deploy(candidate, True, lock_held=True, review_id=review_id, on_result=finish)
            # Pre-apply security failures do not invoke the transaction callback.
            if twin.reviews[review_id]["status"] == "pending":
                finish(result)
                self._save()
            return copy.deepcopy(twin.reviews[review_id])
