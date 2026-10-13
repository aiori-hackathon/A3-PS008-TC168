# Connect the PS-008 dashboard to GNS3

Implemented: dashboard at `/gns3`, controller/device evidence capture, strict IOS route normalization, modeled preview, separate human review, allowlisted IOS static-route writes, read-back verification, configured probes for additions, stale-state checks, route ownership, durable device-operation journal, and conservative recovery. The software simulator remains at `/`. Local GNS3 controller discovery and a VPCS link have now been verified live; the IOS device-write path has not. OpenAI calls are deferred.

## Quick live GNS3 smoke test

On the development laptop, GNS3 2.2.61 runs with controller authentication enabled and the local VPCS executable installed. The VPCS executable path was missing from GNS3 settings and has now been configured. From the repository root, run:

```powershell
python tools/setup_gns3_smoke_lab.py
```

This creates or reuses the isolated `AIORI-PS008-Connectivity-Smoke` project, starts two VPCS endpoints, connects them, assigns `10.99.0.10/24` and `10.99.0.11/24`, and records ping results at `.ps008/gns3-smoke-evidence.json`. The observed run returned five ping replies. It proves the local controller, VPCS executable, link, and packet path work; it does not exercise IOS observation or approved route deployment.

An OpenWrt 25.12 x86-64 image was also booted in a separate probe project using local QEMU software emulation. This is useful for checking GNS3/QEMU startup, but the PS-008 device adapter currently accepts Cisco IOS output and IOS commands only. The OpenWrt static route console experiment did not produce a successful router-to-router endpoint ping, so it is not agent deployment evidence. Local Windows QEMU software emulation is slow and uses significant memory; the configured GNS3 VM profile was unavailable on this machine.

## Try the dashboard now, without a router image

```powershell
cd C:\Users\parth\Documents\ChatGPT\AIORI-PS008
.\.venv\Scripts\python.exe -m ps008 --web
```

Open `http://127.0.0.1:8000/gns3`. Click **Offline rehearsal**, then **Compare forwarding**. The example addition is R1 -> `10.20.0.0/24` via `10.0.1.2`. Inspect before/after paths from all three modeled sources. Set the next hop to `192.0.2.20` to see an invalid proposal blocked. Capture again to compare normalized state and export evidence JSON. Rehearsal data is synthetic, never live GNS3 evidence.

## 1. Prepare the GNS3 application

Open GNS3 and create a new local project called `AIORI-PS008-Lab`. Confirm its local server is running under Preferences > Server (labels vary by installed version). A GUI installation alone does not create routers. If the local server is not listening, the adapter cannot connect. Do not run a second competing server on the same port.

Import a router appliance and a compatible image you have permission to use. An appliance template is not the OS image. The current reader targets Cisco IOS output; it cannot assume that FRR, VyOS, or a different OS produces compatible output. On Windows, a GNS3 VM may be required for your appliance. Check the appliance requirements first.

Begin with one router named R1 and confirm that it boots. Then duplicate/configure R2 and R3 for the full demo. The development machine has approximately 16 GB RAM and an Intel i5-12450H; actual memory allocation depends on the image and other running software.

## 2. Build the isolated data-plane topology

Connect R1 to R2 and R2 to R3. Map your image's actual interface names to these addresses; interface names are not universal:

- R1 towards R2: `10.0.1.1/24`; R2 towards R1: `10.0.1.2/24`.
- R2 towards R3: `10.0.2.1/24`; R3 towards R2: `10.0.2.2/24`.
- R1 LAN gateway: `10.10.0.1/24`; R2 LAN gateway: `10.30.0.1/24`; R3 LAN gateway: `10.20.0.1/24`.
- R2 bootstrap routes: `10.10.0.0/24` via `10.0.1.1`, and `10.20.0.0/24` via `10.0.2.2`.
- R3 bootstrap routes: `10.10.0.0/24` and `10.30.0.0/24` via `10.0.2.1`.
- Leave the R1 engineering static route absent initially to demonstrate the missing path.

