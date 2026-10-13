"""Crash-recoverable local state for the simulated lab."""

import ipaddress
import json
import os
import tempfile
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path

from .lab import DigitalTwin, Route

STATE_VERSION = 2


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def record_event(twin, action, review_id=None, **details):
    twin.audit.append({"sequence": len(twin.audit) + 1, "at": timestamp(),
                       "action": action, "review_id": review_id, "revision": twin.revision, **details})


class StateStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.journal = self.path.with_name(self.path.name + ".journal")
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temp_path = Path(stream.name)
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _payload(twin: DigitalTwin) -> dict:
        return {
            "version": STATE_VERSION, "revision": twin.revision,
            "routes": twin.state(), "applied_request_ids": sorted(twin.applied_request_ids),
            "modeled": twin.modeled, "reviews": twin.reviews, "audit": twin.audit,
        }

    @staticmethod
    def _decode(payload: dict) -> DigitalTwin:
        if not isinstance(payload, dict) or payload.get("version") not in (1, STATE_VERSION):
            raise ValueError("Unsupported or corrupt lab state")
        revision = payload.get("revision")
        raw = payload.get("routes")
        applied = payload.get("applied_request_ids", [])
        if type(revision) is not int or revision < 0 or not isinstance(raw, dict):
            raise ValueError("Invalid lab state revision or routes")
        if not isinstance(applied, list) or any(not isinstance(value, str) or not value for value in applied):
            raise ValueError("Invalid applied request identifiers")
        if len(set(applied)) != len(applied):
            raise ValueError("Duplicate applied request identifier")
        if set(raw) != {"R1", "R2", "R3"}:
            raise ValueError("Lab state must contain exactly R1, R2, and R3")
        routes: dict[str, list[Route]] = {}
        for router, items in raw.items():
            if not isinstance(items, list):
                raise ValueError("Router routes must be a list")
            routes[router] = []
            seen: set[str] = set()
            for item in items:
                if not isinstance(item, dict) or set(item) != {"network", "next_hop", "owner"}:
                    raise ValueError("Malformed route in lab state")
                try:
                    network = str(ipaddress.IPv4Network(item["network"], strict=True))
                    next_hop = str(ipaddress.IPv4Address(item["next_hop"]))
                except (ValueError, TypeError) as exc:
                    raise ValueError("Invalid route address in lab state") from exc
                if item["owner"] != router or network in seen:
                    raise ValueError("Duplicate or improperly owned route in lab state")
                seen.add(network)
                routes[router].append(Route(network, next_hop, router))
        reviews, audit = payload.get("reviews", {}), payload.get("audit", [])
        if not isinstance(reviews, dict) or not isinstance(audit, list):
            raise ValueError("Invalid review or audit state")
        modeled = payload.get("modeled", True)
        if type(modeled) is not bool:
            raise ValueError("Invalid topology mode")
        return DigitalTwin(routes=routes, revision=revision, applied_request_ids=set(applied),
                           modeled=modeled, reviews=reviews, audit=audit)

    def load(self) -> DigitalTwin:
        with self.locked():
            return self._load_unlocked()

    def _load_unlocked(self, recovery=True) -> DigitalTwin:
        if self.journal.exists():
            journal = json.loads(self.journal.read_text(encoding="utf-8"))
            if not isinstance(journal, dict) or journal.get("status") != "pending":
                raise ValueError("Invalid rollback journal")
            before = self._decode(journal.get("before"))
            review_id = journal.get("review_id")
            if recovery and review_id in before.reviews:
                before.reviews[review_id]["status"] = "rolled_back"
                before.reviews[review_id]["deployment"] = {
                    "success": False, "rolled_back": True,
                    "message": "Interrupted deployment recovered from rollback journal",
                }
            if recovery:
                record_event(before, "recovered", review_id, message="Restored state after interrupted deployment")
            self._write(self.path, self._payload(before))
            self.journal.unlink()
            return before
        if not self.path.exists():
            return DigitalTwin.baseline()
        return self._decode(json.loads(self.path.read_text(encoding="utf-8")))

    def begin(self, twin: DigitalTwin, review_id: str | None = None) -> None:
        if self.journal.exists():
            raise RuntimeError("Pending rollback journal must be recovered before deployment")
        current = self._decode(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else DigitalTwin.baseline()
        if (
            current.revision != twin.revision or current.state() != twin.state()
            or current.applied_request_ids != twin.applied_request_ids
        ):
            raise RuntimeError("Simulated lab state changed since it was loaded")
        self._write(self.journal, {"status": "pending", "before": self._payload(twin), "review_id": review_id})

    def save(self, twin: DigitalTwin):
        """Caller holds the state lock; persist review/audit changes atomically."""
        self._write(self.path, self._payload(twin))

    def commit(self, twin: DigitalTwin) -> None:
        if not self.journal.exists():
            raise RuntimeError("No pending deployment transaction")
        self._write(self.path, self._payload(twin))
        self.journal.unlink()

    def rollback(self) -> DigitalTwin:
        if not self.journal.exists():
            raise RuntimeError("No pending deployment transaction")
        return self._load_unlocked(recovery=False)
