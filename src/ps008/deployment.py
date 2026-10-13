from contextlib import nullcontext
from dataclasses import asdict

from .generator import generate_candidate, is_allowlisted, parse_request
from .lab import DigitalTwin
from .schema import Candidate, DeploymentResult
from .state import StateStore


class DeploymentManager:
    def __init__(self, twin: DigitalTwin, store: StateStore | None = None):
        self.twin = twin
        self.store = store
        self.fail_next = False
        self.fail_verification = False

    def deploy(self, candidate: Candidate, approved: bool, *, lock_held=False, review_id=None,
               on_result=None) -> DeploymentResult:
        with self.store.locked() if self.store is not None and not lock_held else nullcontext():
            return self._deploy(candidate, approved, review_id, on_result)

    def _deploy(self, candidate, approved, review_id, on_result):
        if approved is not True:
            return DeploymentResult(False, "Deployment rejected by human approval gate")
        if not candidate.commands or not all(is_allowlisted(command) for command in candidate.commands):
            return DeploymentResult(False, "Candidate contains a non-allowlisted command")
        try:
            if parse_request(asdict(candidate.request)) != candidate.request:
                raise ValueError("Candidate request is not canonical")
            existing = (
                self.twin.exact_route(candidate.router, candidate.request.destination)
                if candidate.request.intent_type == "remove_static_route" else None
            )
            expected = generate_candidate(
                candidate.request, candidate.base_revision,
                existing.next_hop if existing else None,
            )
        except (ValueError, TypeError) as exc:
            return DeploymentResult(False, f"Candidate request is invalid: {exc}")
        if candidate.router != expected.router or candidate.commands != expected.commands or candidate.change_summary != expected.change_summary:
            return DeploymentResult(False, "Candidate differs from the generated configuration")
        if candidate.base_revision is not None and candidate.base_revision != self.twin.revision:
            return DeploymentResult(False, "Candidate was reviewed against an outdated lab state")
        validation = self.twin.validate(candidate)
        if not validation.valid:
            return DeploymentResult(False, f"Candidate failed validation: {'; '.join(validation.errors)}")
        snapshot = self.twin.snapshot()
        before_revision = self.twin.revision
        before_request_ids = self.twin.applied_request_ids.copy()
        journal_started = False
        try:
            if self.store is not None:
                self.store.begin(self.twin, review_id)
                journal_started = True
            req = candidate.request
            self.twin.apply(candidate)
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("Simulated router deployment failure after apply")
            actual = self.twin.exact_route(candidate.router, req.destination)
            if self.fail_verification:
                self.fail_verification = False
                raise RuntimeError("Simulated post-apply verification failure")
            if req.intent_type == "add_static_route" and (actual is None or actual.next_hop != req.next_hop):
                raise RuntimeError("Post-apply route verification failed")
            if req.intent_type == "remove_static_route" and actual is not None:
                raise RuntimeError("Post-apply removal verification failed")
            if self.twin.modeled and req.intent_type == "add_static_route":
                if any(self.twin.trace(req.source, p)["outcome"] != "reached" for p in self.twin.probes(req.destination)):
                    raise RuntimeError("Post-apply forwarding verification failed")
            self.twin.revision += 1
            self.twin.applied_request_ids.add(req.request_id)
            result = DeploymentResult(True, "Deployment applied and verified successfully")
            if on_result:
                on_result(result)
            if self.store is not None:
                self.store.commit(self.twin)
            return result
        except Exception as exc:
            self.twin.restore(snapshot)
            self.twin.revision = before_revision
            self.twin.applied_request_ids = before_request_ids
            if self.store is not None and journal_started:
                try:
                    recovered = self.store.rollback()
                    self.twin.reviews = recovered.reviews
                    self.twin.audit = recovered.audit
                except Exception as rollback_error:
                    return DeploymentResult(False, f"Deployment failed: {exc}; persistent rollback failed: {rollback_error}")
            result = DeploymentResult(False, f"Deployment failed: {exc}", rolled_back=journal_started or self.store is None)
            if on_result:
                on_result(result)
                if self.store is not None:
                    self.store.save(self.twin)
            return result
