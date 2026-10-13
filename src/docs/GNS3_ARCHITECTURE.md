# GNS3 agent architecture and current status

Status: the GNS3 approval-gated route-change path is implemented in the repository and offline/mock tested. It has **not** been verified against a real router or GNS3 project. Local GNS3 2.2.61 was installed, but no server, image, or lab was available at implementation time. See [setup](GNS3_SETUP.md) for remaining real-device gates.

## Components

- `web.py` exposes the dashboard and API with same-origin, CSRF, and request-size controls.
- `backends.py` defines a typed `ReviewLifecycle` shared by simulator and GNS3 facades. `SimulatorReviewBackend` delegates to the existing twin review service; `GNS3ReviewBackend` delegates to the live device console.
- `gns3.py` validates inventory and reads controller project/node identity. It does not configure GNS3 nodes.
- `device_observation.py` uses Netmiko over SSH with system host-key verification for bounded IOS show commands, the one allowlisted route configuration, and IPv4 ping.
- `observed_network.py` strictly parses supported IOS output and predicts forwarding from captured tables. Unknown output blocks the live path.
- `lab_console.py` captures evidence, creates and decides device reviews, serializes writes, revalidates all observed routers, and stores outcomes separately from simulator state.
- `gns3-operations.json` under the configured state directory contains the operation record and managed-route ownership. It is local JSON, not a signed or tamper-proof log.

## Review-to-device sequence

1. Capture controller identity and SSH-observed state for R1/R2/R3. Each router must be started; observed OS, interface, and route output must match the supported formats.
2. Preview the structured `add_static_route` or `remove_static_route` request against the captured network. Modeled forwarding impact is labeled separately from actual device evidence.
3. Create a pending operation. The journal records the exact candidate, evidence snapshot and timestamp, fingerprints, probe selection, and operation ID. Rehearsal and controller-only evidence cannot create an executable operation.
4. On explicit approval, serialize the device operation, reject expired reviews, rediscover the configured project/nodes, re-read every router and compare normalized state/fingerprints and inventory. Any drift means stale and no write.
5. Validate the one stored command against the IPv4 static-route allowlist, journal the applying state, and then send it once over SSH. Browser content is never interpreted as a CLI command.
6. Re-read routes. An addition also requires a 100% source-router ping to a configured host inside the requested network. A removal checks exact route absence and is allowed only for an unchanged route this app previously added and verified.
7. If the command response is uncertain, reconcile read-back before deciding. If a probe fails after an addition, issue one scoped inverse for that candidate route and verify absence. Unexpected state, failed recovery, or unavailable read-back produces `recovery_required` or `outcome_unknown` and blocks further device writes.

The process lock serializes this application's operations. It cannot prevent an external operator changing a router between the final read and command; run only in an isolated lab/change window. This is not an atomic network transaction. After a crash, unresolved operations block writes until the dashboard's read-only reconcile action determines whether the approved route state or exact pre-change state is present. It never retries a configuration command; unexpected state remains blocked for manual repair.

## Supported scope and limitations

- GNS3 controller API v2/v3 as explicitly set in inventory; HTTP is limited to loopback, remote controllers require HTTPS.
- One Cisco IOS-compatible command/output family, over SSH with known host keys.
- Exactly R1, R2, R3 for device approval; one-router inventory remains useful for discovery/read-only observation.
- IPv4 static route add/remove only. No arbitrary commands, dynamic protocols, IPv6, ECMP, VRFs, interfaces as next hops, or unsupported parser formats.
- Source-defined IPv4 ping endpoints for route addition. A ping is an endpoint check from the router, not proof of application correctness.
- Removal is intentionally restricted to a route this operation journal marks as managed; the agent will not adopt or delete a pre-existing operator route.
- No OpenAI/LLM calls or API key handling are active. The language layer remains deferred.

## Test and evidence classification

The simulator tests exercise modeled twin behavior. GNS3 unit/API tests use faked controller, SSH, command, and probe responses; they establish application logic, not router compatibility. The browser run exercises rehearsal, preview, export, and layout. No controller-observed or device-observed live result is claimed until an actual configured GNS3 project is used and a route table plus endpoint probe are captured.

Remaining milestone gates are: create the isolated topology; configure independent management reachability, SSH users and verified host keys; capture exact parser-compatible IOS outputs; run approved addition and owned-route removal; test command rejection and live recovery; export the resulting evidence and label it device-observed.