Configure and enable those interfaces in the GNS3 router console. Add a VPCS/test host on each LAN for actual ping demonstrations, with matching subnet masks and gateway addresses. A routed packet needs a return path: a one-way predicted path is not enough to prove ping success.

## 3. Give the Python application a management path

Provide each router a separate reachable management IP through a suitable host-only/management network and GNS3 Cloud/VM networking. Keep it independent of the routes under test. Do not assume the IPs shown on the topology are directly reachable from Windows. Test Windows-to-router reachability and SSH first.

On each router, configure its hostname, a local SSH user, domain name, SSH keys and VTY SSH login as supported by the selected image. Credentials should be created locally, not committed. Verify each host key and connect once using Windows OpenSSH so the verified key is in your user's `known_hosts` file. The application rejects unknown or changed host keys.

## 4. Find project and node IDs

For a local v2 controller at its default address, these PowerShell commands list identifiers without modifying GNS3. If controller authentication is enabled, supply credentials using the method configured for your local GNS3 installation.

```powershell
Invoke-RestMethod http://127.0.0.1:3080/v2/version
Invoke-RestMethod http://127.0.0.1:3080/v2/projects | Select-Object name,project_id
# Substitute the actual project UUID below:
Invoke-RestMethod http://127.0.0.1:3080/v2/projects/PROJECT_UUID/nodes | Select-Object name,node_id,status
```

The adapter supports bearer tokens over HTTPS, and Basic authentication over HTTPS or HTTP on loopback only. For Basic auth, inventory may reference `controller_username_env` and `controller_password_env`; values must come from process environment variables and must never be placed in JSON. Remote controllers require HTTPS. The laptop's GNS3 controller uses Basic authentication on loopback.

## 5. Configure inventory and credentials

Copy the example inventory into `.ps008/gns3-inventory.json` and replace every sample value. Add entries for R2 and R3 using their own node IDs, management IPs, `PS008_R2_USERNAME`/`PS008_R2_PASSWORD` and `PS008_R3_USERNAME`/`PS008_R3_PASSWORD` references. These are names of environment variables, not passwords. For local authenticated GNS3, add `controller_username_env` and `controller_password_env` references too.

Add all three routers, then add an explicit test endpoint probe under `probes`. For example:

```json
"probes": [
  { "source": "R1", "destination": "10.20.0.10" }
]
```

The destination must be a provisioned host inside the route being added, with a working return route. The sample inventory is schema-only; replace all IDs, management addresses, credentials variable names, and probe values. One-router inventory still supports observation, but live reviews require R1, R2, and R3.

Set credentials without echoing the password in the PowerShell session used to start the application:

```powershell
$env:PS008_R1_USERNAME = Read-Host 'R1 SSH username'
$routerCredential = Get-Credential -UserName $env:PS008_R1_USERNAME -Message 'R1 SSH credential'
$env:PS008_R1_PASSWORD = $routerCredential.GetNetworkCredential().Password
```

Repeat for the other routers. The app currently reads process environment variables; it does not automatically load `.env` files. Never paste secrets in chat or commit them.

## 6. Launch and connect

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gns3]"
.\.venv\Scripts\python.exe -m ps008 --web --gns3-inventory .ps008/gns3-inventory.json
```

Open `http://127.0.0.1:8000/gns3`, click **Check GNS3 controller**, then **Capture router evidence**. Choose the action and inspect modeled forwarding impact. Only complete device-observed evidence can create an approval review. Inspect the target, timestamp, and exact command, then approve or reject. Approval re-observes all three routers and blocks if evidence or inventory changed. An add is complete only if route read-back and its configured 100% ping probe both pass. Removal is allowed only for the exact route this app previously added and verified, and succeeds only after read-back confirms absence. The application never silently substitutes rehearsal data after live failures.

## Scope of captured-state previews

