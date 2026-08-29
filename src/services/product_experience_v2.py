"""Finite, server-owned Product Experience V2 navigation and onboarding data.

The service deliberately describes existing Hub routes rather than inspecting
files, configuration or providers.  It gives the desktop a stable dashboard,
onboarding and global-search contract without turning the browser into an
authority over work, settings or external applications.
"""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping, Sequence
import re
from typing import Any


PRODUCT_EXPERIENCE_V2_SCHEMA = "product-experience.v2"
_MAX_QUERY_LENGTH = 80
_MAX_RESULTS = 8
_UNSAFE_PUBLIC_TEXT = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\|/(?:users|home|tmp|var)/|https?://|api[_-]?key|token|password|secret|credential|private[_-]?key)")

_SURFACES: tuple[dict[str, str], ...] = (
    {"id": "dashboard", "label": "Bảng điều khiển", "route": "dashboard", "api": "/api/product-experience/v2", "kind": "dashboard", "description": "Tóm tắt trạng thái Hub, điều hướng và bước tiếp theo."},
    {"id": "image", "label": "Hình ảnh AI", "route": "image", "api": "/api/lifecycle", "kind": "workspace", "description": "Mở không gian ảnh và Image & Mask Studio."},
    {"id": "vision", "label": "Studio thị giác", "route": "vision", "api": "/api/capabilities", "kind": "workspace", "description": "Kiểm tra các công cụ thị giác qua Hub."},
    {"id": "tools", "label": "Công cụ", "route": "vision", "api": "/tools", "kind": "catalog", "description": "Tìm công cụ đã được Hub công bố."},
    {"id": "media", "label": "Phương tiện", "route": "media", "api": "/api/media-pipeline/v2", "kind": "workspace", "description": "Xem preflight media có kiểu dữ liệu."},
    {"id": "jobs", "label": "Tác vụ", "route": "jobs", "api": "/api/jobs", "kind": "control_plane", "description": "Theo dõi hàng đợi, lịch sử và phục hồi tác vụ."},
    {"id": "models", "label": "Mô hình & Lưu trữ", "route": "models", "api": "/api/model-manager/v2", "kind": "control_plane", "description": "Xem inventory và preflight server-owned."},
    {"id": "workflows", "label": "Workflow Library", "route": "projects", "api": "/api/workflow-library", "kind": "workspace", "description": "Tìm workflow đã lưu và template đã kiểm tra."},
    {"id": "artifacts", "label": "Artifact Library", "route": "projects", "api": "/api/artifact-library/v2", "kind": "control_plane", "description": "Tìm artifact bằng metadata opaque."},
    {"id": "projects", "label": "Dự án & Công thức", "route": "projects", "api": "/api/project-workspace/v2", "kind": "workspace", "description": "Quản lý metadata dự án, workflow và artifact."},
    {"id": "diagnostics", "label": "Diagnostics", "route": "diagnostics", "api": "/api/diagnostics/snapshot", "kind": "diagnostics", "description": "Xem chẩn đoán sanitized và bước phục hồi."},
    {"id": "settings", "label": "Cài đặt", "route": "settings", "api": "/api/settings", "kind": "settings", "description": "Chỉ áp dụng theme, ngôn ngữ và cấu hình sau khi bấm Áp dụng & lưu."},
)

_ONBOARDING: tuple[dict[str, str], ...] = (
    {"id": "system-detected", "step": "01", "title": "Xác nhận hệ thống", "route": "dashboard", "reason": "Đọc snapshot loopback và kiểm tra Hub API trước khi bắt đầu.", "action": "Mở Dashboard"},
    {"id": "gpu-review", "step": "02", "title": "Xem GPU / VRAM", "route": "dashboard", "reason": "Chỉ xem năng lực do server công bố; không tự dò GPU hoặc chạy model.", "action": "Xem snapshot"},
    {"id": "storage-review", "step": "03", "title": "Xem dung lượng", "route": "models", "reason": "Kiểm tra storage projection; deep scan chỉ chạy khi bạn yêu cầu.", "action": "Mở Lưu trữ"},
    {"id": "models-review", "step": "04", "title": "Kiểm tra model hiện có", "route": "models", "reason": "Xem model catalog/verification; không tự tải hoặc thay thế model.", "action": "Mở Models"},
    {"id": "runtime-review", "step": "05", "title": "Kiểm tra runtime", "route": "components", "reason": "Xem component/runtime readiness và dependency blocker trước khi dùng.", "action": "Mở Components"},
    {"id": "external-review", "step": "06", "title": "Xem ứng dụng ngoài", "route": "airi", "reason": "AIRI và ứng dụng ngoài vẫn external-managed; Hub không hack API/WebView.", "action": "Mở AIRI"},
    {"id": "privacy-updates", "step": "07", "title": "Đặt quyền riêng tư / cập nhật", "route": "settings", "reason": "Cấu hình chỉ có hiệu lực sau Áp dụng & lưu; lịch update mặc định không tự cài.", "action": "Mở Cài đặt"},
)


def _surface_projection(item: dict[str, str]) -> dict[str, str]:
    return {key: item[key] for key in ("id", "label", "route", "api", "kind", "description")}


def _search_projection(item: Mapping[str, Any]) -> dict[str, str]:
    """Return one stable result shape for static and live metadata records."""

    return {key: str(item.get(key) or "") for key in ("id", "label", "route", "category", "api", "kind", "description")}


