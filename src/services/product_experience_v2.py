"""Finite, server-owned Product Experience V2 navigation and onboarding data.

The service deliberately describes existing Hub routes rather than inspecting
files, configuration or providers.  It gives the desktop a stable dashboard,
onboarding and global-search contract without turning the browser into an
authority over work, settings or external applications.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


PRODUCT_EXPERIENCE_V2_SCHEMA = "product-experience.v2"
_MAX_QUERY_LENGTH = 80
_MAX_RESULTS = 8

_SURFACES: tuple[dict[str, str], ...] = (
    {"id": "dashboard", "label": "Bảng điều khiển", "route": "dashboard", "api": "/api/product-experience/v2", "kind": "dashboard", "description": "Tóm tắt trạng thái Hub, điều hướng và bước tiếp theo."},
    {"id": "image", "label": "Hình ảnh AI", "route": "image", "api": "/api/lifecycle", "kind": "workspace", "description": "Mở không gian ảnh và Image & Mask Studio."},
    {"id": "vision", "label": "Studio thị giác", "route": "vision", "api": "/api/capabilities", "kind": "workspace", "description": "Kiểm tra các công cụ thị giác qua Hub."},
    {"id": "media", "label": "Phương tiện", "route": "media", "api": "/api/media-pipeline/v2", "kind": "workspace", "description": "Xem preflight media có kiểu dữ liệu."},
    {"id": "jobs", "label": "Tác vụ", "route": "jobs", "api": "/api/jobs", "kind": "control_plane", "description": "Theo dõi hàng đợi, lịch sử và phục hồi tác vụ."},
    {"id": "models", "label": "Mô hình & Lưu trữ", "route": "models", "api": "/api/model-manager/v2", "kind": "control_plane", "description": "Xem inventory và preflight server-owned."},
    {"id": "projects", "label": "Dự án & Công thức", "route": "projects", "api": "/api/project-workspace/v2", "kind": "workspace", "description": "Quản lý metadata dự án, workflow và artifact."},
    {"id": "diagnostics", "label": "Diagnostics", "route": "diagnostics", "api": "/api/diagnostics/snapshot", "kind": "diagnostics", "description": "Xem chẩn đoán sanitized và bước phục hồi."},
    {"id": "settings", "label": "Cài đặt", "route": "settings", "api": "/api/settings", "kind": "settings", "description": "Chỉ áp dụng theme, ngôn ngữ và cấu hình sau khi bấm Áp dụng & lưu."},
)

_ONBOARDING: tuple[dict[str, str], ...] = (
    {"id": "review-dashboard", "step": "01", "title": "Kiểm tra Bảng điều khiển", "route": "dashboard", "reason": "Đọc snapshot loopback, module và job trước khi bắt đầu.", "action": "Mở Dashboard"},
    {"id": "inspect-capabilities", "step": "02", "title": "Chọn workspace có bằng chứng", "route": "vision", "reason": "Mở một route và kiểm tra trạng thái/capability trước khi yêu cầu runtime.", "action": "Mở Studio thị giác"},
    {"id": "review-jobs", "step": "03", "title": "Theo dõi tác vụ trong Hub", "route": "jobs", "reason": "Xem trạng thái, recovery và artifact bằng ID opaque thay vì đường dẫn máy.", "action": "Mở Tác vụ"},
    {"id": "resolve-diagnostics", "step": "04", "title": "Xử lý blocker bằng Diagnostics", "route": "diagnostics", "reason": "Xuất hoặc đọc gói diagnostics đã sanitize trước khi thay đổi runtime.", "action": "Mở Diagnostics"},
)


def _surface_projection(item: dict[str, str]) -> dict[str, str]:
    return {key: item[key] for key in ("id", "label", "route", "api", "kind", "description")}


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
        "persistent_state": "none",
        "execution": "not_run",
        "dry_run": True,
        "reason": "Onboarding is a reusable finite guide; completion is not fabricated or persisted in browser storage.",
        "next_action": "Open a listed Hub route when ready.",
    }


def search(query: object) -> dict[str, Any]:
    """Search the closed route catalog only; query never reaches files or data."""

    raw = query if isinstance(query, str) else ""
    normalized = " ".join(raw.strip().casefold().split())[:_MAX_QUERY_LENGTH]
    if not normalized:
        matches = list(_SURFACES[:_MAX_RESULTS])
    else:
        terms = tuple(normalized.split(" "))
        matches = [
            item for item in _SURFACES
            if all(term in " ".join((item["id"], item["label"], item["route"], item["description"])).casefold() for term in terms)
        ][: _MAX_RESULTS]
    return {
        "schema_version": PRODUCT_EXPERIENCE_V2_SCHEMA,
        "status": "completed",
        "query": normalized,
        "results": [_surface_projection(item) for item in matches],
        "truncated": len(matches) >= _MAX_RESULTS,
        "execution": "not_run",
        "dry_run": True,
        "reason": "Search is limited to the finite server-owned product surface catalog; no project, artifact, filesystem or external application data is searched.",
        "next_action": "Choose a result to navigate inside the Hub.",
    }


__all__ = ["PRODUCT_EXPERIENCE_V2_SCHEMA", "onboarding", "search", "snapshot"]
