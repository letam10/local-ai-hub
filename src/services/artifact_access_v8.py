"""Wave 2 compatibility surface for legacy Artifact Store callsites.

Existing uploads and historical artifacts stay readable from the V7 JSON store.
New job-output publication is routed exclusively through V8 reservation-bound
Output Authority. The installer mutates only process-local Python callables; it
never rewrites legacy metadata during import.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from src.services import artifact_store
from src.services.v8_output_bridge import default_bridge


_LEGACY = {
    "resolve": artifact_store.resolve,
    "describe": artifact_store.describe,
    "list_artifacts": artifact_store.list_artifacts,
    "open_artifact": artifact_store.open_artifact,
    "begin_job_output_scope": artifact_store.begin_job_output_scope,
    "prepare_job_output_scope": artifact_store.prepare_job_output_scope,
    "register_worker_outputs": artifact_store.register_worker_outputs,
    "finalize_job_output_scope": artifact_store.finalize_job_output_scope,
    "reconcile_job_output_scopes": artifact_store.reconcile_job_output_scopes,
    "atomic_write_job_output": artifact_store.atomic_write_job_output,
}
_INSTALLED = False


def resolve(artifact_id: str) -> Path | None:
    bridge = default_bridge(create=False)
    if bridge is not None:
        path = bridge.resolve(artifact_id)
        if path is not None:
            return path
    return _LEGACY["resolve"](artifact_id)


def describe(artifact_id: str) -> dict[str, Any] | None:
    bridge = default_bridge(create=False)
    if bridge is not None:
        value = bridge.describe(artifact_id)
        if value is not None:
            return value
    return _LEGACY["describe"](artifact_id)


def list_artifacts(*, limit: int = 240) -> list[dict[str, Any]]:
    bounded = max(1, min(500, int(limit)))
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    bridge = default_bridge(create=False)
    if bridge is not None:
        for item in bridge.list_public(limit=bounded):
            artifact_id = item.get("id")
            if isinstance(artifact_id, str) and artifact_id not in seen:
                seen.add(artifact_id)
                result.append(item)
    for item in _LEGACY["list_artifacts"](limit=bounded):
        artifact_id = item.get("id") if isinstance(item, dict) else None
        if isinstance(artifact_id, str) and artifact_id not in seen:
            seen.add(artifact_id)
            result.append(item)
        if len(result) >= bounded:
            break
    return result[:bounded]


def open_artifact(artifact_id: str) -> tuple[bool, str]:
    bridge = default_bridge(create=False)
    if bridge is not None:
        path = bridge.resolve(artifact_id)
        if path is not None:
            if os.name != "nt":
                return False, "Mở artifact chỉ được hỗ trợ từ bản Hub cho Windows."
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
            except OSError as exc:
                return False, str(exc)
            return True, "Đã yêu cầu Windows mở artifact Hub."
    return _LEGACY["open_artifact"](artifact_id)


def begin_job_output_scope(job_id: str) -> dict[str, Any] | None:
    """Retain V7 producer-ownership proof, then reserve V8 publication authority."""

    legacy = _LEGACY["begin_job_output_scope"](job_id)
    if legacy is None:
        return None
    bridge = default_bridge(create=True)
    reservation = bridge.reserve(job_id) if bridge is not None else None
    if reservation is None:
        try:
            _LEGACY["finalize_job_output_scope"](job_id, terminal_state="failed")
        except Exception:
            pass
        return None
    return {"status": "ready", "publication_authority": "v8"}


def register_worker_outputs(
    paths: list[str | Path],
    *,
    provenance: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """V7 call signature, V8 publication, with ownership proof at the boundary.

    A reservation proves only that the job may publish; it never proves that a
    returned filesystem candidate belongs to that job. Re-run the bounded V7
    scope claim here and require every candidate to be newly job-owned before
    handing anything to the V8 copy-once publisher.
    """

    job_id = provenance.get("job_id") if isinstance(provenance, dict) else None
    if not isinstance(job_id, str):
        return None
    values = list(paths)
    if not values:
        return None
    bridge = default_bridge(create=False)
    if bridge is None or bridge.open_reservation(job_id) is None:
        return None
    proof = _LEGACY["prepare_job_output_scope"](
        job_id,
        {"status": "completed", "outputs": values},
    )
    if not isinstance(proof, dict) or proof.get("status") != "owned":
        return None
    return bridge.publish_candidates(
        job_id=job_id,
        candidates=values,
        provenance=provenance,
    )


def finalize_job_output_scope(
    job_id: str,
    result: object = None,
    *,
    terminal_state: str,
    published: bool = False,
) -> dict[str, Any]:
    """Finalize V7 producer cleanup and the matching V8 reservation."""

    legacy = _LEGACY["finalize_job_output_scope"](
        job_id,
        result,
        terminal_state=terminal_state,
        published=published,
    )
    bridge = default_bridge(create=False)
    if bridge is None:
        return legacy
    if terminal_state == "completed" and published:
        bridge.close_no_output(job_id)
    elif terminal_state in {"failed", "cancelled", "unavailable", "interrupted"}:
        bridge.abort_job(job_id)
    return legacy


def reconcile_job_output_scopes(*, active_job_ids: set[str] | None = None) -> dict[str, int]:
    legacy = _LEGACY["reconcile_job_output_scopes"](active_job_ids=active_job_ids)
    bridge = default_bridge(create=False)
    if bridge is None:
        return legacy
    v8 = bridge.reconcile(active_job_ids=active_job_ids)
    return {
        **legacy,
        "v8_aborted_transactions": int(v8.get("aborted_transactions", 0)),
        "v8_removed_objects": int(v8.get("removed_objects", 0)),
        "v8_dropped_rows": int(v8.get("dropped_rows", 0)),
        "v8_manual_review": int(v8.get("manual_review", 0)),
        "v8_aborted_reservations": int(v8.get("aborted_reservations", 0)),
    }


def atomic_write_job_output(
    job_id: str,
    content: bytes,
    *,
    name: str = "result.bin",
    media_type: str | None = None,
    provenance: dict[str, Any],
    disk_safety_bytes: int = 0,
) -> dict[str, Any]:
    """Compatibility writer: reserve first, then produce/publish only through V8."""

    del disk_safety_bytes
    bridge = default_bridge(create=True)
    if bridge is None:
        raise artifact_store.ArtifactWriteError("V8 output authority is unavailable.")
    artifact = bridge.publish_bytes(
        job_id=job_id,
        content=content,
        name=name,
        media_type=media_type,
        provenance=provenance,
    )
    if artifact is None:
        bridge.abort_job(job_id)
        raise artifact_store.ArtifactWriteError("V8 output publication failed.")
    return artifact


def install_v8_artifact_compatibility() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    artifact_store.resolve = resolve
    artifact_store.describe = describe
    artifact_store.list_artifacts = list_artifacts
    artifact_store.open_artifact = open_artifact
    artifact_store.begin_job_output_scope = begin_job_output_scope
    artifact_store.register_worker_outputs = register_worker_outputs
    artifact_store.finalize_job_output_scope = finalize_job_output_scope
    artifact_store.reconcile_job_output_scopes = reconcile_job_output_scopes
    artifact_store.atomic_write_job_output = atomic_write_job_output
    _INSTALLED = True


def uninstall_v8_artifact_compatibility_for_tests() -> None:
    global _INSTALLED
    if not _INSTALLED:
        return
    for name, value in _LEGACY.items():
        setattr(artifact_store, name, value)
    _INSTALLED = False


__all__ = [
    "atomic_write_job_output",
    "begin_job_output_scope",
    "describe",
    "finalize_job_output_scope",
    "install_v8_artifact_compatibility",
    "list_artifacts",
    "open_artifact",
    "reconcile_job_output_scopes",
    "register_worker_outputs",
    "resolve",
    "uninstall_v8_artifact_compatibility_for_tests",
]
