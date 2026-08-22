"""Production-migration Output Authority for V8 Wave 2."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.platform.paths import HubPaths, get_paths, is_reparse_point
from src.platform.storage_authority import StorageAuthorityError
from src.services.output_authority import OutputAuthority, OutputAuthorityError
from src.services.transaction_store import TransactionStoreError
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


class ProductionOutputAuthority(OutputAuthority):
    """Wave 0 authority plus restart-safe cleanup for aborted staged rows."""

    def __init__(self, *, paths: HubPaths | None = None, store: V8ProductionTransactionStore | None = None) -> None:
        active = paths or get_paths()
        super().__init__(
            paths=active,
            store=store or V8ProductionTransactionStore.for_paths(active),
        )
        if not isinstance(self.store, V8ProductionTransactionStore):
            raise TypeError("ProductionOutputAuthority requires V8ProductionTransactionStore.")

    def _count_untracked_managed_objects(self, lease: Any) -> int:
        """Count bounded orphan candidates without deleting ambiguous bytes.

        A crash after a copy succeeds but before ``stage_artifact`` writes its
        row leaves no ownership proof for the object. Treating a matching name
        as permission to delete would risk a foreign replacement, so startup
        reports it through the existing manual-review count and preserves it.
        """

        try:
            known = self.store.known_artifact_object_keys(limit=4096)
            root_relative = ".hub-v8/objects"
            root = self.storage.resolve_relative(lease, root_relative, require_exists=True, expect_file=False)
        except (StorageAuthorityError, TransactionStoreError):
            return 1
        manual_review = 0
        scanned = 0
        try:
            with os.scandir(root) as shards:
                for shard in shards:
                    if scanned >= 1024:
                        return manual_review + 1
                    scanned += 1
                    if shard.is_symlink() or not shard.is_dir(follow_symlinks=False) or is_reparse_point(Path(shard.path)):
                        manual_review += 1
                        continue
                    shard_name = shard.name
                    if len(shard_name) != 2 or any(char not in "0123456789abcdef" for char in shard_name):
                        manual_review += 1
                        continue
                    shard_relative = f"{root_relative}/{shard_name}"
                    try:
                        directory = self.storage.resolve_relative(
                            lease, shard_relative, require_exists=True, expect_file=False
                        )
                    except StorageAuthorityError:
                        manual_review += 1
                        continue
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if scanned >= 1024:
                                return manual_review + 1
                            scanned += 1
                            if entry.is_symlink() or not entry.is_file(follow_symlinks=False) or is_reparse_point(Path(entry.path)):
                                manual_review += 1
                                continue
                            name = entry.name
                            if len(name) != 36 or not name.startswith("obj_") or any(char not in "0123456789abcdef" for char in name[4:]):
                                manual_review += 1
                                continue
                            object_key = f"{shard_relative}/{name}"
                            if object_key not in known:
                                manual_review += 1
        except OSError:
            return manual_review + 1
        return manual_review

    def reconcile_incomplete(self) -> dict[str, int]:
        """Converge aborted rows without repeatedly flagging already-removed objects."""

        lease = self._output_lease(create=True)
        aborted = 0
        removed = 0
        dropped_rows = 0
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
            if did_abort:
                aborted += 1
            else:
                try:
                    already_aborted = self.store.transaction_state(
                        transaction_id, reservation_id, job_id
                    ) == "aborted"
                except TransactionStoreError:
                    already_aborted = False
                if not already_aborted:
                    manual_review += len(items)
                    continue

            for row in items:
                artifact_id = str(row.get("artifact_id") or "")
                object_key = row.get("object_key")
                if not isinstance(object_key, str):
                    manual_review += 1
                    continue
                safe_missing = False
                try:
                    _path, identity = self._validate_managed_row(lease, row)
                    if self.storage.unlink_if_identity(lease, object_key, identity):
                        removed += 1
                        safe_missing = True
                    else:
                        manual_review += 1
                        continue
                except (OutputAuthorityError, StorageAuthorityError, KeyError, TypeError, ValueError):
                    try:
                        candidate = self.storage.resolve_relative(lease, object_key, require_exists=False)
                        safe_missing = not candidate.exists()
                    except (StorageAuthorityError, OSError, ValueError):
                        safe_missing = False
                    if not safe_missing:
                        manual_review += 1
                        continue

                try:
                    dropped = self.store.drop_aborted_staged_artifact(
                        artifact_id=artifact_id,
                        transaction_id=transaction_id,
                        reservation_id=reservation_id,
                        job_id=job_id,
                    )
                except TransactionStoreError:
                    dropped = False
                if dropped:
                    dropped_rows += 1
                else:
                    manual_review += 1

        manual_review += self._count_untracked_managed_objects(lease)
        return {
            "aborted_transactions": aborted,
            "removed_objects": removed,
            "dropped_rows": dropped_rows,
            "manual_review": manual_review,
        }


__all__ = ["ProductionOutputAuthority"]
