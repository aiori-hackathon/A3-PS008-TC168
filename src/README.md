# PS-008 Application Source

This directory contains the runnable application, tests, examples, and technical documentation. The submission overview is in the repository-root [README](../README.md), and the supplied hackathon structure template is preserved at the repository root.

## Quick start (Windows PowerShell)

```powershell
cd src
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ps008 --web
```

Open `http://127.0.0.1:8000`, choose **Judge demo**, inspect the proposed route and modeled forwarding paths, then approve or reject the review.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gns3]"
.\.venv\Scripts\python.exe -m pytest -q
```

## GNS3 workflow

The adapter implements a restricted IOS-compatible IPv4 static-route workflow, including controller/device evidence capture, approval review, state revalidation, route read-back, configured probes, and scoped recovery outcomes. Local controller discovery and VPCS connectivity have been demonstrated. IOS route deployment in a configured lab is a future validation milestone; see [GNS3 setup](docs/GNS3_SETUP.md) and the root README's [Future Scope](../README.md#future-scope).

## Technical guides

- [Architecture and implementation](docs/IMPLEMENTATION.md)
- [GNS3 architecture and evidence](docs/GNS3_ARCHITECTURE.md)
- [GNS3 setup](docs/GNS3_SETUP.md)
- [Judge demo](docs/DEMO.md)
- [Security boundaries](docs/SECURITY.md)
- [API reference](docs/API.md)
- [Test plan](docs/TEST_PLAN.md)
