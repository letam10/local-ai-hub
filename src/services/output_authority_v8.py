"""Production-migration Output Authority for V8 Wave 2."""

from __future__ import annotations

from typing import Any

from src.platform.paths import HubPaths, get_paths
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

        return {
            "aborted_transactions": aborted,
            "removed_objects": removed,
            "dropped_rows": dropped_rows,
            "manual_review": manual_review,
        }


__all__ = ["ProductionOutputAuthority"]
