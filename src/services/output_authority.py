"""V8 output publication authority.

Producer files are input material only. A public artifact is backed by a
Hub-owned immutable managed object and becomes visible only after the exact
reservation/job/transaction binding is durably authorized.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import secrets
from typing import Any, Iterable

from src.platform.paths import HubPaths, get_paths
from src.platform.storage_authority import FileIdentity, RootLease, StorageAuthority, StorageAuthorityError
from src.services.transaction_store import TransactionStoreError, V8TransactionStore


MAX_ARTIFACTS_PER_TRANSACTION = 64
MAX_ARTIFACT_BYTES = 8 * 1024**3
MAX_TRANSACTION_BYTES = 16 * 1024**3
COPY_CHUNK_BYTES = 1024 * 1024
_JOB_ID = re.compile(r"^(?:jobv5_[a-f0-9]{32}|job_[0-9]{8}_[0-9]{6}_[a-f0-9]{8})$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_MEDIA = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")


class OutputAuthorityError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 180:
        raise OutputAuthorityError("invalid_artifact_name")
    if any(char in value for char in ("\x00", "\r", "\n", "/", "\\")) or value in {".", ".."}:
        raise OutputAuthorityError("invalid_artifact_name")
    return value


def _safe_media_type(value: object, fallback_name: str) -> str:
    if isinstance(value, str) and _SAFE_MEDIA.fullmatch(value):
        return value
    suffix = Path(fallback_name).suffix.casefold()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".json": "application/json",
        ".srt": "application/x-subrip",
        ".wav": "audio/wav",
    }.get(suffix, "application/octet-stream")


def _safe_provenance(value: object, job_id: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "job_id",
        "job_spec_fingerprint",
        "adapter_id",
        "attempt",
        "status",
    }:
        raise OutputAuthorityError("invalid_provenance")
    fingerprint = value.get("job_spec_fingerprint")
    adapter_id = value.get("adapter_id")
    attempt = value.get("attempt")
    if value.get("job_id") != job_id or value.get("status") != "completed":
        raise OutputAuthorityError("invalid_provenance")
    if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
        raise OutputAuthorityError("invalid_provenance")
    if not isinstance(adapter_id, str) or _ADAPTER_ID.fullmatch(adapter_id) is None:
        raise OutputAuthorityError("invalid_provenance")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or not 1 <= attempt <= 10_000:
        raise OutputAuthorityError("invalid_provenance")
    return {
        "job_id": job_id,
        "job_spec_fingerprint": fingerprint,
        "adapter_id": adapter_id,
        "attempt": attempt,
        "status": "completed",
    }


class OutputAuthority:
    """Prepare immutable objects privately and publish only as a final DB commit."""

    def __init__(
        self,
        *,
        paths: HubPaths | None = None,
        storage: StorageAuthority | None = None,
        store: V8TransactionStore | None = None,
    ) -> None:
        self.paths = paths or get_paths()
        self.storage = storage or StorageAuthority(self.paths)
        self.store = store or V8TransactionStore.for_paths(self.paths)

    def _output_lease(self, *, create: bool = True) -> RootLease:
        lease = self.storage.lease("output", create=create)
        if create:
            self.storage.mkdir_relative(lease, ".hub-v8")
            self.storage.mkdir_relative(lease, ".hub-v8/objects")
        lease.assert_current()
        return lease

    @staticmethod
    def _require_job_id(job_id: object) -> str:
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            raise OutputAuthorityError("invalid_job_id")
        return job_id

    def begin_reservation(self, job_id: str) -> str:
        """Reserve publication authority before a producer starts."""

        job_id = self._require_job_id(job_id)
        self._output_lease(create=True)
        try:
            return self.store.create_output_reservation(job_id)
        except TransactionStoreError as exc:
            raise OutputAuthorityError(exc.code) from exc

    def abort_reservation(self, reservation_id: str, job_id: str) -> bool:
        job_id = self._require_job_id(job_id)
        try:
            return self.store.abort_reservation(reservation_id, job_id)
        except TransactionStoreError:
            return False

    def close_no_output(self, reservation_id: str, job_id: str) -> bool:
        job_id = self._require_job_id(job_id)
        try:
            return self.store.close_no_output(reservation_id, job_id)
        except TransactionStoreError:
            return False

    def _producer_file(
        self,
        lease: RootLease,
        value: Path | str,
    ) -> tuple[Path, Path, FileIdentity]:
        path, relative, identity = self.storage.file_identity_path(lease, value)
        if relative.parts[:1] == (".hub-v8",):
            raise OutputAuthorityError("managed_object_not_producer_input")
        if identity.size_bytes < 0 or identity.size_bytes > MAX_ARTIFACT_BYTES:
            raise OutputAuthorityError("artifact_size_out_of_bounds")
        return path, relative, identity

    def _cleanup_object(self, lease: RootLease, relative: Path, identity: FileIdentity | None) -> None:
        if identity is None:
            return
        self.storage.unlink_if_identity(lease, relative, identity)

    def _copy_managed_object(
        self,
        lease: RootLease,
        source_path: Path,
        source_identity: FileIdentity,
    ) -> tuple[str, str, int, str, FileIdentity]:
        """Copy one producer file into a new private copy-once managed object."""

        object_id = f"obj_{secrets.token_hex(16)}"
        shard = object_id[4:6]
        self.storage.mkdir_relative(lease, f".hub-v8/objects/{shard}")
        relative = Path(".hub-v8") / "objects" / shard / object_id
        destination = self.storage.resolve_relative(lease, relative, require_exists=False)
        digest = hashlib.sha256()
        copied = 0
        destination_identity: FileIdentity | None = None
        try:
            with source_path.open("rb") as source, destination.open("xb") as target:
                first = FileIdentity.from_stat(os.fstat(source.fileno()))
                if not source_identity.same_file_state(first):
                    raise OutputAuthorityError("producer_identity_changed")
                while True:
                    chunk = source.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_ARTIFACT_BYTES:
                        raise OutputAuthorityError("artifact_size_out_of_bounds")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
                last = FileIdentity.from_stat(os.fstat(source.fileno()))
                if not first.same_file_state(last) or copied != first.size_bytes:
                    raise OutputAuthorityError("producer_identity_changed")
            _, _, current_source = self.storage.file_identity_path(lease, source_path)
            if not source_identity.same_file_state(current_source):
                raise OutputAuthorityError("producer_identity_changed")
            destination_identity = self.storage.file_identity(lease, relative)
            if destination_identity.size_bytes != copied:
                raise OutputAuthorityError("managed_object_identity_invalid")
            lease.assert_current()
            return object_id, relative.as_posix(), copied, digest.hexdigest(), destination_identity
        except Exception:
            if destination_identity is None:
                try:
                    destination_identity = self.storage.file_identity(lease, relative)
                except StorageAuthorityError:
                    destination_identity = None
            self._cleanup_object(lease, relative, destination_identity)
            raise

    def _validate_managed_row(self, lease: RootLease, row: dict[str, Any]) -> tuple[Path, FileIdentity]:
        object_key = row.get("object_key")
        if not isinstance(object_key, str) or not object_key.startswith(".hub-v8/objects/"):
            raise OutputAuthorityError("managed_object_key_invalid")
        path = self.storage.resolve_relative(lease, object_key, require_exists=True, expect_file=True)
        identity = self.storage.file_identity(lease, object_key)
        expected = FileIdentity(
            device=int(row.get("file_device", -1)),
            inode=int(row.get("file_inode", -1)),
            size_bytes=int(row.get("size_bytes", -1)),
            mtime_ns=int(row.get("file_mtime_ns", -1)),
            mode=identity.mode,
        )
        if not expected.same_file_state(identity):
            raise OutputAuthorityError("managed_object_identity_changed")
        return path, identity

    def publish_owned_candidates(
        self,
        *,
        reservation_id: str,
        job_id: str,
        candidates: Iterable[Path | str],
        provenance: dict[str, Any],
        names: Iterable[str] | None = None,
        media_types: Iterable[str | None] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Prepare, authorize and publish one exact reservation-bound batch.

        Public artifact name/media metadata are independent from private
        producer filenames. This prevents temporary server-owned producer names
        from leaking into the product surface while keeping the filesystem
        candidate itself subject to the same identity checks.
        """

        job_id = self._require_job_id(job_id)
        safe_provenance = _safe_provenance(provenance, job_id)
        values = list(candidates)
        if not 1 <= len(values) <= MAX_ARTIFACTS_PER_TRANSACTION:
            return None
        requested_names = list(names) if names is not None else [None] * len(values)
        requested_media = list(media_types) if media_types is not None else [None] * len(values)
        if len(requested_names) != len(values) or len(requested_media) != len(values):
            return None
        lease = self._output_lease(create=True)
        producer_records: list[tuple[Path, Path, FileIdentity, str, str]] = []
        total = 0
        seen: set[Path] = set()
        try:
            for index, value in enumerate(values):
                path, relative, identity = self._producer_file(lease, value)
                if path in seen:
                    raise OutputAuthorityError("duplicate_producer_output")
                seen.add(path)
                total += identity.size_bytes
                if total > MAX_TRANSACTION_BYTES:
                    raise OutputAuthorityError("transaction_size_out_of_bounds")
                public_name = _safe_name(requested_names[index] if requested_names[index] is not None else path.name)
                public_media = _safe_media_type(requested_media[index], public_name)
                producer_records.append((path, relative, identity, public_name, public_media))
            transaction_id = self.store.create_output_transaction(reservation_id, job_id)
        except (OutputAuthorityError, StorageAuthorityError, TransactionStoreError):
            return None

        prepared: list[tuple[str, Path, FileIdentity]] = []
        try:
            for source_path, _relative, source_identity, public_name, public_media in producer_records:
                object_id, object_key, size_bytes, digest, object_identity = self._copy_managed_object(
                    lease, source_path, source_identity
                )
                artifact_id = self.store.stage_artifact(
                    transaction_id=transaction_id,
                    reservation_id=reservation_id,
                    job_id=job_id,
                    object_id=object_id,
                    object_key=object_key,
                    name=public_name,
                    media_type=public_media,
                    size_bytes=size_bytes,
                    sha256=digest,
                    file_device=object_identity.device,
                    file_inode=object_identity.inode,
                    file_mtime_ns=object_identity.mtime_ns,
                    provenance=safe_provenance,
                )
                prepared.append((artifact_id, Path(object_key), object_identity))

            for artifact_id, _relative, _identity in prepared:
                row = self.store.internal_artifact(artifact_id)
                if row is None:
                    raise OutputAuthorityError("prepared_artifact_missing")
                self._validate_managed_row(lease, row)
            if not self.store.authorize_output_transaction(transaction_id, reservation_id, job_id):
                raise OutputAuthorityError("transaction_authorization_failed")
            for artifact_id, _relative, _identity in prepared:
                row = self.store.internal_artifact(artifact_id)
                if row is None:
                    raise OutputAuthorityError("prepared_artifact_missing")
                self._validate_managed_row(lease, row)
            lease.assert_current()
            published = self.store.commit_output_transaction(transaction_id, reservation_id, job_id)
            return published if isinstance(published, list) and len(published) == len(prepared) else None
        except (OutputAuthorityError, StorageAuthorityError, TransactionStoreError, OSError, ValueError):
            try:
                self.store.abort_output_transaction(transaction_id, reservation_id, job_id)
            except TransactionStoreError:
                pass
            for _artifact_id, relative, identity in prepared:
                self._cleanup_object(lease, relative, identity)
            return None

    def resolve(self, artifact_id: str) -> Path | None:
        """Resolve only a committed public artifact whose managed object identity matches."""

        public = self.store.public_artifact(artifact_id)
        internal = self.store.internal_artifact(artifact_id)
        if public is None or internal is None:
            return None
        try:
            lease = self._output_lease(create=False)
            path, _identity = self._validate_managed_row(lease, internal)
            return path
        except (OutputAuthorityError, StorageAuthorityError, OSError, ValueError):
            return None

    def describe(self, artifact_id: str) -> dict[str, Any] | None:
        return self.store.public_artifact(artifact_id) if self.resolve(artifact_id) is not None else None

    def list_public(self, *, limit: int = 240) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in self.store.list_public_artifacts(limit=limit):
            artifact_id = item.get("id")
            if isinstance(artifact_id, str) and self.resolve(artifact_id) is not None:
                result.append(item)
        return result

    def reconcile_incomplete(self) -> dict[str, int]:
        """Abort non-public transactions and clean only exact managed-object identities."""

        lease = self._output_lease(create=True)
        aborted = 0
        removed = 0
        manual_review = 0
        rows = self.store.incomplete_artifacts()
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            key = (
                str(row.get("transaction_id") or ""),
                str(row.get("reservation_id") or ""),
                str(row.get("job_id") or ""),
            )
            grouped.setdefault(key, []).append(row)
        for (transaction_id, reservation_id, job_id), items in grouped.items():
            try:
                did_abort = self.store.abort_output_transaction(transaction_id, reservation_id, job_id)
            except TransactionStoreError:
                did_abort = False
            if not did_abort:
                manual_review += len(items)
                continue
            aborted += 1
            for row in items:
                try:
                    _path, identity = self._validate_managed_row(lease, row)
                    if self.storage.unlink_if_identity(lease, row["object_key"], identity):
                        removed += 1
                    else:
                        manual_review += 1
                except (OutputAuthorityError, StorageAuthorityError, KeyError, TypeError, ValueError):
                    manual_review += 1
        return {"aborted_transactions": aborted, "removed_objects": removed, "manual_review": manual_review}


__all__ = [
    "MAX_ARTIFACTS_PER_TRANSACTION",
    "MAX_ARTIFACT_BYTES",
    "MAX_TRANSACTION_BYTES",
    "OutputAuthority",
    "OutputAuthorityError",
]
