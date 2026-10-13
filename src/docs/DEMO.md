# Judges' demonstration

## Preparation

Install as described in the README, run `python -m ps008 --web`, and open http://127.0.0.1:8000. Select **Judge demo**. Presets seed a known lab, so the sequence is repeatable without changing persistent state.

Explain that the two intents are provisional pending mentor confirmation. The routers and forwarding checks are a software twin; commands target one Cisco IOS-style platform.

## Suggested five-minute sequence

1. **Network and problem (40 seconds):** show R1-R2-R3/LANs and trace `10.20.0.10` from R1 to show the missing route.
2. **Successful addition (60 seconds):** choose **Add route**. Show the exact command, checks, and preview R1 -> R2 -> R3. Preview has not changed routes. Select **Approve & deploy**; show the managed route, verification, and audit.
3. **Human control (30 seconds):** choose **Rejection** and **Reject change**. Show unchanged routes and the rejection record.
4. **Unsafe changes (45 seconds):** choose **Conflict**, then **Loop**. Show errors and disabled approval. Conflict seeds an inconsistent managed route; loop seeds R2 pointing back to R1. These are controlled test fixtures.
5. **Rollback (60 seconds):** choose **Rollback**, note revision/table, and approve. Failure occurs after apply. Show restored routes, unchanged revision, and audit. Optionally repeat **Verification failure**.
6. **Removal and evidence (45 seconds):** choose **Remove route**, inspect its reachability warning, and approve. Show the missing path, export audit JSON, and show test evidence/report.

Scenario resets affect only the demo. Persistent lab retains operator requests across reloads/restarts. Do not describe preset resets as deployment outcomes.

## CLI fallback

```powershell
python -m ps008 --demo
python -m ps008 examples/add_route.json --state-file .ps008/presentation.json
python -m ps008 examples/remove_route.json --state-file .ps008/presentation.json
```

Use the repository virtual environment. Non-interactive CLI demos script approval outcomes for diagnostics. Interactive CLI and dashboard requests require an explicit decision; browser presets never auto-approve.

## GNS3 device demonstration (only after lab setup)

Use an isolated, disposable three-router GNS3 project and follow [GNS3_SETUP.md](GNS3_SETUP.md). First show controller/device evidence badges and the captured RIB. Preview the route and distinguish the modeled path prediction from device evidence. Create a review; before approval, point out that the operation journal contains the exact candidate and no command has been sent. Approve, then show IOS command result, route read-back, and the configured endpoint ping result. Capture again and show drift handling. Remove the route only after its app-managed ownership is visible. Demonstrate an interrupted operation with fakes in tests, not by intentionally disrupting a judge's live lab. Current browser screenshots and automated browser checks use synthetic rehearsal; they are not GNS3 proof.

## Questions to prepare for

- **Where is the AI?** This is a deterministic agent translating structured intent into an explainable candidate and evaluating explicit network rules. No LLM capability is claimed.
- **How is approval secured?** Stored command/revision are checked again under the lock; the decision sends only a review ID.
- **How is rollback verified?** Tests compare routes, revision, and applied IDs before/after failure; the dashboard exposes durable evidence and current state.
- **What does reachability mean?** Every relevant prefix interval reaches a connected network in the model. It does not prove real application availability.
- **What is the GNS3 status?** The device-review adapter is implemented and mock-tested, but live validation awaits a configured IOS-compatible project and permitted image. We will show actual read-back/probe evidence before calling it device-verified.
- **What is next?** Validate the selected image/parser and operation/recovery flow in the isolated GNS3 lab. The OpenAI language interface is deferred.
