"""Typed review lifecycle shared by the simulator and live device adapter."""
from typing import Protocol, TypedDict, Literal, cast, runtime_checkable


OperationStatus = Literal[
    "pending", "invalid", "rejected", "stale", "applying", "approved",
    "verified", "failed", "recovery_required", "outcome_unknown", "rolled_back",
    "cancelled", "expired",
]


class ReviewResult(TypedDict, total=False):
    status: OperationStatus
    candidate: dict
    validation: dict
    deployment: dict | None
    operation_id: str
    command_result: dict | None
    verification_result: dict | None
    recovery_result: dict | None


@runtime_checkable
class ReviewLifecycle(Protocol):
    """An approval flow accepts intent, exposes its stored review, and decides by ID."""

    def prepare(self, data: dict) -> ReviewResult: ...
    def get(self, review_id: str) -> ReviewResult: ...
    def decide(self, review_id: str, approved: bool) -> ReviewResult: ...


class SimulatorReviewBackend:
    """Typed facade over the existing persistent software-twin workflow."""

    def __init__(self, service):
        self.service = service

    def prepare(self, data: dict) -> ReviewResult:
        return cast(ReviewResult, self.service.prepare_review(data))

    def get(self, review_id: str) -> ReviewResult:
        return cast(ReviewResult, self.service.get_review(review_id))

    def decide(self, review_id: str, approved: bool) -> ReviewResult:
        result = (self.service.approve_review(review_id) if approved
                  else self.service.reject_review(review_id))
        return cast(ReviewResult, result)


class GNS3ReviewBackend:
    """Typed facade over the journaled, observed-device approval workflow."""

    def __init__(self, console):
        self.console = console

    def prepare(self, data: dict) -> ReviewResult:
        return cast(ReviewResult, self.console.prepare_operation(data))

    def get(self, review_id: str) -> ReviewResult:
        return cast(ReviewResult, self.console.get_operation(review_id))

    def decide(self, review_id: str, approved: bool) -> ReviewResult:
        return cast(ReviewResult, self.console.decide_operation(review_id, approved))

    def reconcile(self, review_id: str) -> ReviewResult:
        return cast(ReviewResult, self.console.reconcile_operation(review_id))
