# Implementation and architecture

This document describes the software-twin simulator. A separate approval-gated [GNS3 device path](GNS3_SETUP.md) is implemented and offline/mock tested, but has not been validated on a real router image or lab. Optional language interpretation remains deferred; see [GNS3 architecture and current gates](GNS3_ARCHITECTURE.md).

## Stack and entry points

Python 3.10+ implements parsing, generation, forwarding simulation, deployment, persistence, and reviews. FastAPI/Uvicorn serves the local API; Jinja2 renders the page shell; JavaScript and SVG render evidence without a frontend build. Pytest, HTTPX, and Playwright exercise the core, API, and browser. ReportLab produces the PDF.

`python -m ps008 --web` serves at `127.0.0.1:8000`. Existing JSON CLI and non-interactive demos remain available. Web assets are included in the distribution.

![Architecture](images/architecture.svg)

## Review lifecycle

Both interfaces use `ReviewService.prepare_review`, `approve_review`, and `reject_review`.

Preparation parses an immutable request, generates an immutable candidate at the current revision, and validates a cloned twin. It persists a review/audit event without changing routes. Reviews contain normalized input, exact commands, summary, revision, fingerprint, validation evidence, timestamps, and eventual result.

A valid review is `pending` for 15 minutes. It can become `deployed`, `rejected`, `expired`, `stale`, `invalid`, `failed`, or `rolled_back`. Demo resets cancel pending demo reviews. Terminal reviews cannot deploy again. Status retrieval records expiry.

Approval reloads the lab under the OS lock, checks fingerprint/revision, revalidates, and invokes deployment under the same lock. Deployment independently reconstructs commands and checks their allowlist. The browser sends only a review ID for a decision.

## Forwarding model

Links: R1-R2 `10.0.1.0/24`, R2-R3 `10.0.2.0/24`. LANs: R1 `10.10.0.0/24`, R2 `10.30.0.0/24`, R3 `10.20.0.0/24`. Bootstrap routes are fixed infrastructure, displayed separately from managed routes. Removal targets only managed routes.

Forwarding chooses the longest prefix across connected/static routes, preferring connected on equal prefixes. An exact managed prefix overrides its bootstrap prefix. Traces stop at a connected network, missing route, invalid neighbor, or repeated router.

Validation partitions the requested prefix at every relevant connected/static boundary. One address per interval plus endpoints tests all distinct forwarding choices. Additions must reach the destination for every interval; resulting loops are blocked. Removal may lose reachability and produces an explicit approval warning.

`DigitalTwin.baseline()` enables the complete model. The low-level `DigitalTwin()` constructor retains original topology-only behavior for compatibility with unit consumers. New persistent labs, user interfaces, and version 1 files loaded from disk use the modeled baseline. Managed route tables preserve their public shape.

## Deployment and recovery

Deployment journals the full pre-change payload, applies the route, verifies presence/absence and addition reachability, then increments revision, records the applied ID and result, atomically replaces state, and removes the journal.

Exceptions restore routes, revision, and applied IDs while retaining outcome evidence. An interrupted pending journal is recovered on the next locked load; its review is marked rolled back and recovery is audited. An interruption between state replacement and journal removal conservatively restores the previous state.

Version 2 JSON contains `version`, `revision`, `routes`, `applied_request_ids`, `modeled`, `reviews`, and `audit`. Version 1 loads compatibly. State/demo files are ignored by Git. Reads and mutations use the state lock; approval never deploys cached web state.

## Demo isolation and boundaries

Persistent state uses `--state-file`. Each browser session gets a separate file under the parent's `demos/` directory. Presets reset only that demo, retain its audit, and cancel pending reviews. Failure injection is trusted preset behavior outside the operator schema.

The model covers forwarding decisions, not IOS execution, packet timing, dynamic protocols, TCP/application sessions, or real router access. The local filesystem is trusted. The durable audit is not cryptographically tamper-proof.
