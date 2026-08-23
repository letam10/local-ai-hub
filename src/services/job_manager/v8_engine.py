"""Wave 2 production DurableWorkEngine using V8 output publication authority."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.services.job_manager.contracts import JobContractError, is_artifact_id, validate_job_spec
from src.services.job_manager.durable import (
    DurableStoreHealthError,
    DurableWorkEngine as LegacyDurableWorkEngine,
    _managed_output_context,
)
from src.services.v8_output_bridge import V8OutputBridge, default_bridge


_JOB_ID = re.compile(r"^jobv5_[a-f0-9]{32}$")


class V8DurableWorkEngine(LegacyDurableWorkEngine):
    """Retain V7 durable job state while moving output publication to V8."""

    def __init__(self, *args: Any, output_bridge: V8OutputBridge | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._output_bridge = output_bridge

    def _bridge(self, *, create: bool) -> V8OutputBridge | None:
        if self._output_bridge is not None:
            return self._output_bridge
        self._output_bridge = default_bridge(create=create)
        return self._output_bridge

    def run(self, job_id: str) -> dict[str, Any] | None:
        """Reserve output authority before any trusted adapter can execute."""

        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            return super().run(job_id)
        bridge = self._bridge(create=True)
        try:
            record = self.store.get(job_id)
        except DurableStoreHealthError:
            return super().run(job_id)
        if record is not None and record.get("status") == "queued":
            if bridge is None or bridge.reserve(job_id) is None:
                with self._lock:
                    self._transition_locked(
                        job_id,
                        "unavailable",
                        reason_code="ADAPTER_UNAVAILABLE",
                        action_code="CHECK_SERVER_ADAPTER",
                        retry_available=False,
                    )
                return self.get(job_id)

        result = super().run(job_id)
        if bridge is not None:
            try:
                current = self.store.get(job_id)
            except DurableStoreHealthError:
                current = None
            if isinstance(current, dict) and current.get("status") in {"failed", "unavailable", "interrupted"}:
                bridge.abort_job(job_id)
        return result

    def cancel(self, job_id: str) -> tuple[bool, dict[str, Any] | None]:
        ok, result = super().cancel(job_id)
        bridge = self._bridge(create=False)
        if bridge is not None:
            try:
                current = self.store.get(job_id)
            except DurableStoreHealthError:
                current = None
            if isinstance(current, dict) and current.get("status") in {"failed", "unavailable", "interrupted"}:
                bridge.abort_job(job_id)
        return ok, result

    def _record_artifact(self, job_id: str, artifact: dict[str, Any]) -> dict[str, Any] | None:
        artifact_id = artifact.get("id")
        if not is_artifact_id(artifact_id):
            return None
        with self._lock:
            current = self.store.get(job_id)
            if current is None or current.get("status") != "completed":
                return None
            existing = current.get("artifacts") if isinstance(current.get("artifacts"), list) else []
            artifact_ids = [item for item in existing if is_artifact_id(item)]
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            self._update_locked(job_id, {"artifacts": artifact_ids})
        return dict(artifact)

    def persist_managed_output(self, job_id: str, output_path: object) -> dict[str, Any] | None:
        """Publish an adapter-created producer only when its pre-run reservation exists."""

        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None or not isinstance(output_path, Path):
            return None
        bridge = self._bridge(create=False)
        if bridge is None or bridge.open_reservation(job_id) is None:
            return None
        try:
            record = self.store.get(job_id)
            context = _managed_output_context(record)
        except (DurableStoreHealthError, JobContractError):
            return None
        if context is None:
            return None
        provenance, source_path = context
        try:
            if output_path.is_symlink():
                return None
            candidate = output_path.resolve()
            if candidate == source_path:
                return None
            candidate.relative_to(bridge.authority.paths.output_root.resolve())
            if not candidate.is_file() or not candidate.name.startswith(f"hub-job-{job_id[-8:]}-"):
                return None
        except (OSError, ValueError):
            return None
        published = bridge.publish_candidates(
            job_id=job_id,
            candidates=[candidate],
            provenance=provenance,
        )
        if not isinstance(published, list) or len(published) != 1:
            bridge.abort_job(job_id)
            return None
        return self._record_artifact(job_id, published[0])

    def persist_output(
        self,
        job_id: str,
        content: bytes,
        *,
        name: str = "result.bin",
        media_type: str | None = None,
        disk_safety_bytes: int = 0,
    ) -> dict[str, Any] | None:
        """Publish Hub-produced bytes through reservation-bound V8 authority."""

        del disk_safety_bytes
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None or not isinstance(content, bytes):
            return None
        try:
            record = self.store.get(job_id)
        except DurableStoreHealthError:
            return None
        if record is None or record.get("status") != "completed":
            return None
        try:
            spec = validate_job_spec(record.get("job_spec"))
        except JobContractError:
            return None
        if spec.descriptor.adapter_id == "media.video_grade.v1":
            return None
        provenance = {
            "job_id": job_id,
            "job_spec_fingerprint": record.get("job_spec_fingerprint"),
            "adapter_id": (record.get("descriptor_summary") or {}).get("adapter_id"),
            "attempt": record.get("attempt"),
            "status": "completed",
        }
        bridge = self._bridge(create=True)
        if bridge is None:
            return None
        artifact = bridge.publish_bytes(
            job_id=job_id,
            content=content,
            name=name,
            media_type=media_type,
            provenance=provenance,
        )
        return self._record_artifact(job_id, artifact) if isinstance(artifact, dict) else None

    def reconcile_startup(self) -> dict[str, int]:
        result = super().reconcile_startup()
        bridge = self._bridge(create=False)
        if bridge is None:
            return result
        v8 = bridge.reconcile(active_job_ids=set())
        return {
            **result,
            "v8_aborted_transactions": int(v8.get("aborted_transactions", 0)),
            "v8_removed_objects": int(v8.get("removed_objects", 0)),
            "v8_dropped_rows": int(v8.get("dropped_rows", 0)),
            "v8_manual_review": int(v8.get("manual_review", 0)),
            "v8_aborted_reservations": int(v8.get("aborted_reservations", 0)),
        }


__all__ = ["V8DurableWorkEngine"]
