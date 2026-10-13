from collections.abc import Callable
from .deployment import DeploymentManager
from .generator import parse_request
from .lab import DigitalTwin
from .reviews import ReviewService
from .schema import Candidate, ValidationResult, DeploymentResult


def run_workflow(
    data: dict,
    approve: Callable[[str], bool],
    twin: DigitalTwin | None = None,
    manager: DeploymentManager | None = None,
):
    twin = twin or DigitalTwin.baseline()
    manager = manager or DeploymentManager(twin)
    if manager.twin is not twin:
        raise ValueError("Deployment manager must use the workflow digital twin")
    service = ReviewService(manager.store, twin, manager if manager.store is None else None)
    failure = "apply" if manager.fail_next else "verification" if manager.fail_verification else None
    review = service.prepare_review(data, failure=failure)
    payload = review["candidate"]
    candidate = Candidate(parse_request(payload["request"]), payload["router"], tuple(payload["commands"]),
                          payload["change_summary"], payload["base_revision"])
    validation = ValidationResult(**review["validation"])
    deployment = None
    if validation.valid:
        approved = approve(f"{candidate.change_summary}\nCommands: {', '.join(candidate.commands)}\nWarnings: {'; '.join(validation.warnings) or 'none'}")
        review = service.approve_review(review["review_id"]) if approved is True else service.reject_review(review["review_id"])
        if approved is True:
            manager.fail_next = manager.fail_verification = False
        deployment = DeploymentResult(**review["deployment"])
    current = service.twin
    twin.routes, twin.revision, twin.applied_request_ids = current.routes, current.revision, current.applied_request_ids
    twin.reviews, twin.audit = current.reviews, current.audit
    return {
        "stage": "deployment" if validation.valid else "validation", "candidate": candidate, "validation": validation,
        "deployment": deployment, "state": twin.state(), "revision": twin.revision,
    }
