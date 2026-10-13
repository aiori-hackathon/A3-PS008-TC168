# Implementation prompt: reviewed GNS3 network agent

Copy the text below into a coding-agent session opened in this repository. This is a future implementation task, not a claim that integration already exists.

---

Upgrade this AIORI PS-008 repository into a reviewed network-change agent with an optional GNS3 device backend and optional natural-language intent interpreter. Follow `docs/GNS3_ARCHITECTURE.md` as the proposed design and inspect the actual source before editing. Preserve the working simulator, CLI, dashboard, approval semantics and persisted-state compatibility.

First inspect repository instructions, Git status, `ps008/schema.py`, `generator.py`, `lab.py`, `reviews.py`, `deployment.py`, `state.py`, `workflow.py`, `web.py`, and existing tests. Inventory installed GNS3/server versions and runtime prerequisites through scoped read-only checks. Do not assume a router image, credentials, endpoint host or running lab exists. Ask early for missing lab identity, selected router OS/image, management addresses and credential setup while completing independent implementation work. Do not request plaintext secrets in chat.

Implement in milestones:

1. Introduce a typed backend boundary and simulator implementation. Preserve existing behavior; do not replace deterministic validation with LLM judgments.
2. Add explicit inventory and read-only GNS3/device discovery. Distinguish GNS3 controller API operations from SSH device configuration. Support one tested IOS platform first, using current compatible dependencies. Verify device identity and SSH host keys, use bounded timeouts and keep credentials outside version control. Do not expose arbitrary endpoint/CLI execution through public request fields.
3. Build the preview from observed supported topology/routes, or validate an explicitly configured topology against those observations. Reject incomplete or unsupported states; do not assume the static baseline matches a live lab.
4. Bind reviews to backend/project/device identities, configuration fingerprints and operation IDs. Approval submits only the stored review ID. Re-read relevant state before deployment, detect drift, enforce expiry/single use and serialize lab operations.
5. Implement only add/remove IPv4 static routes. Generate commands in code, capture pre-change state durably, apply the exact approved candidate, read back configuration/routes and execute source-defined probes against provisioned hosts with working return paths. Keep management connectivity separate.
6. Implement explicit recovery and outcome states. Local JSON restoration does not undo device changes. On uncertain transport results, reconcile before retrying. Restore only the scoped change and verify restoration; expose unknown outcomes and recovery failure honestly. Add startup reconciliation for interrupted device operations without changing simulator recovery semantics.
7. Add an optional natural-language proposal layer for English, Marathi and Hinglish. Use a replaceable pretrained model interface, strict output schema, clarification for missing/ambiguous information and deterministic validation. Model output cannot execute CLI, approve reviews or set operation identity. Treat device content as untrusted data. Provide a fake provider and maintain structured-form operation during provider outages. Before live provider work, resolve provider/model, credentials and permitted data sharing through the environment's applicable credential workflow. Do not train from scratch, hard-code an unverified model name, or enable paid calls by default.
8. Add backend and connection status to the UI, clearly distinguishing modeled preview from device evidence. Label planned/unavailable capabilities accurately and do not silently substitute simulation for failed live operations.

Choose a model through a held-out evaluation rather than claiming a universally best model. Measure field extraction, clarification, unsupported intents, adversarial inputs, latency and cost across English/Marathi/Hinglish. Record model/version and observed results. Do not invent scores or claim LLM reasoning makes deployment safe.

Test meaningful boundaries: invalid requests; rejected/expired/stale reviews; approval replay; fingerprint tampering; parser failure; unexpected device OS; unavailable devices; timeout after a possibly successful write; verification failure; successful and failed rollback; interrupted-operation reconciliation; unknown state; LLM malformed output, prompt injection and provider outage. Run existing regression checks after refactoring. Use offline fakes for routine tests and mark device integration tests opt-in. Do not claim live success from mocks.

Deliver updated architecture and setup documentation, sanitized inventory examples, supported image/version requirements, automated tests and a reproducible three-router demo guide. Record actual test results and any external prerequisites that prevented device validation. Do not fabricate a live GNS3 demonstration.

Commit only task-related files and push to the verified existing GitHub remote when authorized by the session. Preserve unrelated working files, credentials and existing generated artifacts. End by reporting implemented features, verification evidence, unresolved prerequisites and commit/push status. Keep completing independent code and documentation work when live-lab prerequisites are missing.
