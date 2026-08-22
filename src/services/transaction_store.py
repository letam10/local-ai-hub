"""SQLite-backed V8 transactional metadata store.

The database is an internal control-plane journal. It stores only opaque IDs,
bounded relative object keys and verification metadata; public callers never
receive a workstation path. Output publication is a single SQLite commit after
reservation/job/transaction binding has been proven.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import re
import secrets
import sqlite3
import stat
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from src.platform.paths import HubPaths, get_paths, is_reparse_point
from src.platform.storage_authority import StorageAuthority


SCHEMA_VERSION = "v8-transaction-store.v1"
_JOB_ID = re.compile(r"^(?:jobv5_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$")
_RESERVATION_ID = re.compile(r"^outres_[a-f0-9]{32}$")
_TRANSACTION_ID = re.compile(r"^outtx_[a-f0-9]{32}$")
_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")
_OBJECT_ID = re.compile(r"^obj_[a-f0-9]{32}$")
_OPERATION_ID = re.compile(r"^compop_[a-f0-9]{32}$")
_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_SAFE_PLAN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_SAFE_ACTION = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SQLITE_INT64_MIN = -(1 << 63)
_SQLITE_INT64_MAX = (1 << 63) - 1
_UINT64_MAX = (1 << 64) - 1
_MAX_BACKUP_BYTES = 128 * 1024 * 1024
_BACKUP_COPY_TIMEOUT_SECONDS = 5.0

_COMPONENT_TRANSITIONS = {
    "planned": frozenset({"executing", "blocked", "cancelled"}),
    "executing": frozenset({"verifying", "committed", "failed", "blocked", "cancelled"}),
    "verifying": frozenset({"committed", "failed", "blocked"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset(),
    "cancelled": frozenset(),
}

# A component-bundle journal intentionally uses the existing opaque component
# operation as its parent.  It records only component identities and state
# fingerprints that were already server-owned by the bundle plan; no source,
# destination, selection, or runtime handle is persisted here.
_BUNDLE_STEP_PHASES = frozenset(
    {
        "pending",
        "reused",
        "installing",
        "installed",
        "rollback_pending",
        "rolled_back",
        "manual_review",
    }
)


class TransactionStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _BackupCopyTimedOut(RuntimeError):
    """Private signal used to bound SQLite's busy-retry backup loop."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise TransactionStoreError("invalid_artifact_name")
    if any(char in value for char in ("\x00", "\r", "\n", "/", "\\")) or value in {".", ".."}:
        raise TransactionStoreError("invalid_artifact_name")
    return value


