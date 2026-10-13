# Test plan and evidence

The original 43 tests remain intact. Expanded tests cover forwarding, persistent reviews, concurrency, API security, and demo isolation. Actual totals/timings are stored in `output/test-results.xml`; the report derives its count from that evidence.

## Required scenarios

1. Valid addition generates the canonical command and reaches engineering.
2. Valid removal succeeds and warns when reachability is lost.
3. Missing fields are rejected.
4. Unsupported intents, routers, protocols, and extra commands are rejected.
5. Invalid destination/next-hop values are rejected.
6. Self-hops, external hops, and wrong-link hops are rejected.
7. Conflicting routes are blocked; unchanged routes produce warnings.
8. Missing managed-route removal is rejected.
9. Longest-prefix forwarding selects the correct route.
10. Prefix-boundary checks detect hidden more-specific loops.
11. Unmodeled and partially reachable additions are rejected.
12. Human rejection leaves routing state unchanged.
13. Unsafe/altered candidates cannot deploy.
14. Approval changes only the target managed route table.
15. Apply/verification failures restore routes, revision, and applied IDs.
16. Pending journals recover interrupted reviews and state.
17. Reviews survive restart; version 1 state upgrades compatibly.
18. Expired, rejected, consumed, or stale reviews cannot deploy.
19. Concurrent decisions commit at most one change per reviewed revision.
20. CSRF tokens are session-bound; incorrect/missing origin or token is rejected.
21. Demo presets require approval and cannot reset persistent state.
22. Oversized input is rejected; packaged assets/security headers are present.

## Run checks

```powershell
python -m pytest -q --junitxml=output/test-results.xml
python -m playwright install chromium
python tools/check_browser.py --screenshots
```

Use the repository virtual environment. Browser checks launch a temporary server/lab and cover preview, approval, rejection, conflict, loop, rollback, verification failure, removal, workspace isolation, reload, keyboard skip, and mobile approval. They assert zero console errors and no horizontal overflow at 390 pixels. Screenshots are genuine captures.

Manual visual inspection complements assertions: command readability, warnings/errors, visible focus, route tables, current state, and audit. Browser verification is a separate command so routine tests do not require a downloaded browser.

Layout regression checks cover an empty review at five viewport sizes, including 1820x900 and 390x844. They inspect card overlap at the start, middle, and end of page scrolling, verify that the routing panel can be scrolled with the keyboard to its last row, and check for horizontal page overflow. The intent card stays in normal flow; routing tables have their own bounded, focusable scroll area.

## Acceptance

All core/API tests and browser flows pass; clean installation and packaged wheel assets are verified; both intents work through both interfaces; invalid/rejected reviews cannot deploy; full lab snapshots match after failures. Claims identify the simulation and provisional intents. Evidence/screenshots/PDF are stored in Git; runtime state and temporary labs are excluded.
