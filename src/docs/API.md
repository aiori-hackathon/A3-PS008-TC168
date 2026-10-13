# Local HTTP API

The dashboard and API share one loopback origin. Obtain a cookie and CSRF token with `GET /api/session`. Mutation requests require that cookie, matching `Origin`, and `X-CSRF-Token`. Bodies are limited to 16 KiB.

## Endpoints

- `GET /api/session`: CSRF token and selected workspace.
- `GET /api/lab`: revision, managed tables, topology/connected/bootstrap routes, and workspace.
- `POST /api/workspace`: `{ "mode": "lab" }` or `{ "mode": "demo" }`.
- `GET /api/trace?source=R1&destination=10.20.0.10`: path, outcome, and forwarding steps.
- `POST /api/reviews`: prepare a persistent review without applying its candidate.
- `GET /api/reviews/{review_id}`: current review in the selected workspace.
- `POST /api/reviews/{review_id}/approve`: approve and synchronously deploy the stored candidate.
- `POST /api/reviews/{review_id}/reject`: persist rejection without changing routes.
- `GET /api/audit`: ordered audit events, downloadable through the UI.
- `POST /api/demo/scenarios/{name}`: reset this session's demo and prepare a review. Names: `success`, `remove`, `rejected`, `conflict`, `loop`, `rollback`, `verification`. Requires demo workspace; approval remains explicit.

## Operator request

```json
{
  "intent_type": "add_static_route",
  "source": "R1",
  "destination": "10.20.0.0/24",
  "next_hop": "10.0.1.2",
  "protocol": "static",
  "requested_change": "Connect operations to engineering",
  "request_id": "request-001"
}
```

Removal uses `remove_static_route` and an omitted/null next hop. CIDRs normalize. Unknown fields, raw commands, unsupported values, and malformed IPv4 input are rejected. Reasons are nonempty with a 2,000-character limit; request IDs are nonempty with a 120-character limit.

## Review and result

Reviews expose `review_id`, `candidate`, `fingerprint`, `validation`, `status`, `created_at`, `expires_at`, and `deployment`. Candidate contains the normalized request, router, commands, summary, and base revision. Validation contains `valid`, errors, warnings, affected routes, checks, and impact with before/after tables and paired traces.

Deployment is null before a decision, then contains `success`, `message`, and `rolled_back`. Trace outcomes: `reached`, `missing_route`, `invalid_adjacency`, and `loop`. Reaching a modeled connected network is the success boundary.

Malformed input returns 422. A well-formed request failing network validation returns an `invalid` review. Missing reviews return 404. Stale/expired/consumed decisions return 409. Origin/CSRF failures return 403, oversized bodies 413, and unavailable filesystem storage 503. Deployment failures return a review with failure/rollback evidence.

The application is for one trusted local operator. It has no public listener, remote-device connector, raw-command endpoint, or public fault-injection field.

## GNS3 evidence and device review API

Run the web server with `--gns3-inventory .ps008/gns3-inventory.json`. These endpoints use the same local Origin/CSRF/body-size rules as simulator writes.

- `GET /api/gns3/status`: evidence summary, deployment readiness, and the separate device-operation journal.
- `POST /api/gns3/capture`: `{ "mode": "controller" | "device" | "rehearsal" }`.
- `POST /api/gns3/preview`: the same structured intent as above; returns modeled impact and the captured evidence reference, but does not write to devices.
- `POST /api/gns3/reviews`: creates a pending operation from valid, fresh, complete device-observed evidence.
- `GET /api/gns3/reviews/{operation_id}`: candidate, evidence timestamp, status, command result, verification, recovery.
- `POST /api/gns3/reviews/{operation_id}/approve`: re-observes controller and all three routers, then applies only the stored allowlisted command if the review remains fresh.
- `POST /api/gns3/reviews/{operation_id}/reject`: permanently rejects that pending operation.
- `POST /api/gns3/reviews/{operation_id}/reconcile`: for an unresolved operation, performs read-only fresh observation and resolves only if the router is in the approved or exact pre-change state; never retries a command.
- `GET /api/gns3/evidence`: exportable latest capture and operation journal.

The operation states include `pending`, `rejected`, `stale`, `applying`, `verified`, `failed`, `recovery_required`, and `outcome_unknown`. An unresolved state blocks additional writes. Addition verification requires route read-back plus a successful configured source-router ping. Removal requires proof the route is app-managed and verifies its absence. A timeout triggers reconciliation, never blind retry. All checks in the automated suite use fakes; no live GNS3 device has yet verified this path.