def _safe_object_key(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 320 or "\x00" in value or ":" in value or "\\" in value:
        raise TransactionStoreError("invalid_object_key")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise TransactionStoreError("invalid_object_key")
    return value


def encode_file_identity_for_sqlite(value: object) -> int:
    """Store Windows unsigned file identifiers without SQLite overflow.

    ``st_ino`` can represent an unsigned 64-bit NTFS file identifier, while
    SQLite INTEGER is signed 64-bit.  Persist the same bit pattern using its
    two's-complement signed representation; callers decode it before identity
    comparison, so no high-bit truncation or collision is introduced.
    """

    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _UINT64_MAX:
        raise TransactionStoreError("invalid_file_identity")
    return value if value <= _SQLITE_INT64_MAX else value - (1 << 64)


def decode_file_identity_from_sqlite(value: object) -> int:
    """Recover the original unsigned identity value from SQLite INTEGER."""

    if isinstance(value, bool) or not isinstance(value, int) or not _SQLITE_INT64_MIN <= value <= _SQLITE_INT64_MAX:
        raise TransactionStoreError("invalid_file_identity")
    return value if value >= 0 else value + (1 << 64)


class V8TransactionStore:
    """Authoritative metadata journal for V8 output and component operations."""

    def __init__(self, path: Path, *, initialize: bool = True) -> None:
        """Open the V8 control journal.

        ``initialize=False`` is deliberately read-only construction for
        server-owned backup/restore orchestration.  It validates an existing
        journal path without creating parents, opening a write connection, or
        applying SQLite pragmas to the live Config database merely to take a
        snapshot.
        """

        self.path = Path(path).absolute()
        self._lock = threading.RLock()
        if not initialize:
            self._regular_path(self.path, required=True, code="transaction_store_path_unsafe")
            return
        try:
            if self.path.exists() and (self.path.is_symlink() or is_reparse_point(self.path) or not self.path.is_file()):
                raise TransactionStoreError("transaction_store_path_unsafe")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TransactionStoreError("transaction_store_unavailable") from exc
        self._initialize()

    @classmethod
    def for_paths(cls, paths: HubPaths | None = None) -> "V8TransactionStore":
        active = paths or get_paths()
        authority = StorageAuthority(active)
        lease = authority.lease("config", create=True)
        database = authority.resolve_relative(lease, "v8_control.sqlite3", require_exists=False)
        store = cls(database)
        lease.assert_current()
        return store

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        except sqlite3.Error as exc:
            raise TransactionStoreError("transaction_store_unavailable") from exc
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
            finally:
                connection.close()

    def _initialize(self) -> None:
        with self._write() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS output_reservations(
                    reservation_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','authorized','published','aborted','no_output')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(reservation_id, job_id)
                );
                CREATE TABLE IF NOT EXISTS output_transactions(
                    transaction_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('preparing','authorized','committed','aborted')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(transaction_id, reservation_id, job_id),
                    FOREIGN KEY(reservation_id, job_id)
                        REFERENCES output_reservations(reservation_id, job_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS artifact_objects(
                    artifact_id TEXT PRIMARY KEY,
                    object_id TEXT NOT NULL UNIQUE,
                    transaction_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    object_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    file_device INTEGER NOT NULL,
                    file_inode INTEGER NOT NULL,
                    file_mtime_ns INTEGER NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('staged','published')),
                    job_spec_fingerprint TEXT NOT NULL,
                    adapter_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(transaction_id, reservation_id, job_id)
                        REFERENCES output_transactions(transaction_id, reservation_id, job_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS component_operations(
                    operation_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    component_id TEXT NOT NULL,
                    component_type TEXT NOT NULL CHECK(component_type IN ('model','runtime')),
                    action TEXT NOT NULL,
                    expected_state_fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('planned','executing','verifying','committed','failed','blocked','cancelled')),
                    result_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS component_bundle_steps(
                    operation_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL CHECK(step_index >= 0 AND step_index < 64),
                    component_id TEXT NOT NULL,
                    component_type TEXT NOT NULL CHECK(component_type IN ('model','runtime')),
                    state_fingerprint TEXT NOT NULL,
                    phase TEXT NOT NULL CHECK(phase IN ('pending','reused','installing','installed','rollback_pending','rolled_back','manual_review')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(operation_id, step_index),
                    FOREIGN KEY(operation_id) REFERENCES component_operations(operation_id) ON DELETE RESTRICT
                );
                CREATE INDEX IF NOT EXISTS idx_artifact_visibility ON artifact_objects(visibility, created_at);
                CREATE INDEX IF NOT EXISTS idx_component_operations ON component_operations(component_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_component_bundle_steps ON component_bundle_steps(operation_id, step_index);
                """
            )
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                db.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
            elif row["value"] != SCHEMA_VERSION:
                raise TransactionStoreError("transaction_store_schema_mismatch")

    @staticmethod
    def _regular_path(path: Path, *, required: bool, code: str) -> Path:
        """Validate one local SQLite leaf without resolving through reparses.

        Backup and restore are deliberately internal store primitives.  Callers
        receive only a finite success/failure result; this guard makes their
        supplied leaf and every existing parent ordinary before SQLite opens a
        handle.  It never creates a missing parent chain.
        """

        candidate = Path(path).absolute()
        current = candidate.parent
        while True:
            try:
                if not current.exists() or current.is_symlink() or is_reparse_point(current) or not current.is_dir():
                    raise TransactionStoreError(code)
            except OSError as exc:
                raise TransactionStoreError(code) from exc
            if current.parent == current:
                break
            current = current.parent
        try:
            exists = candidate.exists()
            if required and not exists:
                raise TransactionStoreError(code)
            if exists and (candidate.is_symlink() or is_reparse_point(candidate) or not candidate.is_file()):
                raise TransactionStoreError(code)
        except OSError as exc:
            raise TransactionStoreError(code) from exc
        return candidate

    @staticmethod
    def _readonly_connection(path: Path) -> sqlite3.Connection:
        try:
            return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise TransactionStoreError("transaction_backup_unavailable") from exc

    @classmethod
    def _validate_snapshot(cls, path: Path) -> int:
        candidate = cls._regular_path(path, required=True, code="transaction_backup_invalid")
        try:
            size = candidate.stat().st_size
        except OSError as exc:
            raise TransactionStoreError("transaction_backup_invalid") from exc
        if size < 1 or size > _MAX_BACKUP_BYTES:
            raise TransactionStoreError("transaction_backup_invalid")
        connection = cls._readonly_connection(candidate)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise TransactionStoreError("transaction_backup_invalid")
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None or row[0] != SCHEMA_VERSION:
                raise TransactionStoreError("transaction_store_schema_mismatch")
            tables = {
                str(item[0])
                for item in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            required_tables = {"meta", "output_reservations", "output_transactions", "artifact_objects", "component_operations"}
            if not required_tables.issubset(tables):
                raise TransactionStoreError("transaction_backup_invalid")
        except sqlite3.Error as exc:
            raise TransactionStoreError("transaction_backup_invalid") from exc
        finally:
            connection.close()
        return int(size)

    @classmethod
    def validate_snapshot(cls, path: Path) -> int:
        """Read-only validation for an existing V8 backup snapshot.

        Backup adapters use this public narrow seam instead of instantiating a
        normal store, which would initialize the supplied path as a live
        journal.  The validator rejects reparses, corrupt databases, and
        schema mismatches without creating or changing any file.
        """

        return cls._validate_snapshot(path)

    @staticmethod
    def _temporary_sibling(destination: Path, *, prefix: str) -> Path:
        for _ in range(8):
            candidate = destination.with_name(f".{destination.name}.{prefix}.{secrets.token_hex(12)}.tmp")
            try:
                with candidate.open("xb"):
                    pass
                return candidate
            except FileExistsError:
                continue
            except OSError as exc:
                raise TransactionStoreError("transaction_backup_unavailable") from exc
        raise TransactionStoreError("transaction_backup_unavailable")

    @staticmethod
    def _temporary_identity(path: Path) -> tuple[int, int, int] | None:
        """Return identity only for a task-created ordinary temporary leaf."""

        try:
            value = os.lstat(path)
        except (OSError, ValueError):
            return None
        if is_reparse_point(path) or not stat.S_ISREG(value.st_mode):
            return None
        return (int(getattr(value, "st_dev", 0)), int(getattr(value, "st_ino", 0)), int(value.st_mode))

    @classmethod
    def _remove_owned_temporary(cls, path: Path, expected: tuple[int, int, int] | None) -> None:
        """Remove only an unchanged private SQLite copy temporary.

        A failed snapshot/restore must not leave a residue, but a replacement
        or reparse must be preserved for manual review rather than unlinked.
        """

        if expected is None or cls._temporary_identity(path) != expected:
            return
        try:
            cls._regular_path(path, required=True, code="transaction_backup_unavailable")
            if cls._temporary_identity(path) == expected:
                path.unlink()
        except (OSError, ValueError, TransactionStoreError):
            return

    @staticmethod
    def _copy_sqlite(source: sqlite3.Connection, destination: Path) -> None:
        try:
            target = sqlite3.connect(str(destination), timeout=5.0, isolation_level=None)
        except sqlite3.Error as exc:
            raise TransactionStoreError("transaction_backup_unavailable") from exc
        try:
            deadline = time.monotonic() + _BACKUP_COPY_TIMEOUT_SECONDS

            def progress(_status: int, _remaining: int, _total: int) -> None:
                if time.monotonic() >= deadline:
                    raise _BackupCopyTimedOut

            # SQLite's default backup loop retries a busy source indefinitely.
            # Use page-sized progress callbacks so a writer-held EXCLUSIVE lock
            # becomes one finite, path-free unavailable result instead of a
            # stuck backup worker.
            source.backup(target, pages=128, progress=progress, sleep=0.05)
            target.execute("PRAGMA synchronous=FULL")
        except (_BackupCopyTimedOut, sqlite3.Error) as exc:
            raise TransactionStoreError("transaction_backup_unavailable") from exc
        finally:
            target.close()

    def backup_to(self, destination: Path) -> dict[str, int | str]:
        """Create a SQLite-consistent, validated snapshot without overwriting a leaf.

        The SQLite backup API reads a coherent database image even while the
        store has future writers.  A destination must be a new regular leaf;
        an existing backup is never overwritten implicitly.
        """

        target = self._regular_path(destination, required=False, code="transaction_backup_destination_unsafe")
        if target.exists():
            raise TransactionStoreError("transaction_backup_destination_exists")
        temporary = self._temporary_sibling(target, prefix="backup")
        temporary_identity = self._temporary_identity(temporary)
        try:
            with self._lock:
                # A backup is a read-only observation of the live journal.
                # Opening it through the ordinary write connection would run
                # journal-mode pragmas against a real Config database merely
                # to create a snapshot.  SQLite's backup API supports a
                # read-only source, so keep the source leaf untouched.
                source = self._readonly_connection(self.path)
                try:
                    self._copy_sqlite(source, temporary)
                finally:
                    source.close()
            bytes_written = self._validate_snapshot(temporary)
            if target.exists():
                raise TransactionStoreError("transaction_backup_destination_exists")
            os.replace(temporary, target)
            bytes_written = self._validate_snapshot(target)
            return {"status": "completed", "bytes_written": bytes_written, "schema_version": SCHEMA_VERSION}
        except TransactionStoreError:
            raise
        except (OSError, ValueError) as exc:
            raise TransactionStoreError("transaction_backup_unavailable") from exc
        finally:
            self._remove_owned_temporary(temporary, temporary_identity)

    def restore_from(self, snapshot: Path) -> dict[str, int | str]:
        """Restore only a prevalidated V8 snapshot through an atomic sibling swap.

        Validation happens before the live control database is touched.  A
        corrupt, reparse-backed or schema-mismatched snapshot therefore leaves
        the existing V8 journal untouched for forensic/manual review.
        """

        source_path = self._regular_path(snapshot, required=True, code="transaction_backup_invalid")
        self._validate_snapshot(source_path)
        live = self._regular_path(self.path, required=True, code="transaction_store_path_unsafe")
        temporary = self._temporary_sibling(live, prefix="restore")
        temporary_identity = self._temporary_identity(temporary)
        try:
            source = self._readonly_connection(source_path)
            try:
                self._copy_sqlite(source, temporary)
            finally:
                source.close()
            bytes_restored = self._validate_snapshot(temporary)
            self._regular_path(live, required=True, code="transaction_store_path_unsafe")
            os.replace(temporary, live)
            bytes_restored = self._validate_snapshot(live)
            return {"status": "completed", "bytes_restored": bytes_restored, "schema_version": SCHEMA_VERSION}
        except TransactionStoreError:
            raise
        except (OSError, ValueError) as exc:
            raise TransactionStoreError("transaction_backup_unavailable") from exc
        finally:
            self._remove_owned_temporary(temporary, temporary_identity)

    @staticmethod
    def _require_job_id(job_id: object) -> str:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise TransactionStoreError("invalid_job_id")
        return job_id

    @staticmethod
    def _require_reservation_id(value: object) -> str:
        if not isinstance(value, str) or _RESERVATION_ID.fullmatch(value) is None:
            raise TransactionStoreError("invalid_reservation_id")
        return value

    @staticmethod
    def _require_transaction_id(value: object) -> str:
        if not isinstance(value, str) or _TRANSACTION_ID.fullmatch(value) is None:
            raise TransactionStoreError("invalid_transaction_id")
        return value

    def create_output_reservation(self, job_id: str) -> str:
        job_id = self._require_job_id(job_id)
        reservation_id = f"outres_{secrets.token_hex(16)}"
        timestamp = _now()
        with self._write() as db:
            db.execute(
                "INSERT INTO output_reservations(reservation_id,job_id,state,created_at,updated_at) VALUES(?,?,?,?,?)",
                (reservation_id, job_id, "reserved", timestamp, timestamp),
            )
        return reservation_id

    def create_output_transaction(self, reservation_id: str, job_id: str) -> str:
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        transaction_id = f"outtx_{secrets.token_hex(16)}"
        timestamp = _now()
        with self._write() as db:
            reservation = db.execute(
                "SELECT state FROM output_reservations WHERE reservation_id=? AND job_id=?",
                (reservation_id, job_id),
            ).fetchone()
            if reservation is None or reservation["state"] != "reserved":
                raise TransactionStoreError("reservation_binding_invalid")
            db.execute(
                "INSERT INTO output_transactions(transaction_id,reservation_id,job_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (transaction_id, reservation_id, job_id, "preparing", timestamp, timestamp),
            )
        return transaction_id

    def stage_artifact(
        self,
        *,
        transaction_id: str,
        reservation_id: str,
        job_id: str,
        object_id: str,
        object_key: str,
        name: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
        file_device: int,
        file_inode: int,
        file_mtime_ns: int,
        provenance: dict[str, Any],
    ) -> str:
        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        if not isinstance(object_id, str) or _OBJECT_ID.fullmatch(object_id) is None:
            raise TransactionStoreError("invalid_object_id")
        object_key = _safe_object_key(object_key)
        name = _safe_name(name)
        if not isinstance(media_type, str) or _MEDIA_TYPE.fullmatch(media_type) is None:
            raise TransactionStoreError("invalid_media_type")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or not 0 <= size_bytes <= 8 * 1024**3:
            raise TransactionStoreError("invalid_artifact_size")
        if not isinstance(sha256, str) or _FINGERPRINT.fullmatch(sha256) is None:
            raise TransactionStoreError("invalid_artifact_hash")
        file_device = encode_file_identity_for_sqlite(file_device)
        file_inode = encode_file_identity_for_sqlite(file_inode)
        file_mtime_ns = encode_file_identity_for_sqlite(file_mtime_ns)
        if not isinstance(provenance, dict):
            raise TransactionStoreError("invalid_provenance")
        fingerprint = provenance.get("job_spec_fingerprint")
        adapter_id = provenance.get("adapter_id")
        attempt = provenance.get("attempt")
        status = provenance.get("status")
        if provenance.get("job_id") != job_id or not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
            raise TransactionStoreError("invalid_provenance")
        if not isinstance(adapter_id, str) or _ADAPTER_ID.fullmatch(adapter_id) is None or status != "completed":
            raise TransactionStoreError("invalid_provenance")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10_000:
            raise TransactionStoreError("invalid_provenance")
        artifact_id = f"artifact_{secrets.token_hex(16)}"
        timestamp = _now()
        with self._write() as db:
            transaction = db.execute(
                "SELECT state FROM output_transactions WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (transaction_id, reservation_id, job_id),
            ).fetchone()
            if transaction is None or transaction["state"] != "preparing":
                raise TransactionStoreError("transaction_binding_invalid")
            db.execute(
                """
                INSERT INTO artifact_objects(
                    artifact_id,object_id,transaction_id,reservation_id,job_id,object_key,name,media_type,
                    size_bytes,sha256,file_device,file_inode,file_mtime_ns,visibility,
                    job_spec_fingerprint,adapter_id,attempt,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'staged',?,?,?,?)
                """,
                (
                    artifact_id,
                    object_id,
                    transaction_id,
                    reservation_id,
                    job_id,
                    object_key,
                    name,
                    media_type,
                    size_bytes,
                    sha256,
                    file_device,
                    file_inode,
                    file_mtime_ns,
                    fingerprint,
                    adapter_id,
                    attempt,
                    timestamp,
                ),
            )
        return artifact_id

    def authorize_output_transaction(self, transaction_id: str, reservation_id: str, job_id: str) -> bool:
        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        timestamp = _now()
        with self._write() as db:
            transaction = db.execute(
                "SELECT state FROM output_transactions WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (transaction_id, reservation_id, job_id),
            ).fetchone()
            reservation = db.execute(
                "SELECT state FROM output_reservations WHERE reservation_id=? AND job_id=?",
                (reservation_id, job_id),
            ).fetchone()
            count = db.execute(
                "SELECT COUNT(*) AS count FROM artifact_objects WHERE transaction_id=? AND reservation_id=? AND job_id=? AND visibility='staged'",
                (transaction_id, reservation_id, job_id),
            ).fetchone()["count"]
            if transaction is None or transaction["state"] != "preparing" or reservation is None or reservation["state"] != "reserved" or count < 1:
                return False
            db.execute(
                "UPDATE output_transactions SET state='authorized', updated_at=? WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (timestamp, transaction_id, reservation_id, job_id),
            )
            db.execute(
                "UPDATE output_reservations SET state='authorized', updated_at=? WHERE reservation_id=? AND job_id=?",
                (timestamp, reservation_id, job_id),
            )
        return True

    def commit_output_transaction(self, transaction_id: str, reservation_id: str, job_id: str) -> list[dict[str, Any]] | None:
        """Final public commit. No caller-side persistence is required afterward."""

        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        timestamp = _now()
        with self._write() as db:
            transaction = db.execute(
                "SELECT state FROM output_transactions WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (transaction_id, reservation_id, job_id),
            ).fetchone()
            reservation = db.execute(
                "SELECT state FROM output_reservations WHERE reservation_id=? AND job_id=?",
                (reservation_id, job_id),
            ).fetchone()
            if transaction is None or reservation is None:
                return None
            if transaction["state"] == "committed" and reservation["state"] == "published":
                rows = db.execute(
                    "SELECT * FROM artifact_objects WHERE transaction_id=? AND reservation_id=? AND job_id=? AND visibility='published' ORDER BY created_at, artifact_id",
                    (transaction_id, reservation_id, job_id),
                ).fetchall()
                return [self._public_artifact(row) for row in rows]
            if transaction["state"] != "authorized" or reservation["state"] != "authorized":
                return None
            staged = db.execute(
                "SELECT COUNT(*) AS count FROM artifact_objects WHERE transaction_id=? AND reservation_id=? AND job_id=? AND visibility='staged'",
                (transaction_id, reservation_id, job_id),
            ).fetchone()["count"]
            if staged < 1:
                return None
            db.execute(
                "UPDATE artifact_objects SET visibility='published' WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (transaction_id, reservation_id, job_id),
            )
            db.execute(
                "UPDATE output_transactions SET state='committed', updated_at=? WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (timestamp, transaction_id, reservation_id, job_id),
            )
            db.execute(
                "UPDATE output_reservations SET state='published', updated_at=? WHERE reservation_id=? AND job_id=?",
                (timestamp, reservation_id, job_id),
            )
            rows = db.execute(
                "SELECT * FROM artifact_objects WHERE transaction_id=? AND reservation_id=? AND job_id=? AND visibility='published' ORDER BY created_at, artifact_id",
                (transaction_id, reservation_id, job_id),
            ).fetchall()
            return [self._public_artifact(row) for row in rows]

    def abort_output_transaction(self, transaction_id: str, reservation_id: str, job_id: str) -> bool:
        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        timestamp = _now()
        with self._write() as db:
            transaction = db.execute(
                "SELECT state FROM output_transactions WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (transaction_id, reservation_id, job_id),
            ).fetchone()
            if transaction is None or transaction["state"] == "committed":
                return False
            db.execute(
                "UPDATE output_transactions SET state='aborted', updated_at=? WHERE transaction_id=? AND reservation_id=? AND job_id=?",
                (timestamp, transaction_id, reservation_id, job_id),
            )
            db.execute(
                "UPDATE output_reservations SET state='aborted', updated_at=? WHERE reservation_id=? AND job_id=? AND state!='published'",
                (timestamp, reservation_id, job_id),
            )
        return True

    def abort_reservation(self, reservation_id: str, job_id: str) -> bool:
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        timestamp = _now()
        with self._write() as db:
            row = db.execute(
                "SELECT state FROM output_reservations WHERE reservation_id=? AND job_id=?",
                (reservation_id, job_id),
            ).fetchone()
            if row is None or row["state"] == "published":
                return False
            db.execute(
                "UPDATE output_transactions SET state='aborted', updated_at=? WHERE reservation_id=? AND job_id=? AND state!='committed'",
                (timestamp, reservation_id, job_id),
            )
            db.execute(
                "UPDATE output_reservations SET state='aborted', updated_at=? WHERE reservation_id=? AND job_id=?",
                (timestamp, reservation_id, job_id),
            )
        return True

    def close_no_output(self, reservation_id: str, job_id: str) -> bool:
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        timestamp = _now()
        with self._write() as db:
            row = db.execute(
                "SELECT state FROM output_reservations WHERE reservation_id=? AND job_id=?",
                (reservation_id, job_id),
            ).fetchone()
            if row is None or row["state"] != "reserved":
                return False
            db.execute(
                "UPDATE output_reservations SET state='no_output', updated_at=? WHERE reservation_id=? AND job_id=?",
                (timestamp, reservation_id, job_id),
            )
        return True

    @staticmethod
    def _public_artifact(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["artifact_id"],
            "name": row["name"],
            "size_bytes": int(row["size_bytes"]),
            "media_type": row["media_type"],
            "created_at": row["created_at"],
            "sha256": row["sha256"],
            "url": f"/api/artifacts/{row['artifact_id']}",
            "provenance": {
                "job_id": row["job_id"],
                "job_spec_fingerprint": row["job_spec_fingerprint"],
                "adapter_id": row["adapter_id"],
                "attempt": int(row["attempt"]),
                "status": "completed",
            },
        }

    def public_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
            return None
        with self._read() as db:
            row = db.execute(
                """
                SELECT a.* FROM artifact_objects a
                JOIN output_transactions t
                  ON t.transaction_id=a.transaction_id AND t.reservation_id=a.reservation_id AND t.job_id=a.job_id
                JOIN output_reservations r
                  ON r.reservation_id=a.reservation_id AND r.job_id=a.job_id
                WHERE a.artifact_id=? AND a.visibility='published'
                  AND t.state='committed' AND r.state='published'
                """,
                (artifact_id,),
            ).fetchone()
            return self._public_artifact(row) if row is not None else None

    def internal_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        if not isinstance(artifact_id, str) or _ARTIFACT_ID.fullmatch(artifact_id) is None:
            return None
        with self._read() as db:
            row = db.execute("SELECT * FROM artifact_objects WHERE artifact_id=?", (artifact_id,)).fetchone()
            return dict(row) if row is not None else None

    def list_public_artifacts(self, *, limit: int = 240) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._read() as db:
            rows = db.execute(
                """
                SELECT a.* FROM artifact_objects a
                JOIN output_transactions t
                  ON t.transaction_id=a.transaction_id AND t.reservation_id=a.reservation_id AND t.job_id=a.job_id
                JOIN output_reservations r
                  ON r.reservation_id=a.reservation_id AND r.job_id=a.job_id
                WHERE a.visibility='published' AND t.state='committed' AND r.state='published'
                ORDER BY a.created_at DESC, a.artifact_id DESC LIMIT ?
                """,
                (bounded,),
            ).fetchall()
            return [self._public_artifact(row) for row in rows]

    def incomplete_artifacts(self, *, limit: int = 256) -> list[dict[str, Any]]:
        bounded = max(1, min(1024, int(limit)))
        with self._read() as db:
            rows = db.execute(
                """
                SELECT a.* FROM artifact_objects a
                JOIN output_transactions t
                  ON t.transaction_id=a.transaction_id AND t.reservation_id=a.reservation_id AND t.job_id=a.job_id
                WHERE t.state IN ('preparing','authorized','aborted') AND a.visibility='staged'
                ORDER BY a.created_at LIMIT ?
                """,
                (bounded,),
            ).fetchall()
            return [dict(row) for row in rows]

    def known_artifact_object_keys(self, *, limit: int = 4096) -> set[str]:
        """Return a bounded internal key set for orphan reconciliation.

        The result remains inside the server-owned storage boundary. It lets
        startup distinguish a journal-owned managed object from an untracked
        object that must be preserved for manual review after a copy-phase
        process interruption.
        """

        bounded = max(1, min(4096, int(limit)))
        with self._read() as db:
            rows = db.execute(
                "SELECT object_key FROM artifact_objects ORDER BY created_at DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        return {str(row["object_key"]) for row in rows if isinstance(row["object_key"], str)}

    def create_component_operation(
        self,
        *,
        plan_id: str,
        component_id: str,
        component_type: str,
        action: str,
        expected_state_fingerprint: str,
    ) -> str:
        if not isinstance(plan_id, str) or _SAFE_PLAN.fullmatch(plan_id) is None:
            raise TransactionStoreError("invalid_plan_id")
        if not isinstance(component_id, str) or _COMPONENT_ID.fullmatch(component_id) is None:
            raise TransactionStoreError("invalid_component_id")
        if component_type not in {"model", "runtime"}:
            raise TransactionStoreError("invalid_component_type")
        if not isinstance(action, str) or _SAFE_ACTION.fullmatch(action) is None:
            raise TransactionStoreError("invalid_component_action")
        if not isinstance(expected_state_fingerprint, str) or _FINGERPRINT.fullmatch(expected_state_fingerprint) is None:
            raise TransactionStoreError("invalid_state_fingerprint")
        operation_id = f"compop_{secrets.token_hex(16)}"
        timestamp = _now()
        with self._write() as db:
            db.execute(
                """
                INSERT INTO component_operations(
                    operation_id,plan_id,component_id,component_type,action,expected_state_fingerprint,
                    state,result_code,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,'planned',NULL,?,?)
                """,
                (operation_id, plan_id, component_id, component_type, action, expected_state_fingerprint, timestamp, timestamp),
            )
        return operation_id

    def transition_component_operation(
        self,
        operation_id: str,
        *,
        expected_state: str,
        target_state: str,
        result_code: str | None = None,
    ) -> bool:
        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise TransactionStoreError("invalid_operation_id")
        if expected_state not in _COMPONENT_TRANSITIONS or target_state not in _COMPONENT_TRANSITIONS[expected_state]:
            raise TransactionStoreError("invalid_component_transition")
        if result_code is not None and (not isinstance(result_code, str) or _SAFE_ACTION.fullmatch(result_code) is None):
            raise TransactionStoreError("invalid_result_code")
        timestamp = _now()
        with self._write() as db:
            row = db.execute("SELECT state FROM component_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None or row["state"] != expected_state:
                return False
            db.execute(
                "UPDATE component_operations SET state=?, result_code=?, updated_at=? WHERE operation_id=? AND state=?",
                (target_state, result_code, timestamp, operation_id, expected_state),
            )
        return True

    def component_operation(self, operation_id: str) -> dict[str, Any] | None:
        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            return None
        with self._read() as db:
            row = db.execute("SELECT * FROM component_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                return None
            value = dict(row)
        return {
            "operation_id": value["operation_id"],
            "plan_id": value["plan_id"],
            "component_id": value["component_id"],
            "component_type": value["component_type"],
            "action": value["action"],
            "state": value["state"],
            "result_code": value["result_code"],
            "created_at": value["created_at"],
            "updated_at": value["updated_at"],
        }

    def list_component_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(500, int(limit)))
        with self._read() as db:
            rows = db.execute(
                "SELECT operation_id FROM component_operations ORDER BY created_at DESC, operation_id DESC LIMIT ?",
                (bounded,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self.component_operation(str(row["operation_id"]))
            if item is not None:
                result.append(item)
        return result

    def create_component_bundle_journal(
        self,
        operation_id: str,
        *,
        steps: list[Mapping[str, Any]],
    ) -> None:
        """Persist the exact server-owned bundle step set before execution.

        This is deliberately separate from the in-memory V7 plan.  It gives a
        restarted V8 coordinator just enough evidence to compensate a step
        that was durably observed as newly installed, without re-running the
        original plan or guessing about a step that was interrupted mid-call.
        """

        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise TransactionStoreError("invalid_operation_id")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 64:
            raise TransactionStoreError("invalid_component_bundle_steps")
        prepared: list[tuple[int, str, str, str]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                raise TransactionStoreError("invalid_component_bundle_steps")
            step_index = step.get("step_index")
            component_id = step.get("component_id")
            component_type = step.get("component_type")
            fingerprint = step.get("state_fingerprint")
            if (
                type(step_index) is not int
                or step_index != index
                or not isinstance(component_id, str)
                or _COMPONENT_ID.fullmatch(component_id) is None
                or component_type not in {"model", "runtime"}
                or not isinstance(fingerprint, str)
                or _FINGERPRINT.fullmatch(fingerprint) is None
            ):
                raise TransactionStoreError("invalid_component_bundle_steps")
            prepared.append((step_index, component_id, str(component_type), fingerprint))
        timestamp = _now()
        with self._write() as db:
            operation = db.execute(
                "SELECT action,state FROM component_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if operation is None or operation["action"] != "bundle" or operation["state"] != "planned":
                raise TransactionStoreError("invalid_component_bundle_operation")
            existing = db.execute(
                "SELECT 1 FROM component_bundle_steps WHERE operation_id=? LIMIT 1", (operation_id,)
            ).fetchone()
            if existing is not None:
                raise TransactionStoreError("component_bundle_journal_exists")
            db.executemany(
                """
                INSERT INTO component_bundle_steps(
                    operation_id,step_index,component_id,component_type,state_fingerprint,phase,created_at,updated_at
                ) VALUES(?,?,?,?,?,'pending',?,?)
                """,
                [(operation_id, index, component_id, component_type, fingerprint, timestamp, timestamp)
                 for index, component_id, component_type, fingerprint in prepared],
            )

    def transition_component_bundle_step(
        self,
        operation_id: str,
        *,
        step_index: int,
        expected_phase: str,
        target_phase: str,
    ) -> bool:
        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise TransactionStoreError("invalid_operation_id")
        if type(step_index) is not int or not 0 <= step_index < 64:
            raise TransactionStoreError("invalid_component_bundle_step")
        if expected_phase not in _BUNDLE_STEP_PHASES or target_phase not in _BUNDLE_STEP_PHASES:
            raise TransactionStoreError("invalid_component_bundle_phase")
        timestamp = _now()
        with self._write() as db:
            row = db.execute(
                "SELECT phase FROM component_bundle_steps WHERE operation_id=? AND step_index=?",
                (operation_id, step_index),
            ).fetchone()
            if row is None or row["phase"] != expected_phase:
                return False
            db.execute(
                """
                UPDATE component_bundle_steps SET phase=?,updated_at=?
                WHERE operation_id=? AND step_index=? AND phase=?
                """,
                (target_phase, timestamp, operation_id, step_index, expected_phase),
            )
        return True

    def component_bundle_steps(self, operation_id: str) -> list[dict[str, Any]]:
        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            return []
        with self._read() as db:
            rows = db.execute(
                """
                SELECT step_index,component_id,component_type,state_fingerprint,phase
                FROM component_bundle_steps WHERE operation_id=? ORDER BY step_index ASC
                """,
                (operation_id,),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = ["SCHEMA_VERSION", "TransactionStoreError", "V8TransactionStore"]
