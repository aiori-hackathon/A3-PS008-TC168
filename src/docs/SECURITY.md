# Security boundaries

The parser accepts only documented static-route fields. IPv4 normalization and bounded nonempty reasons/IDs constrain generation. An exact IOS network/mask/next-hop allowlist restricts commands. Deployment reconstructs the candidate independently and rejects altered commands, routers, or summaries.

Validation checks neighbors, conflicts, request reuse, modeled reachability, and loops. Approval requires a pending unexpired review at the current revision, reloads state under the OS lock, and revalidates. Terminal reviews cannot deploy again.

HTTP mutations require matching origin and session-bound CSRF token. Cookies are HttpOnly and SameSite Strict; sessions last eight hours. The listener/accepted hosts are loopback. A restrictive content-security policy and content-type protection accompany responses. Dynamic UI HTML is escaped; exact commands/status messages render as text.

Snapshots and journals precede mutation. Post-apply verification precedes success. Failed/interrupted changes restore routes, revision, and applied IDs. Audit records preparation, validation failures, decisions, expiry, staleness, deployment, rollback, recovery, and demo resets. Trusted presets inject faults only in isolated demo files.

The operator and local filesystem are trusted. There is no multi-user authentication, separate approver role, encrypted storage, TLS termination, or tamper-proof audit. Filesystem compromise is outside the model. Keep the listener local and avoid public tunnels.

The simulator does not contact routers or execute IOS/shell commands. Bootstrap routes are fixed infrastructure. A failed persistent rollback is reported as failed; it must not be presented as restoration. Tests cover these boundaries and browser checks exercise the approval gate in the real UI.
