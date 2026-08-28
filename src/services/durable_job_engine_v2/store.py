"""Small SQLite metadata store for Durable Job Engine V2.

The store owns only job metadata. It never contains paths, raw user inputs,
artifact bytes, worker callables, process handles, credentials, or model data.
Read calls do not create a database; the database is initialized only by a
server-owned mutation such as admission, archive, or history deletion.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from src.platform.paths import is_reparse_point


STORE_SCHEMA_VERSION = "durable-job-store.v2"
_JOB_ID = re.compile(r"^jobv2_[a-f0-9]{32}$")
_MAX_RECORD_BYTES = 128 * 1024


class DurableJobStoreV2Error(RuntimeError):
    """Fixed failure code for the V2 durable metadata store."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DurableJobStoreV2Error("durable_job_v2_serialization_invalid") from exc


class DurableJobStoreV2:
    """Atomic SQLite records under a caller-owned managed config leaf."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).absolute()
        self._lock = threading.RLock()

    def _safe_parent(self, *, create: bool) -> None:
        parent = self.path.parent
        # Validate the existing ancestry *before* mkdir().  Creating a child
        # through an already-existing junction would otherwise follow it
        # before this store could reject the reparse point.
        current = parent
        while True:
            try:
                if current.exists() and (not current.is_dir() or is_reparse_point(current)):
                    raise DurableJobStoreV2Error("durable_job_v2_store_unavailable")
            except OSError as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            if current.parent == current:
                break
            current = current.parent
        if create:
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
        current = parent
        while True:
            try:
                if not current.exists() or not current.is_dir() or is_reparse_point(current):
                    raise DurableJobStoreV2Error("durable_job_v2_store_unavailable")
            except OSError as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            if current.parent == current:
                break
            current = current.parent
        try:
            if self.path.exists() and (not self.path.is_file() or is_reparse_point(self.path)):
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable")
        except OSError as exc:
            raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc

    def _connect(self, *, write: bool) -> sqlite3.Connection | None:
        if not write:
            try:
                if not self.path.parent.exists():
                    return None
            except OSError as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
        self._safe_parent(create=write)
        if not self.path.exists():
            return None if not write else sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        try:
            if write:
                return sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            return sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=5.0, isolation_level=None)
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS durable_jobs_v2("
                "job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                connection.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (STORE_SCHEMA_VERSION,))
            elif str(row[0]) != STORE_SCHEMA_VERSION:
                raise DurableJobStoreV2Error("durable_job_v2_schema_mismatch")
            connection.execute("COMMIT")
        except DurableJobStoreV2Error:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc

    @staticmethod
    def _validate_job_id(value: object) -> str:
        if not isinstance(value, str) or _JOB_ID.fullmatch(value) is None:
            raise DurableJobStoreV2Error("durable_job_v2_id_invalid")
        return value

    @staticmethod
    def _decode(value: object) -> dict[str, Any] | None:
        if not isinstance(value, str) or len(value.encode("utf-8")) > _MAX_RECORD_BYTES:
            return None
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def get(self, job_id: object) -> dict[str, Any] | None:
        identifier = self._validate_job_id(job_id)
        with self._lock:
            connection = self._connect(write=False)
            if connection is None:
                return None
            try:
                row = connection.execute("SELECT record_json FROM durable_jobs_v2 WHERE job_id=?", (identifier,)).fetchone()
            except sqlite3.Error as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            finally:
                connection.close()
        record = self._decode(row[0]) if row is not None else None
        if row is not None and record is None:
            raise DurableJobStoreV2Error("durable_job_v2_record_invalid")
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._connect(write=False)
            if connection is None:
                return []
            try:
                rows = connection.execute("SELECT record_json FROM durable_jobs_v2 ORDER BY created_at DESC, job_id DESC LIMIT 512").fetchall()
            except sqlite3.Error as exc:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            finally:
                connection.close()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = self._decode(row[0])
            if record is None:
                raise DurableJobStoreV2Error("durable_job_v2_record_invalid")
            result.append(record)
        return result

    def put(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(record, Mapping):
            raise DurableJobStoreV2Error("durable_job_v2_record_invalid")
        detached = json.loads(_canonical(dict(record)))
        job_id = self._validate_job_id(detached.get("job_id"))
        timestamps = detached.get("timestamps") if isinstance(detached.get("timestamps"), Mapping) else {}
        created_at = timestamps.get("created_at")
        updated_at = timestamps.get("updated_at")
        if not isinstance(created_at, str) or not isinstance(updated_at, str):
            raise DurableJobStoreV2Error("durable_job_v2_record_invalid")
        encoded = _canonical(detached)
        if len(encoded.encode("utf-8")) > _MAX_RECORD_BYTES:
            raise DurableJobStoreV2Error("durable_job_v2_record_too_large")
        with self._lock:
            connection = self._connect(write=True)
            if connection is None:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable")
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO durable_jobs_v2(job_id,record_json,created_at,updated_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(job_id) DO UPDATE SET record_json=excluded.record_json, updated_at=excluded.updated_at",
                    (job_id, encoded, created_at, updated_at),
                )
                connection.execute("COMMIT")
            except DurableJobStoreV2Error:
                raise
            except sqlite3.Error as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            finally:
                connection.close()
        return detached

    def delete_many(self, job_ids: Sequence[object]) -> int:
        """Atomically remove selected metadata rows only.

        Callers must have already established that each row is terminal. The
        store deliberately has no artifact table or file reference, so this
        transaction cannot remove user artifacts. A missing row causes the
        whole operation to fail before any deletion is committed.
        """

        if not isinstance(job_ids, Sequence) or isinstance(job_ids, (str, bytes)):
            raise DurableJobStoreV2Error("durable_job_v2_id_invalid")
        identifiers = [self._validate_job_id(item) for item in job_ids]
        if not identifiers or len(identifiers) > 100 or len(identifiers) != len(set(identifiers)):
            raise DurableJobStoreV2Error("durable_job_v2_id_invalid")
        with self._lock:
            connection = self._connect(write=True)
            if connection is None:
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable")
            try:
                self._initialize(connection)
                connection.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in identifiers)
                rows = connection.execute(
                    f"SELECT job_id FROM durable_jobs_v2 WHERE job_id IN ({placeholders})",
                    tuple(identifiers),
                ).fetchall()
                if len(rows) != len(identifiers):
                    raise DurableJobStoreV2Error("durable_job_v2_record_missing")
                row = connection.execute(
                    f"DELETE FROM durable_jobs_v2 WHERE job_id IN ({placeholders})",
                    tuple(identifiers),
                )
                connection.execute("COMMIT")
                return int(row.rowcount)
            except DurableJobStoreV2Error:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            except sqlite3.Error as exc:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise DurableJobStoreV2Error("durable_job_v2_store_unavailable") from exc
            finally:
                connection.close()

    def delete(self, job_id: object) -> bool:
        try:
            return self.delete_many([job_id]) == 1
        except DurableJobStoreV2Error as exc:
            if exc.code == "durable_job_v2_record_missing":
                return False
            raise


__all__ = ["STORE_SCHEMA_VERSION", "DurableJobStoreV2", "DurableJobStoreV2Error"]
