"""Wave 2 extensions over the V8 transaction journal.

The Wave 0 schema stays unchanged. These helpers add production-migration
queries and exact cleanup operations without exposing workstation paths.
"""

from __future__ import annotations

from typing import Any

from src.services.transaction_store import V8TransactionStore


class V8ProductionTransactionStore(V8TransactionStore):
    """Transaction-store operations needed by production callsite migration."""

    def reservation_for_job(
        self,
        job_id: str,
        *,
        states: tuple[str, ...] = ("reserved",),
    ) -> dict[str, Any] | None:
        job_id = self._require_job_id(job_id)
        allowed = {"reserved", "authorized", "published", "aborted", "no_output"}
        requested = tuple(state for state in states if state in allowed)
        if not requested:
            return None
        placeholders = ",".join("?" for _ in requested)
        with self._read() as db:
            row = db.execute(
                f"""
                SELECT reservation_id, job_id, state, created_at, updated_at
                FROM output_reservations
                WHERE job_id=? AND state IN ({placeholders})
                ORDER BY created_at DESC, reservation_id DESC
                LIMIT 1
                """,
                (job_id, *requested),
            ).fetchone()
        return dict(row) if row is not None else None

    def unfinished_reservations(self, *, limit: int = 512) -> list[dict[str, Any]]:
        bounded = max(1, min(2048, int(limit)))
        with self._read() as db:
            rows = db.execute(
                """
                SELECT reservation_id, job_id, state, created_at, updated_at
                FROM output_reservations
                WHERE state IN ('reserved','authorized')
                ORDER BY created_at, reservation_id
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    def transaction_state(self, transaction_id: str, reservation_id: str, job_id: str) -> str | None:
        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        with self._read() as db:
            row = db.execute(
                """
                SELECT state FROM output_transactions
                WHERE transaction_id=? AND reservation_id=? AND job_id=?
                """,
                (transaction_id, reservation_id, job_id),
            ).fetchone()
        return str(row["state"]) if row is not None else None

    def drop_aborted_staged_artifact(
        self,
        *,
        artifact_id: str,
        transaction_id: str,
        reservation_id: str,
        job_id: str,
    ) -> bool:
        """Drop metadata only after the exact transaction is durably aborted."""

        if not isinstance(artifact_id, str) or self.public_artifact(artifact_id) is not None:
            return False
        transaction_id = self._require_transaction_id(transaction_id)
        reservation_id = self._require_reservation_id(reservation_id)
        job_id = self._require_job_id(job_id)
        with self._write() as db:
            tx = db.execute(
                """
                SELECT state FROM output_transactions
                WHERE transaction_id=? AND reservation_id=? AND job_id=?
                """,
                (transaction_id, reservation_id, job_id),
            ).fetchone()
            if tx is None or tx["state"] != "aborted":
                return False
            row = db.execute(
                """
                SELECT visibility FROM artifact_objects
                WHERE artifact_id=? AND transaction_id=? AND reservation_id=? AND job_id=?
                """,
                (artifact_id, transaction_id, reservation_id, job_id),
            ).fetchone()
            if row is None:
                return True
            if row["visibility"] != "staged":
                return False
            db.execute(
                """
                DELETE FROM artifact_objects
                WHERE artifact_id=? AND transaction_id=? AND reservation_id=? AND job_id=?
                  AND visibility='staged'
                """,
                (artifact_id, transaction_id, reservation_id, job_id),
            )
        return True

    def component_operation_for_plan(self, plan_id: str) -> dict[str, Any] | None:
        if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 120:
            return None
        with self._read() as db:
            row = db.execute(
                """
                SELECT operation_id FROM component_operations
                WHERE plan_id=?
                ORDER BY created_at DESC, operation_id DESC
                LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
        return self.component_operation(str(row["operation_id"])) if row is not None else None


__all__ = ["V8ProductionTransactionStore"]
