# PS-008 Network Management Agent

[Project understanding and Proposed solution](https://youtu.be/tZcXjvHfEpg?si=Nb0RlGkdUhJWbuEH) · [Prototype demonstration link](https://drive.google.com/drive/folders/1gVgXb2LjOvwtVQpNY_jxOXl32ayPishS?usp=drive_link)

A local dashboard and CLI for reviewed IPv4 static-route changes. The software-twin demo remains available, and `/gns3` provides a separate, approval-gated GNS3/IOS adapter with device read-back, endpoint probing, drift checks, an operation journal, and scoped recovery.

![Dashboard with a reviewed route](docs/images/dashboard.png)

## Start the application

Python 3.10+, Git, and a browser are required. Run from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,report]"
.\.venv\Scripts\python.exe -m ps008 --web
```

Open http://127.0.0.1:8000. The application binds to your computer's loopback interface. No API key or external network service is required. On macOS/Linux, use `.venv/bin/python` instead of the Windows executable.

Select **Judge demo** and choose **Add route**. Inspect the generated command and before/after forwarding paths, then select **Approve & deploy**. Try rejection, conflicts, loops, removal, deployment failure, and verification failure. Each preset resets only your session's demo lab; the persistent lab is separate.

## What works

- Two provisional intents: `add_static_route` and `remove_static_route`.
- Explicit R1-R2-R3 links, connected LANs, fixed bootstrap routes, and operator-managed routes.
- Normalized IPv4 requests and strict Cisco IOS command allowlisting.
- Preview on a cloned twin, prefix-boundary reachability checks, longest-prefix forwarding, conflict and loop detection.
- Server-issued reviews bound to a lab revision, with a 15-minute expiry and single-use approval/rejection.
- Deployment-boundary validation and post-apply verification.
- Atomic persistence, snapshots, rollback, interrupted-deployment recovery, and an exportable audit trail.
- Accessible local dashboard, responsive layout, and shared CLI/service logic.
- Typed review lifecycle shared by simulator and GNS3 review adapters.
- GNS3 dashboard for controller/device evidence, modeled preview, explicit approve/reject, and evidence export.
- Allowlisted IOS static-route add/remove commands over verified-host-key SSH; review expiry and device-state revalidation.
- Persistent device-operation journal, route ownership tracking, read-back verification, configured IPv4 probes, and conservative recovery states.

The software-twin workflow is deterministic and does not emulate IOS. The GNS3 path can issue writes only after its separate review is approved and fresh device evidence still matches. Both remain limited to explicitly supported IPv4 static routes. No LLM is connected; the OpenAI interface is deferred until the live path has real-lab validation and secure key setup.

## Is the agent working correctly?

The simulator path has transactional twin-state checks. The current full suite passes 127 tests. In the isolated GNS3 2.2.61 lab, three OpenWrt router processes ran as R1–R2–R3. A fresh R1-to-R3 console probe received 4/4 replies through R2, and packet captures on both links recorded Echo requests and replies. The `/gns3` dashboard also read the router interfaces, routes, and 4/4 ping result from their live consoles.

These live checks validate router observation and packet connectivity. The preparation helper configures this isolated OpenWrt lab over its local consoles; the approval-gated write adapter is designed for Cisco IOS-compatible routers and has been verified with offline device fakes. Its live IOS deployment remains a future validation step. Dynamic routes, IPv6, ECMP, arbitrary commands, and unowned-route removal are outside the current scope. The local JSON journal is not a signed compliance log.

## GNS3 lab and optional LLM

### Local OpenWrt recording lab

The `/gns3` page includes a **Live GNS3 / OpenWrt lab** panel for the isolated `AIORI-PS008-Three-Router-Lab` project created by `python tools/setup_three_router_gns3_lab.py`. It reads the started R1/R2/R3 QEMU consoles, displays their IPv4 interfaces and routes, and can run a four-packet R1-to-R3 ping. Captures retain the latest 20 results in the ignored `.ps008/openwrt-lab-captures.json` file and can be exported as JSON. The panel is read-only apart from the explicitly requested ping; it does not deploy route changes. Set `PS008_OPENWRT_PROJECT_ID` before starting the web app if your GNS3 project has a different UUID. A ping result is evidence of that run only; a green GNS3 link does not prove routed connectivity.

For a repeatable local demonstration, start GNS3, then run the following commands from the project root. The preparation script waits for the OpenWrt consoles, restores the link addresses and routes after a restart, enables R2 forwarding inside this isolated lab, and saves the observed ping result to `.ps008/openwrt-preparation-evidence.json`. It fails if all four replies do not return.

```powershell
.\.venv\Scripts\python.exe tools/setup_three_router_gns3_lab.py
.\.venv\Scripts\python.exe tools/prepare_openwrt_demo.py
.\.venv\Scripts\python.exe -m ps008 --web
```

Open `http://127.0.0.1:8000/gns3`, use **Refresh live routers** and **Send R1 → R3 ping**, and optionally switch on **Auto-refresh** for a new console snapshot every minute. Start captures on both GNS3 links to inspect the same traffic in Wireshark. The setup uses the OpenWrt image already installed on this development machine; another computer needs that image and its own GNS3 project ID.

To record a fresh packet proof on both links after preparation, run `.\.venv\Scripts\python.exe tools/record_gns3_packet_demo.py`. It starts GNS3 link captures, requests the console ping, stops capture in a cleanup step, and writes the observed result and capture paths to `.ps008/openwrt-packet-demo.json`.

