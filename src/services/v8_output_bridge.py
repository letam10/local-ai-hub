"""Wave 2 compatibility bridge from V7 production callsites to V8 Output Authority."""

from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
import threading
from typing import Any, Iterable

from src.platform.paths import HubPaths, get_paths
from src.platform.storage_authority import StorageAuthorityError
from src.services.output_authority import OutputAuthorityError
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.transaction_store import TransactionStoreError
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


_JOB_ID = re.compile(r"^(?:jobv5_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._() -]+")
_LOCK = threading.RLock()
_DEFAULT: "V8OutputBridge | None" = None


class V8OutputBridge:
    """Use one reservation-bound V8 publisher while retaining legacy read compatibility."""

    def __init__(self, authority: ProductionOutputAuthority) -> None:
        self.authority = authority
        self.store = authority.store

    @classmethod
    def for_paths(cls, paths: HubPaths | None = None) -> "V8OutputBridge":
        active = paths or get_paths()
        store = V8ProductionTransactionStore.for_paths(active)
        return cls(ProductionOutputAuthority(paths=active, store=store))

    @staticmethod
    def _job(job_id: object) -> str:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise OutputAuthorityError("invalid_job_id")
        return job_id

    def reserve(self, job_id: str) -> str | None:
        job_id = self._job(job_id)
        current = self.store.reservation_for_job(job_id, states=("reserved",))
        if current is not None:
            return str(current["reservation_id"])
        if self.store.reservation_for_job(job_id, states=("authorized",)) is not None:
            return None
        try:
            return self.authority.begin_reservation(job_id)
        except (OutputAuthorityError, TransactionStoreError, StorageAuthorityError):
            return None

    def open_reservation(self, job_id: str) -> str | None:
        job_id = self._job(job_id)
        current = self.store.reservation_for_job(job_id, states=("reserved",))
        return str(current["reservation_id"]) if current is not None else None

    def publish_candidates(
        self,
        *,
        job_id: str,
        candidates: Iterable[Path | str],
        provenance: dict[str, Any],
        reservation_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        job_id = self._job(job_id)
        reservation = reservation_id or self.open_reservation(job_id)
        if reservation is None:
            return None
        return self.authority.publish_owned_candidates(
            reservation_id=reservation,
            job_id=job_id,
            candidates=candidates,
            provenance=provenance,
        )

    def publish_bytes(
        self,
        *,
        job_id: str,
        content: bytes,
        name: str,
        provenance: dict[str, Any],
        reservation_id: str | None = None,
    ) -> dict[str, Any] | None:
        job_id = self._job(job_id)
        if not isinstance(content, bytes):
            return None
        safe_name = _SAFE_NAME.sub("_", Path(name).name.replace("\x00", ""))[:180] or "result.bin"
        reservation = reservation_id or self.open_reservation(job_id) or self.reserve(job_id)
        if reservation is None:
            return None
        lease = self.authority.storage.lease("output", create=True)
        relative = Path(f"hub-v8-producer-{job_id[-8:]}-{secrets.token_hex(12)}-{safe_name}")
        producer = self.authority.storage.resolve_relative(lease, relative, require_exists=False)
        identity = None
        try:
            with producer.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            identity = self.authority.storage.file_identity(lease, relative)
            published = self.publish_candidates(
                job_id=job_id,
                candidates=[producer],
                provenance=provenance,
                reservation_id=reservation,
            )
            return published[0] if isinstance(published, list) and len(published) == 1 else None
        except (OSError, OutputAuthorityError, StorageAuthorityError, TransactionStoreError):
            return None
        finally:
            if identity is not None:
                self.authority.storage.unlink_if_identity(lease, relative, identity)

    def abort_job(self, job_id: str) -> bool:
        job_id = self._job(job_id)
        rows = [row for row in self.store.unfinished_reservations() if row.get("job_id") == job_id]
        changed = False
        for row in rows:
            try:
                changed = self.authority.abort_reservation(str(row["reservation_id"]), job_id) or changed
            except Exception:
                continue
        return changed

    def close_no_output(self, job_id: str) -> bool:
        job_id = self._job(job_id)
        reservation = self.open_reservation(job_id)
        return bool(reservation and self.authority.close_no_output(reservation, job_id))

    def reconcile(self, *, active_job_ids: set[str] | None = None) -> dict[str, int]:
        active = active_job_ids or set()
        result = self.authority.reconcile_incomplete()
        aborted_reservations = 0
        for row in self.store.unfinished_reservations():
            job_id = str(row.get("job_id") or "")
            if job_id in active:
                continue
            try:
                if self.authority.abort_reservation(str(row["reservation_id"]), job_id):
                    aborted_reservations += 1
            except Exception:
                continue
        return {**result, "aborted_reservations": aborted_reservations}

    def resolve(self, artifact_id: str) -> Path | None:
        return self.authority.resolve(artifact_id)

    def describe(self, artifact_id: str) -> dict[str, Any] | None:
        return self.authority.describe(artifact_id)

    def list_public(self, *, limit: int = 240) -> list[dict[str, Any]]:
        return self.authority.list_public(limit=limit)


def default_bridge(*, create: bool = False) -> V8OutputBridge | None:
    """Return the process singleton; read-only callers do not create a missing DB."""

    global _DEFAULT
    with _LOCK:
        if _DEFAULT is not None:
            return _DEFAULT
        paths = get_paths()
        database = paths.config_root / "v8_control.sqlite3"
        if not create and not database.is_file():
            return None
        try:
            _DEFAULT = V8OutputBridge.for_paths(paths)
        except Exception:
            return None
        return _DEFAULT


def set_default_bridge_for_tests(bridge: V8OutputBridge | None) -> None:
    global _DEFAULT
    with _LOCK:
        _DEFAULT = bridge


def reset_default_bridge_for_tests() -> None:
    set_default_bridge_for_tests(None)


__all__ = [
    "V8OutputBridge",
    "default_bridge",
    "reset_default_bridge_for_tests",
    "set_default_bridge_for_tests",
]
