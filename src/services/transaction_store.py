"""SQLite-backed V8 transactional metadata store.

The database is an internal control-plane journal. It stores only opaque IDs,
bounded relative object keys and verification metadata; public callers never
receive a workstation path. Output publication is a single SQLite commit after
reservation/job/transaction binding has been proven.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import re
import secrets
import sqlite3
import threading
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

_COMPONENT_TRANSITIONS = {
    "planned": frozenset({"executing", "blocked", "cancelled"}),
    "executing": frozenset({"verifying", "committed", "failed", "blocked", "cancelled"}),
    "verifying": frozenset({"committed", "failed", "blocked"}),
    "committed": frozenset(),
    "failed": frozenset(),
    "blocked": frozenset(),
    "cancelled": frozenset(),
}


class TransactionStoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


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


class V8TransactionStore:
    """Authoritative metadata journal for V8 output and component operations."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).absolute()
        self._lock = threading.RLock()
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
                CREATE INDEX IF NOT EXISTS idx_artifact_visibility ON artifact_objects(visibility, created_at);
                CREATE INDEX IF NOT EXISTS idx_component_operations ON component_operations(component_id, created_at);
                """
            )
            row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                db.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
            elif row["value"] != SCHEMA_VERSION:
                raise TransactionStoreError("transaction_store_schema_mismatch")

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
        for value in (file_device, file_inode, file_mtime_ns):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TransactionStoreError("invalid_file_identity")
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


__all__ = ["SCHEMA_VERSION", "TransactionStoreError", "V8TransactionStore"]
