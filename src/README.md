# PS-008 Application Source

This directory contains the runnable application, tests, examples, and technical documentation. The submission overview and template-aligned project explanation are in the repository-root [README](../README.md).

## Quick start (Windows PowerShell)

```powershell
cd src
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ps008 --web
```

Open `http://127.0.0.1:8000`, choose **Judge demo**, and review the example route before approving it. The software twin is the reproducible demonstration; it does not emulate IOS.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gns3]"
.\.venv\Scripts\python.exe -m pytest -q
```

## GNS3 status

The optional adapter supports a narrow IOS-compatible IPv4 static-route workflow. Controller discovery and a VPCS connectivity smoke test were observed locally, but no successful agent-driven IOS route change has been verified. Follow [GNS3 setup and limitations](docs/GNS3_SETUP.md) before attempting a live lab. Never use this prototype against production routers.

## Technical guides

- [Architecture and implementation](docs/IMPLEMENTATION.md)
- [GNS3 architecture and evidence status](docs/GNS3_ARCHITECTURE.md)
- [GNS3 setup](docs/GNS3_SETUP.md)
- [Judge demo](docs/DEMO.md)
- [Security boundaries](docs/SECURITY.md)
- [API reference](docs/API.md)
- [Test plan](docs/TEST_PLAN.md)
