"""Pure, non-mutating contract for LocalAIHub shortcut migration.

The real Windows shortcut locations are intentionally outside this module's
scope.  The PowerShell repair script may use the same decisions around its
COM object, while tests and Diagnostics can validate the owned/stale/admin
states with isolated dictionaries and never touch a user's shortcuts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


SHORTCUT_CORRECT = "CORRECT"
SHORTCUT_STALE = "STALE_HUB_OWNED"
SHORTCUT_AMBIGUOUS = "AMBIGUOUS"
SHORTCUT_UNOWNED = "UNOWNED"
MIGRATION_ALREADY_CORRECT = "ALREADY_CORRECT"
MIGRATION_UPDATE_REQUIRED = "UPDATE_REQUIRED"
MIGRATION_ADMIN_REQUIRED = "ADMIN_REQUIRED"
MIGRATION_UNOWNED = "UNOWNED_UNTOUCHED"
MIGRATION_AMBIGUOUS = "AMBIGUOUS_REVIEW_REQUIRED"


def _path(value: object) -> str:
    if not isinstance(value, (str, os.PathLike)):
        return ""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(value))))
    except (TypeError, ValueError, OSError):
        return ""


def _basename(value: object) -> str:
    return os.path.basename(_path(value)).casefold()


def _root_script_reference(arguments: str, root: str) -> bool:
    normalized = arguments.replace("/", "\\").casefold()
    script = _path(Path(root) / "LocalAIHub.vbs").replace("/", "\\").casefold()
    return script in normalized or ("localaihub.vbs" in normalized and _path(root).casefold() in normalized)


def classify_shortcut(shortcut: Mapping[str, Any] | None, install_root: str | os.PathLike[str]) -> dict[str, Any]:
    """Classify one shortcut without opening, changing, or resolving a link."""

    value = shortcut if isinstance(shortcut, Mapping) else {}
    root = _path(install_root)
    expected = _path(Path(root) / "LocalAIHub.exe")
    target = _path(value.get("target_path", value.get("target")))
    arguments = str(value.get("arguments") or "")
    working = _path(value.get("working_directory", value.get("working")))
    user_owned = value.get("user_owned") is True or value.get("owned") is False
    scope = str(value.get("scope") or "per_user").casefold()
    if scope not in {"per_user", "all_users"}:
        scope = "unknown"

    if user_owned:
        return {"classification": SHORTCUT_UNOWNED, "owned": False, "scope": scope, "stale": False, "target_is_hub": False}
    if target == expected:
        if not arguments.strip() and (not working or working == root):
            return {"classification": SHORTCUT_CORRECT, "owned": True, "scope": scope, "stale": False, "target_is_hub": True}
        return {"classification": SHORTCUT_AMBIGUOUS, "owned": True, "scope": scope, "stale": False, "target_is_hub": True}

    wscript = _basename(target) in {"wscript.exe", "wscript"}
    has_hub_script = "localaihub.vbs" in arguments.casefold()
    if wscript and has_hub_script and _root_script_reference(arguments, root):
        return {"classification": SHORTCUT_STALE, "owned": True, "scope": scope, "stale": True, "target_is_hub": True}
    if wscript or has_hub_script or "local ai hub" in str(value.get("name") or "").casefold():
        return {"classification": SHORTCUT_AMBIGUOUS, "owned": True, "scope": scope, "stale": False, "target_is_hub": False}
    return {"classification": SHORTCUT_UNOWNED, "owned": False, "scope": scope, "stale": False, "target_is_hub": False}


def plan_shortcut_migration(
    shortcut: Mapping[str, Any] | None,
    install_root: str | os.PathLike[str],
    *,
    authorized_elevation: bool = False,
) -> dict[str, Any]:
    """Return a bounded action; this function never mutates a shortcut."""

    classification = classify_shortcut(shortcut, install_root)
    kind = classification["classification"]
    scope = classification["scope"]
    if kind == SHORTCUT_CORRECT:
        return {**classification, "status": MIGRATION_ALREADY_CORRECT, "can_update": False, "action": "Shortcut đã trỏ trực tiếp tới LocalAIHub.exe."}
    if kind == SHORTCUT_UNOWNED:
        return {**classification, "status": MIGRATION_UNOWNED, "can_update": False, "action": "Giữ nguyên shortcut không thuộc sở hữu LocalAIHub."}
    if kind == SHORTCUT_AMBIGUOUS:
        return {**classification, "status": MIGRATION_AMBIGUOUS, "can_update": False, "action": "Cần kiểm tra thủ công; Hub không tự sửa shortcut mơ hồ."}
    if scope == "all_users" and not authorized_elevation:
        return {
            **classification,
            "status": MIGRATION_ADMIN_REQUIRED,
            "can_update": False,
            "action": "Mở Diagnostics bằng quyền Administrator rồi chọn Sửa shortcut dùng chung.",
        }
    return {
        **classification,
        "status": MIGRATION_UPDATE_REQUIRED,
        "can_update": True,
        "action": "Cập nhật shortcut Hub-owned sang LocalAIHub.exe.",
    }


def migration_complete(shortcuts: list[Mapping[str, Any]], install_root: str | os.PathLike[str]) -> bool:
    """Return false while any owned stale/ambiguous shortcut remains active."""

    return all(
        classify_shortcut(shortcut, install_root)["classification"] in {SHORTCUT_CORRECT, SHORTCUT_UNOWNED}
        for shortcut in shortcuts
    )


def normal_launch_command(install_root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return the only normal launch target; no Temp VBS is ever returned."""

    return (str(Path(install_root) / "LocalAIHub.exe"),)


__all__ = [
    "MIGRATION_ADMIN_REQUIRED", "MIGRATION_AMBIGUOUS", "MIGRATION_ALREADY_CORRECT",
    "MIGRATION_UNOWNED", "MIGRATION_UPDATE_REQUIRED", "SHORTCUT_AMBIGUOUS",
    "SHORTCUT_CORRECT", "SHORTCUT_STALE", "SHORTCUT_UNOWNED", "classify_shortcut",
    "migration_complete", "normal_launch_command", "plan_shortcut_migration",
]