def snapshot() -> dict[str, Any]:
    """Return a static, bounded experience contract with no local-state read."""

    return {
        "schema_version": PRODUCT_EXPERIENCE_V2_SCHEMA,
        "status": "completed",
        "surfaces": [_surface_projection(item) for item in _SURFACES],
        "onboarding": deepcopy(list(_ONBOARDING)),
        "settings_behavior": {
            "theme": "applied_after_explicit_save",
            "language": "applied_after_explicit_save",
            "reason": "Settings changes are staged in the UI and only become effective after the server accepts Áp dụng & lưu.",
        },
        "diagnostics": {"route": "diagnostics", "api": "/api/diagnostics/snapshot", "mode": "sanitized_read_only"},
        "execution": "not_run",
        "dry_run": True,
        "reason": "Product Experience V2 is a finite Hub navigation/onboarding projection; it does not inspect filesystem state, save settings, start a provider or execute a job.",
        "next_action": "Use a listed route, then inspect that route's server-owned snapshot before requesting a runtime action.",
    }


def onboarding() -> dict[str, Any]:
    value = snapshot()
    return {
        "schema_version": PRODUCT_EXPERIENCE_V2_SCHEMA,
        "status": "completed",
        "steps": value["onboarding"],
        "skip_supported": True,
        "persistent_state": "none",
        "execution": "not_run",
        "dry_run": True,
        "reason": "Onboarding is a reusable finite guide; completion is not fabricated or persisted in browser storage.",
        "next_action": "Open a listed Hub route when ready.",
    }


def _record(category: str, identifier: object, label: object, description: object, route: str) -> dict[str, str] | None:
    safe_id = str(identifier or "").strip()[:96]
    raw_label = str(label or safe_id or "").strip()[:120]
    raw_description = str(description or "").strip()[:240]
    safe_label = raw_label if raw_label and not _UNSAFE_PUBLIC_TEXT.search(raw_label) else "Mục server-owned"
    safe_description = raw_description if raw_description and not _UNSAFE_PUBLIC_TEXT.search(raw_description) else "Metadata server-owned đã được sanitize."
    if not safe_id or not safe_label or any(char in safe_id for char in ("/", "\\", ":")) or _UNSAFE_PUBLIC_TEXT.search(safe_id):
        return None
    return {"id": safe_id, "label": safe_label, "description": safe_description, "route": route, "category": category}


def _live_records(sources: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """Normalize bounded server-owned records into opaque search results."""

    if not isinstance(sources, Mapping):
        return []
    records: list[dict[str, str]] = []
    specs = (
        ("models", "models", "models", "model_id", "display_name", "purpose"),
        ("tools", "tools", "tools", "name", "display_name", "description"),
        ("projects", "projects", "projects", "id", "title", "description"),
        ("workflows", "workflows", "workflows", "id", "title", "description"),
        ("jobs", "jobs", "jobs", "id", "title", "tool"),
        ("artifacts", "artifacts", "artifacts", "id", "name", "media_type"),
    )
    for category, route, source_name, id_key, label_key, description_key in specs:
        values = sources.get(source_name, [])
        if isinstance(values, Mapping):
            values = values.get(source_name) or values.get("records") or values.get("items") or []
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            continue
        for value in list(values)[:120]:
            if not isinstance(value, Mapping):
                continue
            identifier = value.get(id_key) or value.get("id") or value.get("artifact_id") or value.get("job_id")
            label = value.get(label_key) or value.get("name") or value.get("title") or identifier
            description = value.get(description_key) or value.get("description") or value.get("status") or ""
            item = _record(category, identifier, label, description, route)
            if item is not None:
                records.append(item)
    settings_item = _record("settings", "settings", "Cài đặt", "Ngôn ngữ, theme, cửa sổ và chính sách job.", "settings")
    if settings_item is not None:
        records.append(settings_item)
    return records


def search(query: object, *, sources: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Search bounded server-owned records, falling back to route catalog."""

    raw = query if isinstance(query, str) else ""
    normalized = " ".join(raw.strip().casefold().split())[:_MAX_QUERY_LENGTH]
    live_records = _live_records(sources)
    search_categories = {"models", "tools", "projects", "workflows", "jobs", "artifacts", "settings"}
    static_records = [_surface_projection(item) | {"category": item["id"] if item["id"] in search_categories else item["kind"]} for item in _SURFACES]
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*static_records, *live_records]:
        identity = (str(item.get("category") or ""), str(item.get("id") or ""))
        if identity in seen:
            continue
        seen.add(identity)
        catalog.append(item)
    if not normalized:
        priority = ("dashboard", "models", "tools", "projects", "workflows", "jobs", "artifacts", "settings")
        priority_items = [item for wanted in priority for item in catalog if item.get("id") == wanted]
        matches = priority_items[:_MAX_RESULTS]
    else:
        terms = tuple(normalized.split(" "))
        matches = [
            item for item in catalog
            if all(term in " ".join((item["id"], item["label"], item["route"], item["description"])).casefold() for term in terms)
        ][: _MAX_RESULTS]
    return {
        "schema_version": PRODUCT_EXPERIENCE_V2_SCHEMA,
        "status": "completed",
        "query": normalized,
        "results": [_search_projection(item) for item in matches],
        "truncated": len(matches) >= _MAX_RESULTS,
        "execution": "not_run",
        "dry_run": True,
        "reason": "Search uses only the finite server-owned product surface catalog and bounded metadata projections; it never exposes paths, secrets, artifact bytes or external application data.",
        "next_action": "Choose a result to navigate inside the Hub.",
    }


__all__ = ["PRODUCT_EXPERIENCE_V2_SCHEMA", "onboarding", "search", "snapshot"]
