from dataclasses import dataclass, field
from typing import Literal

IntentType = Literal["add_static_route", "remove_static_route"]


@dataclass(frozen=True)
class IntentRequest:
    intent_type: IntentType
    source: str
    destination: str
    next_hop: str | None = None
    protocol: str = "static"
    requested_change: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class Candidate:
    request: IntentRequest
    router: str
    commands: tuple[str, ...]
    change_summary: str
    base_revision: int | None = None


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    affected_routes: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    impact: dict = field(default_factory=dict)


@dataclass
class DeploymentResult:
    success: bool
    message: str
    rolled_back: bool = False