The GNS3 review flow is at `/gns3`. Its live write path requires an isolated three-router IOS-compatible lab, independent SSH management addresses, verified host keys, environment-based credentials, and a source-defined endpoint probe. The example inventory is a template, not a ready-to-run lab. Follow the [setup and limitations](docs/GNS3_SETUP.md); never connect this prototype to production routers.

For this machine's authenticated local GNS3 controller dashboard demo, install the adapter dependencies and start the prepared inventory using:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gns3]"
python tools/run_gns3_dashboard.py
```

Open http://127.0.0.1:8000/gns3 and use **Check GNS3 controller** to see the local node. This controller-only inventory is specific to the development laptop. For a separate GNS3 connectivity smoke test, run `python tools/setup_gns3_smoke_lab.py`; it records live VPCS ping evidence under `.ps008/`. Neither the smoke lab nor the OpenWrt probe proves the IOS route deployment flow. For a real deployment, prepare `.ps008/gns3-inventory.json` using the full setup guide and verify route read-back plus a configured endpoint probe. OpenAI natural-language interpretation remains deferred; no API key is needed or read by this version.

## Topology

![Three-router topology](docs/images/topology.svg)

R1 hosts `10.10.0.0/24`, R2 hosts `10.30.0.0/24`, and R3 hosts `10.20.0.0/24`. Links are `10.0.1.0/24` and `10.0.2.0/24`. Fixed bootstrap routes let R2 reach the outer LANs and R3 reach R1/R2. R1 initially has no managed route to engineering; adding `10.20.0.0/24` via `10.0.1.2` changes its path from a missing route to R1 -> R2 -> R3.

## CLI

```powershell
.\.venv\Scripts\python.exe -m ps008 --demo
.\.venv\Scripts\python.exe -m ps008 examples/add_route.json
.\.venv\Scripts\python.exe -m ps008 examples/remove_route.json
```

Interactive CLI deployment requires `yes` or `y`. Default state: `.ps008/state.json`; `--state-file PATH` selects another file. CLI and web requests share the same persisted lab and review rules. Version 1 files load compatibly and upgrade on the next write.

## Verify and reproduce evidence

### Evidence and reproducibility

- **Automated tests:** [JUnit XML report](output/test-results.xml) from the latest local run: 127 passed, 0 failed (1 dependency deprecation warning). Rerun with the Pytest command below.
- **Three-router topology:** [GNS3 screenshot](docs/evidence/gns3-three-router-topology.png) and the repeatable [lab setup helper](tools/setup_three_router_gns3_lab.py). The screenshot shows the started R1–R2–R3 OpenWrt nodes and links.
- **Live router state and ping:** [timestamped 4/4 OpenWrt console result](docs/evidence/openwrt-routed-ping-success.json) records interfaces and routes for all three routers and R1's four replies from R3. The [dashboard capture history](docs/evidence/openwrt-lab-captures.json) includes earlier failed diagnostics and later live snapshots; each result is labeled with its own time and reply count.
- **Packet captures:** open the successful run from [R1–R2](docs/evidence/R1-R2-routed-success.pcap) and [R2–R3](docs/evidence/R2-R3-routed-success.pcap) in Wireshark and filter `icmp`. Each link recorded four Echo requests and four Echo replies during the four-packet run. A separate [VPCS smoke-lab report](docs/evidence/gns3-smoke-evidence.json) records five replies across a direct link.
- **Review workflow:** [dashboard screenshot](docs/images/dashboard.png), [review screenshot](docs/images/review.png), and [judge walkthrough](docs/DEMO.md) show the software-twin proposal, modeled impact, approval/rejection, and result. The GNS3 IOS adapter is exercised by offline tests; a live IOS route deployment is a future validation step.

The software-twin screenshots, mocked adapter tests, GNS3 controller observations, and OpenWrt packet captures represent distinct evidence sources. The linked files let reviewers inspect each result and reproduce the supported tests.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --junitxml=output/test-results.xml
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tools/check_browser.py --screenshots
.\.venv\Scripts\python.exe tools/check_gns3_browser.py
.\.venv\Scripts\python.exe tools/build_report.py
```

If Windows denies access to Pytest's shared temporary folder, use a fresh directory: `--basetemp .pytest_cache/run-<unique-name>`. Pytest removes that directory before use, so choose a disposable test directory.

`check_browser.py` exercises the simulator's UI workflows. `check_gns3_browser.py` exercises the GNS3 dashboard and approval controls with mocked device responses; it does not contact GNS3 or prove router compatibility. The report builder reads simulator test evidence and refuses failed results or missing evidence.

## Documentation

- [Implementation and architecture](docs/IMPLEMENTATION.md)
- [HTTP API](docs/API.md)
- [Security boundaries](docs/SECURITY.md)
- [Judges' demonstration](docs/DEMO.md)
- [Theory and judge preparation](docs/JUDGE_THEORY.md)
- [Tests and evidence](docs/TEST_PLAN.md)
- [Technical report source](docs/technical-report.md)
- [Earlier technical report PDF](output/pdf/ps008-technical-report.pdf) (simulator milestone; not regenerated for GNS3)
- [Submission checklist](docs/SUBMISSION.md)

The UI and report were implemented as one integrated project. Both teammates should review the complete flow before the mentor demonstration.
