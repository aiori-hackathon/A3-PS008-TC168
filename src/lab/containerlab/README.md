# Lightweight 3-Router Lab Testbed (Containerlab + FRRouting)

This testbed provides an instant, zero-license, reproducible 3-router lab using **Containerlab** and **FRRouting (FRR)**. It is identical in topology and IP addressing to the PS-008 Cisco IOS specification.

## Topology Architecture

```
[client-ops] 10.10.0.10
     │
   (eth2) 10.10.0.1/24
   [ R1 ]
   (eth1) 10.0.1.1/24
     │
     │ 10.0.1.0/24 transit
     │
   (eth1) 10.0.1.2/24
   [ R2 ] (Core 10.30.0.1/24)
   (eth2) 10.0.2.1/24
     │
     │ 10.0.2.0/24 transit
     │
   (eth1) 10.0.2.2/24
   [ R3 ]
   (eth2) 10.20.0.1/24
     │
[client-eng] 10.20.0.10
```

## Quick Start (Linux / WSL2 / Docker)

1. Install [Containerlab](https://containerlab.dev/install/):
   ```bash
   bash -c "$(curl -sL https://get.containerlab.dev)"
   ```

2. Deploy the 3-router testbed:
   ```bash
   sudo clab deploy -t topology.clab.yml
   ```

3. Test initial connectivity (proves missing route before agent):
   ```bash
   # Ping from Operations client to Engineering client (fails as expected)
   docker exec -it clab-aiori-ps008-lab-client-ops ping -c 2 10.20.0.10
   ```

4. Apply the agent's approved candidate route:
   ```bash
   docker exec -it clab-aiori-ps008-lab-r1 vtysh -c "conf t" -c "ip route 10.20.0.0/24 10.0.1.2"
   ```

5. Verify end-to-end connectivity:
   ```bash
   # Ping now succeeds!
   docker exec -it clab-aiori-ps008-lab-client-ops ping -c 3 10.20.0.10
   ```

6. Teardown:
   ```bash
   sudo clab destroy -t topology.clab.yml
   ```