The parser currently supports explicit CIDR connected/local routes and simple IPv4 static next-hop routes with administrative distance 1. Dynamic routes, ECMP, VRFs, interface-only routes, abbreviated route formats and unrecognized output block preview/deployment. Connected/local route interfaces must be up. This conservative subset still requires testing against the specific IOS-compatible image before a real demo.

Previews use the latest captured snapshot, expire after 15 minutes and are blocked after a failed observation or inventory change. They check forwarding intervals across the requested prefix from every observed source, report loops and loss of modeled reachability. The modeled prediction is not a packet test. Drift compares normalized routes and interface state from consecutive comparable captures, excluding timestamps and presentation order. It is not continuous monitoring or a full configuration audit. Local operation serialization cannot prevent an external administrator from changing a router between final read and command execution.

Evidence exports contain lab addresses and captured command output. Review them before sharing. Their SHA-256 checksums detect content changes, but are not signed attestations or tamper-proof logs.

## CLI-only observation reference

Install GNS3 using its [Windows instructions](https://docs.gns3.com/docs/getting-started/installation/windows). Obtain a router image you are permitted to use and configure one IOS router named R1 first. Enable SSH and provide a management address reachable from the application. Verify its SSH host key independently and add it to your user's known_hosts file before observation. Do not disable host-key verification to bypass errors.

Copy `examples/gns3-inventory.example.json` into an ignored `.ps008/` file. Replace the sample UUIDs and documentation-only IP address with your project ID, node ID and actual management IP. Select the API version matching your server. The example is a schema demonstration, not a runnable lab. Authenticated controllers require HTTPS; `token_env` optionally references a bearer-token environment variable. Remote controllers also require HTTPS. No credentials belong in inventory JSON.

Install optional dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gns3]"
.\.venv\Scripts\python.exe -m ps008 --gns3-inventory .ps008/gns3-inventory.json
```

For device observation, securely configure the username/password environment variables referenced in inventory, then run:

```powershell
.\.venv\Scripts\python.exe -m ps008 --gns3-inventory .ps008/gns3-inventory.json --observe-ios
```

The command performs controller GET requests and, when requested, three device show commands. Netmiko may issue terminal-session preparation commands; it does not enter configuration mode here. It never creates or starts nodes. Connection errors return a nonzero exit status rather than synthetic success.

Successful discovery means controller/node identity was observed. SSH evidence is collected using the system's verified host keys and normalized only for a deliberately narrow IOS output subset. SHA-256 fingerprints detect relevant observed changes but are not cryptographic device attestations. The operation journal stores the reviewed candidate, pre-change evidence, command outcome, read-back/probe result, and recovery outcome; it is local JSON and not tamper-proof.

For the local controller-only dashboard demonstration prepared on the development laptop, install `.[gns3]`, then run `python tools/run_gns3_dashboard.py`. The launcher reads the local GNS3 username/password from `gns3_server.ini` into the app process environment without printing them; it expects `.ps008/gns3-openwrt-inventory.json`. Use **Check GNS3 controller** or **Capture router evidence** in controller mode to verify node discovery. Device mode does not work against the OpenWrt node because the current adapter is IOS-only.

## Verification and remaining gates

Offline tests use HTTP/SSH/device fakes and cover identity, strict parser behavior, approval/rejection, no write before approval, add/remove, stale state, timeout before/after a possible write, read-back, failed probes, scoped recovery, and replay. The current full suite passes 127 tests. Live evidence from this laptop confirms GNS3 2.2.61 controller discovery, two running OpenWrt nodes during the probe, and five VPCS ping replies in the separate smoke project. No successful end-to-end routed ping or approved agent route write has been observed.

Remaining gates: build a three-router IOS-compatible lab, verify SSH host keys and exact parser output, prove route read-back and configured endpoint probes for approved add and owned-route removal, then record device-observed evidence. OpenWrt is not supported by the current device adapter. The OpenAI proposal interface comes later, with schema validation and its own security/evaluation plan.
