"""Milestone 2 project, artifact, and media protocol projections.

The existing V7 project and artifact stores retain persistence ownership.  These
facades provide a V2 vocabulary over their public records only. Artifact and
media projections never mutate metadata, create a thumbnail, or start a media
operation. Project Workspace V2 may explicitly attach an existing opaque
workflow/job reference through the existing project owner; it never copies a
graph, artifact, or path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
from typing import Any


PROJECT_WORKSPACE_V2_SCHEMA_VERSION = "project-workspace.v2"
ARTIFACT_LIBRARY_V2_SCHEMA_VERSION = "artifact-library.v2"
MEDIA_PIPELINE_V2_SCHEMA_VERSION = "media-pipeline.v2"
_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")
_PROJECT_ID = re.compile(r"^project_[a-f0-9]{32}$")
_UNSAFE_KEY = re.compile(r"(?:path|secret|token|credential|password|command|executable|url)", re.IGNORECASE)


def _safe_text(value: object, *, maximum: int = 320) -> str | None:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        return None
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\)", value):
        return None
    return value


def _safe_tags(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:16]:
        safe = _safe_text(item, maximum=64)
        if safe is not None and safe not in result:
            result.append(safe)
    return result


def _safe_size(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**63 - 1 else None


def _safe_artifact(record: object) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    artifact_id = record.get("id") or record.get("artifact_id")
    if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
        return None
    item: dict[str, Any] = {"artifact_id": artifact_id}
    for source, target, maximum in (("name", "name", 180), ("media_type", "media_type", 120), ("created_at", "created_at", 80)):
        safe = _safe_text(record.get(source), maximum=maximum)
        if safe is not None:
            item[target] = safe
    size = _safe_size(record.get("size_bytes"))
    if size is not None:
        item["size_bytes"] = size
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        safe_provenance: dict[str, str] = {}
        for key in ("job_id", "workflow_id", "node_id", "source_artifact_id", "parent_artifact_id"):
            value = _safe_text(provenance.get(key), maximum=120)
            if value is not None:
                safe_provenance[key] = value
        if safe_provenance:
            item["lineage"] = safe_provenance
    return item


class ProjectWorkspaceV2:
    """Project projection plus explicit opaque workflow/job attachment."""

    def __init__(
        self,
        project_manager: Any,
        workflow_library_store: Any | None = None,
        durable_job_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._projects = project_manager
        self._library = workflow_library_store
        self._durable_job_lookup = durable_job_lookup or (lambda _job_id: None)

    def snapshot(self) -> dict[str, Any]:
        try:
            raw = self._projects.list_projects()
        except Exception:
            raw = {}
        projects = raw.get("projects") if isinstance(raw, Mapping) else []
        values = projects if isinstance(projects, list) else []
        rows = [row for row in (self._project_summary(item) for item in values) if row is not None]
        library = self._workflow_library_snapshot()
        workflows = library["workflows"]
        recent_workflows = library["recent_workflows"]
        templates = self._template_summary()
        return {
            "schema_version": PROJECT_WORKSPACE_V2_SCHEMA_VERSION,
            "status": "completed",
            "projects": rows,
            "workflow_library": workflows,
            "recent_workflows": recent_workflows,
            "templates": templates,
            "counts": {"projects": len(rows), "workflows": len(workflows), "recent_workflows": len(recent_workflows), "favorites": sum(1 for item in workflows if item.get("favorite") is True), "templates": len(templates)},
            "execution": "not_run",
            "dry_run": True,
            "reason": "Projects and workflows are projected from their existing server-owned metadata stores only.",
            "next_action": "Use Workflow Runtime V2 preflight before a future job is attached to a project.",
        }

    def detail(self, project_id: str) -> dict[str, Any] | None:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            return None
        try:
            raw = self._projects.get_project(project_id)
        except Exception:
            return None
        if not isinstance(raw, Mapping):
            return None
        project = raw.get("project", raw)
        if isinstance(project, Mapping) and isinstance(raw.get("workflow_ids"), list):
            project = {**project, "workflow_ids": raw["workflow_ids"], "job_ids": raw.get("job_ids") if isinstance(raw.get("job_ids"), list) else []}
        result = self._project_summary(project)
        if result is None:
            return None
        assets = raw.get("assets") if isinstance(raw, Mapping) and isinstance(raw.get("assets"), list) else []
        safe_assets = [item for item in (_safe_artifact(value) for value in assets if isinstance(assets, list)) if item is not None]
        result["assets"] = safe_assets
        result["artifact_count"] = len(safe_assets) if safe_assets else result["artifact_count"]
        result["execution"] = "not_run"
        result["dry_run"] = True
        return result

    def attach_workflow(self, project_id: str, workflow_id: str) -> dict[str, Any]:
        """Attach one currently available library workflow by opaque ID."""

        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            return self._invalid_attachment("project_workspace_v2_project_invalid")
        if not isinstance(workflow_id, str) or not re.fullmatch(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,11}$", workflow_id):
            return self._invalid_attachment("project_workspace_v2_workflow_invalid")
        if self._library is None:
            return self._unavailable_attachment("workflow_library_unavailable")
        try:
            lookup = self._library.get_workflow(workflow_id)
        except Exception:
            return self._unavailable_attachment("workflow_library_unavailable")
        if not isinstance(lookup, Mapping) or lookup.get("status") != "ready":
            return self._invalid_attachment("project_workspace_v2_workflow_not_found")
        try:
            value = self._projects.attach_workflow(project_id, workflow_id)
        except KeyError:
            return self._invalid_attachment("project_workspace_v2_project_not_found")
        except (TypeError, ValueError):
            return self._invalid_attachment("project_workspace_v2_attachment_invalid")
        return self._attachment_result(value, reference_key="workflow_id", reference=workflow_id)

    def attach_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        """Attach only an existing V2 durable job metadata record."""

        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            return self._invalid_attachment("project_workspace_v2_project_invalid")
        if not isinstance(job_id, str) or not re.fullmatch(r"^jobv2_[a-f0-9]{32}$", job_id):
            return self._invalid_attachment("project_workspace_v2_job_invalid")
        try:
            job = self._durable_job_lookup(job_id)
        except Exception:
            return self._unavailable_attachment("durable_job_lookup_unavailable")
        if not isinstance(job, Mapping):
            return self._invalid_attachment("project_workspace_v2_job_not_found")
        try:
            value = self._projects.attach_job(project_id, job_id)
        except KeyError:
            return self._invalid_attachment("project_workspace_v2_project_not_found")
        except (TypeError, ValueError):
            return self._invalid_attachment("project_workspace_v2_attachment_invalid")
        return self._attachment_result(value, reference_key="job_id", reference=job_id)

    def export_manifest(self, project_id: str) -> dict[str, Any] | None:
        """Return a bounded, path-free project/workflow export projection.

        This does not create an archive or write a file.  It lets a future
        explicit export authority serialize the exact reviewed metadata.
        """

        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            return None
        try:
            source = self._projects.export_project(project_id)
        except (KeyError, TypeError, ValueError):
            return None
        manifest = source.get("manifest") if isinstance(source, Mapping) else None
        if not isinstance(manifest, Mapping):
            return None
        project = manifest.get("project")
        summary = self._project_summary(project)
        if summary is None:
            return None
        workflow_ids = project.get("workflow_ids") if isinstance(project, Mapping) and isinstance(project.get("workflow_ids"), list) else []
        workflows = [item for item in (self._workflow_detail(item) for item in workflow_ids) if item is not None]
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), list) else []
        safe_assets: list[dict[str, Any]] = []
        for item in assets[:240]:
            if not isinstance(item, Mapping):
                continue
            artifact_id = item.get("artifact_id")
            if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
                continue
            safe_assets.append({
                "artifact_id": artifact_id,
                "tags": _safe_tags(item.get("tags")),
                "favorite": item.get("favorite") is True,
                "parent_artifact_id": item.get("parent_artifact_id") if isinstance(item.get("parent_artifact_id"), str) and _ARTIFACT_ID.fullmatch(item["parent_artifact_id"]) else None,
                "recipe_id": _safe_text(item.get("recipe_id"), maximum=100),
            })
        return {
            "schema_version": "project-workspace-export.v2",
            "status": "completed",
            "project": summary,
            "workflows": workflows,
            "assets": safe_assets,
            "job_ids": [item for item in (project.get("job_ids", []) if isinstance(project, Mapping) else []) if isinstance(item, str) and re.fullmatch(r"^jobv2_[a-f0-9]{32}$", item)],
            "execution": "not_run",
            "dry_run": True,
            "reason": "This is a metadata-only export projection; it contains no file bytes, paths, model weights or secrets.",
            "next_action": "Use an explicit future export authority to write a user-selected package after review.",
        }

    @staticmethod
    def _invalid_attachment(code: str) -> dict[str, Any]:
        return {"status": "invalid", "error": code, "execution": "not_run", "dry_run": True}

    @staticmethod
    def _unavailable_attachment(code: str) -> dict[str, Any]:
        return {"status": "unavailable", "error": code, "execution": "not_run", "dry_run": True}

    @staticmethod
    def _attachment_result(value: object, *, reference_key: str, reference: str) -> dict[str, Any]:
        project = value.get("project") if isinstance(value, Mapping) else None
        return {
            "status": "completed" if isinstance(project, Mapping) else "unavailable",
            reference_key: reference,
            "project": project,
            "execution": "not_run",
            "dry_run": True,
            "reason": "Only an existing opaque reference was attached; no workflow/job execution or artifact deletion occurred.",
            "next_action": "Inspect the project and its attached references before a future dispatch.",
        }

    @staticmethod
    def _project_summary(value: object) -> dict[str, Any] | None:
        if not isinstance(value, Mapping):
            return None
        project_id = value.get("id")
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            return None
        title = _safe_text(value.get("title"), maximum=120)
        status = value.get("status")
        asset_ids = value.get("asset_ids") if isinstance(value.get("asset_ids"), list) else value.get("artifact_ids") if isinstance(value.get("artifact_ids"), list) else []
        recipe_ids = value.get("recipe_ids") if isinstance(value.get("recipe_ids"), list) else []
        workflow_ids = value.get("workflow_ids") if isinstance(value.get("workflow_ids"), list) else []
        job_ids = value.get("job_ids") if isinstance(value.get("job_ids"), list) else []
        workflow_preset = _safe_text(value.get("workflow_preset"), maximum=100)
        workflow_count = sum(1 for item in workflow_ids if isinstance(item, str) and len(item) <= 100)
        job_count = sum(1 for item in job_ids if isinstance(item, str) and len(item) <= 100)
        if not workflow_ids and isinstance(value.get("workflow_count"), int) and value["workflow_count"] >= 0:
            workflow_count = min(256, value["workflow_count"])
        if not job_ids and isinstance(value.get("job_count"), int) and value["job_count"] >= 0:
            job_count = min(512, value["job_count"])
        return {
            "project_id": project_id,
            "title": title or "Untitled project",
            "status": status if status in {"active", "archived"} else "active",
            "tags": _safe_tags(value.get("tags")),
            "artifact_count": sum(1 for item in asset_ids if isinstance(item, str) and _ARTIFACT_ID.fullmatch(item)),
            "recipe_count": sum(1 for item in recipe_ids if isinstance(item, str) and len(item) <= 80),
            "workflow_count": workflow_count,
            "job_count": job_count,
            "workflow_preset": workflow_preset,
        }

    def _workflow_library_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        if self._library is None:
            return {"workflows": [], "recent_workflows": []}
        try:
            raw = self._library.list_workflows()
        except Exception:
            return {"workflows": [], "recent_workflows": []}
        values = raw.get("workflows") if isinstance(raw, Mapping) else []
        recent_values = raw.get("recent_workflows") if isinstance(raw, Mapping) else []
        return {
            "workflows": self._workflow_rows(values),
            "recent_workflows": self._workflow_rows(recent_values),
        }

    @staticmethod
    def _workflow_rows(values: object) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(values, list):
            return result
        for item in values[:240]:
            if not isinstance(item, Mapping):
                continue
            workflow_id = _safe_text(item.get("id"), maximum=100)
            if workflow_id is None:
                continue
            result.append({
                "workflow_id": workflow_id,
                "title": _safe_text(item.get("title"), maximum=160) or "Untitled workflow",
                "scope": _safe_text(item.get("scope"), maximum=64) or "all",
                "revision": item.get("revision") if isinstance(item.get("revision"), int) and item["revision"] >= 1 else 1,
                "status": _safe_text(item.get("status"), maximum=48) or "draft",
                "source": _safe_text(item.get("source"), maximum=32) or "local",
                "kind": "template" if item.get("source") == "preset" else "user_workflow",
                "favorite": item.get("favorite") is True,
                "last_opened_at": _safe_text(item.get("last_opened_at"), maximum=80) or None,
            })
        return result

    def _template_summary(self) -> list[dict[str, Any]]:
        gallery = getattr(self._projects, "workflow_gallery", None)
        if not callable(gallery):
            return []
        try:
            raw = gallery()
        except Exception:
            return []
        values = raw.get("gallery") if isinstance(raw, Mapping) else []
        if not isinstance(values, list):
            return []
        result: list[dict[str, Any]] = []
        for item in values[:120]:
            if not isinstance(item, Mapping):
                continue
            template_id = _safe_text(item.get("id"), maximum=100)
            if template_id is None or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", template_id):
                continue
            result.append({
                "template_id": template_id,
                "title": _safe_text(item.get("title"), maximum=160) or template_id,
                "scope": _safe_text(item.get("scope"), maximum=64) or "all",
                "status": _safe_text(item.get("status"), maximum=48) or "partial",
                "node_count": item.get("node_count") if isinstance(item.get("node_count"), int) and 0 <= item["node_count"] <= 384 else 0,
            })
        return result

    def _workflow_detail(self, workflow_id: object) -> dict[str, Any] | None:
        if self._library is None or not isinstance(workflow_id, str):
            return None
        try:
            value = self._library.get_workflow(workflow_id)
        except Exception:
            return None
        workflow = value.get("workflow") if isinstance(value, Mapping) else None
        if not isinstance(workflow, Mapping):
            return None
        workflow_id = _safe_text(workflow.get("id"), maximum=100)
        graph = workflow.get("graph")
        if workflow_id is None or not isinstance(graph, Mapping):
            return None
        # WorkflowLibraryStore already validates the graph. Retain only the
        # declarative typed graph keys in a fresh detached object here.
        safe_graph: dict[str, Any] = {}
        for key in ("schema_version", "id", "title", "scope", "nodes", "edges", "groups"):
            if key in graph:
                safe_graph[key] = graph[key]
        encoded = json.dumps(safe_graph, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 512 * 1024:
            return None
        return {
            "workflow_id": workflow_id,
            "title": _safe_text(workflow.get("title"), maximum=160) or "Untitled workflow",
            "revision": workflow.get("revision") if isinstance(workflow.get("revision"), int) and workflow["revision"] >= 1 else 1,
            "status": _safe_text(workflow.get("status"), maximum=48) or "draft",
            "graph": safe_graph,
        }


class ArtifactLibraryV2:
    """Visual-library metadata projection without file or preview execution."""

    def __init__(
        self,
        *,
        artifact_lister: Callable[..., list[dict[str, Any]]],
        artifact_describer: Callable[[str], Mapping[str, Any] | None],
        project_manager: Any | None = None,
    ) -> None:
        self._list = artifact_lister
        self._describe = artifact_describer
        self._projects = project_manager

    def snapshot(self, *, limit: int = 120) -> dict[str, Any]:
        bounded = max(1, min(240, int(limit)))
        try:
            raw = self._list(limit=bounded)
        except Exception:
            raw = []
        values = raw if isinstance(raw, list) else []
        rows = [row for row in (_safe_artifact(item) for item in values) if row is not None]
        for row in rows:
            self._merge_workspace_metadata(row)
            row["preview"] = {
                "status": "available" if row.get("media_type") else "metadata_only",
                "url": f"/api/artifacts/{row['artifact_id']}",
            }
            row["thumbnail"] = {
                "status": "unavailable",
                "reason": "Thumbnail generation has no server-owned background worker in this milestone increment.",
            }
        return {
            "schema_version": ARTIFACT_LIBRARY_V2_SCHEMA_VERSION,
            "status": "completed",
            "artifacts": rows,
            "counts": {"artifacts": len(rows)},
            "execution": "not_run",
            "dry_run": True,
            "reason": "Artifact Library V2 exposes existing opaque artifact metadata and bounded loopback previews only.",
            "next_action": "Review an artifact's lineage or open its existing bounded preview; no thumbnail or export task is started here.",
        }

    def detail(self, artifact_id: str) -> dict[str, Any] | None:
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
            return None
        try:
            raw = self._describe(artifact_id)
        except Exception:
            return None
        row = _safe_artifact(raw)
        if row is None:
            return None
        self._merge_workspace_metadata(row)
        row["preview"] = {"status": "available" if row.get("media_type") else "metadata_only", "url": f"/api/artifacts/{artifact_id}"}
        row["thumbnail"] = {"status": "unavailable", "reason": "No thumbnail job has been created."}
        row["execution"] = "not_run"
        row["dry_run"] = True
        return row

    def _merge_workspace_metadata(self, row: dict[str, Any]) -> None:
        if self._projects is None:
            return
        try:
            value = self._projects.get_artifact_status(row["artifact_id"])
        except Exception:
            return
        if not isinstance(value, Mapping):
            return
        row["tags"] = _safe_tags(value.get("tags"))
        project_ids = value.get("project_ids")
        if isinstance(project_ids, list):
            row["project_ids"] = [item for item in project_ids[:32] if isinstance(item, str) and _PROJECT_ID.fullmatch(item)]
        row["favorite"] = value.get("favorite") is True


class MediaPipelineV2:
    """Closed media-plan contract; actual media execution remains elsewhere."""

    _OPERATIONS = frozenset({"probe", "trim", "concat", "encode", "audio_extract", "subtitle", "overlay", "resize", "fps", "transcode", "video_upscale", "frame_interpolation", "preview", "save", "export"})
    _BACKENDS = frozenset({"ffmpeg", "ffmpeg_scale", "animesr", "ffmpeg_minterpolate"})
    _VIDEO_OPERATIONS = frozenset({"trim", "concat", "encode", "subtitle", "overlay", "resize", "fps", "transcode", "video_upscale", "frame_interpolation", "preview", "save", "export"})
    _OPTIONS: dict[str, dict[str, tuple[str, object, object]]] = {
        "trim": {"start_seconds": ("number", 0, 86_400), "end_seconds": ("number", 0, 86_400)},
        "resize": {"width": ("dimension", 2, 8192), "height": ("dimension", -2, 8192)},
        "fps": {"fps": ("number", 1, 240)},
        "encode": {"container": ("enum", ("mp4", "mkv", "webm"), None), "video_codec": ("enum", ("h264", "hevc", "vp9", "av1"), None)},
        "transcode": {"container": ("enum", ("mp4", "mkv", "webm"), None)},
        "audio_extract": {"container": ("enum", ("wav", "mp3", "m4a", "flac"), None)},
        "video_upscale": {"scale": ("integer", 2, 4)},
        "frame_interpolation": {"fps": ("number", 1, 240)},
    }

    def __init__(self, artifact_describer: Callable[[str], Mapping[str, Any] | None]) -> None:
        self._describe = artifact_describer

    def contract(self) -> dict[str, Any]:
        stages = [
            {"stage": "probe", "status": "partial", "execution": "not_run"},
            {"stage": "transform", "status": "partial", "execution": "not_run"},
            {"stage": "upscale", "status": "partial", "fallback": {"backend": "ffmpeg_scale", "ai_upscaler": False}},
            {"stage": "frame_interpolation", "status": "partial", "fallback": {"backend": "ffmpeg_minterpolate", "ai_interpolator": False}},
            {"stage": "encode", "status": "partial", "execution": "not_run"},
            {"stage": "preview", "status": "read_only", "execution": "not_run"},
            {"stage": "save", "status": "owner_required", "execution": "not_run"},
            {"stage": "export", "status": "owner_required", "execution": "not_run"},
        ]
        return {
            "schema_version": MEDIA_PIPELINE_V2_SCHEMA_VERSION,
            "status": "partial",
            "stages": stages,
            "allowed_operations": sorted(self._OPERATIONS),
            "execution": "not_run",
            "dry_run": True,
            "reason": "Media Pipeline V2 publishes an allowlisted preflight only; no FFmpeg, AnimeSR, RIFE, provider, or GPU job is started.",
            "next_action": "Use a future server-owned media execution owner after capability and artifact evidence are present.",
        }

    def preflight(self, payload: object) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or set(payload) - {"artifact_id", "input_artifact_ids", "operation", "backend", "options"}:
            return self._invalid("media_pipeline_payload_invalid")
        artifact_id = payload.get("artifact_id")
        operation = payload.get("operation")
        backend = payload.get("backend")
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
            return self._invalid("media_pipeline_artifact_invalid")
        if not isinstance(operation, str) or operation not in self._OPERATIONS:
            return self._invalid("media_pipeline_operation_invalid")
        if backend is not None and (not isinstance(backend, str) or backend not in self._BACKENDS):
            return self._invalid("media_pipeline_backend_invalid")
        options = self._options(operation, payload.get("options"))
        if options is None:
            return self._invalid("media_pipeline_options_invalid")
        additional = self._additional_artifacts(payload.get("input_artifact_ids"), operation)
        if additional is None:
            return self._invalid("media_pipeline_inputs_invalid")
        try:
            record = self._describe(artifact_id)
        except Exception:
            record = None
        artifact = _safe_artifact(record)
        if artifact is None:
            return {
                **self._invalid("media_pipeline_artifact_not_found"),
                "artifact_id": artifact_id,
                "operation": operation,
                "next_action": "Choose an existing opaque Hub artifact before planning a media operation.",
            }
        media_type = str(artifact.get("media_type") or "")
        if operation in self._VIDEO_OPERATIONS and not media_type.casefold().startswith("video/"):
            return {
                **self._invalid("media_pipeline_media_type_invalid"),
                "artifact_id": artifact_id,
                "operation": operation,
                "next_action": "Choose a VIDEO artifact for this pipeline operation.",
            }
        input_artifacts = [artifact]
        for item in additional:
            try:
                dependent = _safe_artifact(self._describe(item))
            except Exception:
                dependent = None
            if dependent is None:
                return {
                    **self._invalid("media_pipeline_artifact_not_found"),
                    "artifact_id": item,
                    "operation": operation,
                    "next_action": "Choose only existing opaque Hub artifacts for a multi-input media plan.",
                }
            input_artifacts.append(dependent)
        selected_backend = backend or ("ffmpeg_scale" if operation == "video_upscale" else "ffmpeg_minterpolate" if operation == "frame_interpolation" else "ffmpeg")
        unavailable = operation == "video_upscale" and selected_backend == "animesr"
        return {
            "schema_version": MEDIA_PIPELINE_V2_SCHEMA_VERSION,
            "status": "unavailable" if unavailable else "partial",
            "artifacts": [{"artifact_id": item["artifact_id"], "media_type": item.get("media_type")} for item in input_artifacts],
            "operation": operation,
            "backend": selected_backend,
            "options": options,
            "execution_descriptor": {
                "adapter_id": "media_pipeline.v2.animesr" if selected_backend == "animesr" else "media_pipeline.v2.ffmpeg",
                "operation": operation,
                "input_identity": "opaque_artifact_ids_only",
                "output_contract": self._output_contract(operation, media_type),
                "progress_contract": "server_owned_job_progress_only",
                "cancellation_contract": "server_owned_job_cancellation_only",
            },
            "metadata": {"ai_upscaler": False} if selected_backend == "ffmpeg_scale" else {"ai_interpolator": False} if selected_backend == "ffmpeg_minterpolate" else {},
            "execution": "not_run",
            "dry_run": True,
            "reason": "AnimeSR dispatch is unavailable until it has a separately verified server-owned execution owner." if unavailable else "This is a closed media preflight only; it did not invoke a media process.",
            "next_action": "Review the existing media capability and submit only through a future server-owned execution owner.",
        }

    @staticmethod
    def _invalid(code: str) -> dict[str, Any]:
        return {"schema_version": MEDIA_PIPELINE_V2_SCHEMA_VERSION, "status": "invalid", "error": code, "execution": "not_run", "dry_run": True}

    @classmethod
    def _options(cls, operation: object, value: object) -> dict[str, Any] | None:
        if not isinstance(operation, str) or operation not in cls._OPERATIONS:
            return None
        source = {} if value is None else value
        if not isinstance(source, Mapping):
            return None
        schema = cls._OPTIONS.get(operation, {})
        if set(source) - set(schema):
            return None
        normalized: dict[str, Any] = {}
        for key, spec in schema.items():
            if key not in source:
                continue
            kind, minimum, maximum = spec
            raw = source[key]
            if kind == "enum":
                if not isinstance(raw, str) or raw not in minimum:
                    return None
                normalized[key] = raw
                continue
            if kind == "integer":
                if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum or raw > maximum:
                    return None
                normalized[key] = raw
                continue
            if kind == "dimension":
                if not isinstance(raw, int) or isinstance(raw, bool) or raw < minimum or raw > maximum:
                    return None
                if raw not in {-2, -1} and raw % 2:
                    return None
                normalized[key] = raw
                continue
            if kind == "number":
                if not isinstance(raw, (int, float)) or isinstance(raw, bool) or not float(minimum) <= float(raw) <= float(maximum):
                    return None
                normalized[key] = float(raw)
        if operation == "trim" and normalized.get("end_seconds") is not None and normalized.get("start_seconds") is not None and normalized["end_seconds"] <= normalized["start_seconds"]:
            return None
        return normalized

    @staticmethod
    def _additional_artifacts(value: object, operation: str) -> list[str] | None:
        if value is None:
            return None if operation == "concat" else []
        if not isinstance(value, list) or len(value) > 16:
            return None
        values = [item for item in value if isinstance(item, str) and _ARTIFACT_ID.fullmatch(item)]
        if len(values) != len(value) or len(set(values)) != len(values):
            return None
        if operation == "concat" and not values:
            return None
        return values

    @staticmethod
    def _output_contract(operation: str, source_media_type: str) -> str:
        if operation == "audio_extract":
            return "AUDIO"
        if operation in {"probe", "preview"}:
            return "METADATA"
        if operation in {"save", "export"}:
            return "ARTIFACT_REFERENCE"
        return "VIDEO" if source_media_type.casefold().startswith("video/") else "MEDIA"


__all__ = [
    "ARTIFACT_LIBRARY_V2_SCHEMA_VERSION",
    "ArtifactLibraryV2",
    "MEDIA_PIPELINE_V2_SCHEMA_VERSION",
    "MediaPipelineV2",
    "PROJECT_WORKSPACE_V2_SCHEMA_VERSION",
    "ProjectWorkspaceV2",
]
