"""Compose manifest, runtime, model and evidence state without inference."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _status(runtime: str, model: str, evidence: Mapping[str, Any] | None) -> tuple[str, str, str]:
    if runtime in {"NOT_INSTALLED", "UNAVAILABLE", "BROKEN"}:
        return "NOT_INSTALLED", "The required runtime is unavailable.", "Install or repair the runtime through an explicit plan."
    if model in {"NOT_INSTALLED", "UNAVAILABLE", "BROKEN"}:
        return "NOT_INSTALLED", "The required model is not installed.", "Review a model installation plan; no download runs at startup."
    if runtime == "PARTIAL" or model == "PARTIAL":
        return "PARTIAL", "Runtime or model evidence is incomplete.", "Inspect the missing leaves before requesting verification."
    if isinstance(evidence, Mapping) and evidence.get("status") == "passed" and evidence.get("runtime_fingerprint"):
        return "OPERATIONAL", "A bounded smoke receipt matches the current runtime fingerprint.", "Keep the receipt fresh and rerun bounded verification after changes."
    return "INSTALLED_UNVERIFIED", "Runtime and model leaves are present without a matching bounded smoke receipt.", "Run a separately authorized bounded smoke before claiming operational status."


def compose_module_status(manifest: Mapping[str, Any], *, runtime_status: Mapping[str, Any], model_statuses: list[Mapping[str, Any]], evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    runtime = str(runtime_status.get("status", "UNAVAILABLE"))
    model = "INSTALLED_UNVERIFIED" if not model_statuses or all(item.get("status") == "INSTALLED_UNVERIFIED" for item in model_statuses) else str(model_statuses[0].get("status", "NOT_INSTALLED"))
    status, reason, action = _status(runtime, model, evidence)
    return {
        "module_id": manifest.get("id"),
        "display_name": manifest.get("display_name"),
        "status": status,
        "runtime_status": runtime,
        "model_status": model,
        "execution": "not_run" if status != "OPERATIONAL" else "bounded_smoke",
        "reason": reason,
        "next_action": action,
        "runtime_id": manifest.get("runtime", {}).get("required") if isinstance(manifest.get("runtime"), Mapping) else None,
        "model_ids": [item.get("model_id") for item in model_statuses if isinstance(item.get("model_id"), str)],
    }


def compose_registry(manifests: Mapping[str, Mapping[str, Any]], *, runtime_statuses: Mapping[str, Mapping[str, Any]], model_statuses: Mapping[str, Mapping[str, Any]], evidence: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    evidence = evidence or {}
    for identifier in sorted(manifests):
        manifest = manifests[identifier]
        runtime_id = manifest.get("runtime", {}).get("required") if isinstance(manifest.get("runtime"), Mapping) else None
        runtime = runtime_statuses.get(runtime_id, {"status": "NOT_INSTALLED"})
        model_ids = manifest.get("models", {}).get("required", []) if isinstance(manifest.get("models"), Mapping) else []
        models = [model_statuses.get(model_id, {"model_id": model_id, "status": "NOT_INSTALLED"}) for model_id in model_ids]
        records.append(compose_module_status(manifest, runtime_status=runtime, model_statuses=models, evidence=evidence.get(identifier)))
    overall = "OPERATIONAL" if records and all(item["status"] == "OPERATIONAL" for item in records) else ("PARTIAL" if any(item["status"] in {"PARTIAL", "INSTALLED_UNVERIFIED"} for item in records) else "NOT_INSTALLED")
    return {"schema_version": "module-composition.v1", "status": overall, "execution": "not_run", "dry_run": True, "records": records, "reason": "Module state is composed from manifests, runtime/model observations and optional bounded evidence; no inference ran."}


__all__ = ["compose_module_status", "compose_registry"]
