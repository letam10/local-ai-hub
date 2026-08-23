"""Central application/data path resolution.

The source checkout (``APP_ROOT``) and mutable installation (``DATA_ROOT``)
are separate concepts. The owner installation may deliberately use legacy
single-root mode, while a clean clone can point data at another directory via
``LOCALAIHUB_DATA_ROOT``. No module should invent a workstation path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import json


ROOT_CLASSES = frozenset({"models_root", "runtime_root", "environments_root", "external_managed"})
COMPONENT_TYPES = frozenset({"model", "runtime"})
_SAFE_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")


class ComponentPathError(ValueError):
    """Fixed-code failure for a server-owned component root/leaf lookup."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_reparse_point(path: Path) -> bool:
    """Return whether a path is a symlink/junction/reparse point.

    The helper is deliberately conservative: an unreadable attribute is
    treated as unsafe instead of falling through to a caller-controlled path.
    """

    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes

            attributes = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            if attributes == 0xFFFFFFFF:
                return False
            return bool(attributes & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _safe_ancestor_chain(path: Path, *, stop: Path | None = None) -> bool:
    """Check every existing original ancestor without following a link."""

    current = path.absolute()
    boundary = stop.absolute() if stop is not None else None
    while True:
        if is_reparse_point(current):
            return False
        if boundary is not None and current == boundary:
            return True
        if current.parent == current:
            return boundary is None
        current = current.parent


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ComponentPathError("invalid_component_leaf")
    if "\x00" in value or value.startswith(("/", "\\")) or ":" in value:
        raise ComponentPathError("unsafe_component_leaf")
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ComponentPathError("unsafe_component_leaf")
    return "/".join(parts)


def _resolved(value: str | os.PathLike[str] | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser().resolve()


def resolve_app_root(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the checkout root without assuming a drive letter."""

    explicit = _resolved(value)
    if explicit is not None:
        return explicit
    configured = _resolved(os.environ.get("LOCALAIHUB_APP_ROOT"))
    if configured is not None:
        return configured
    return Path(__file__).resolve().parents[2]


def resolve_data_root(app_root: Path, value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve mutable data from explicit/env/installed-product authority."""

    explicit = _resolved(value)
    if explicit is not None:
        return explicit
    configured = _resolved(os.environ.get("LOCALAIHUB_DATA_ROOT"))
    if configured is not None:
        return configured
    install_root = _resolved(os.environ.get("LOCALAIHUB_INSTALL_ROOT"))
    # An installed shell sets LOCALAIHUB_INSTALL_ROOT before importing the
    # application. Without that explicit authority, do not scan arbitrary
    # source/parent directories for installation.json; legacy checkout mode
    # remains app_root and avoids touching unrelated files during reads.
    if install_root is None:
        return app_root
    candidates = [install_root]
    for candidate in candidates:
        if candidate is None:
            continue
        config = candidate / "installation.json"
        try:
            raw = config.read_bytes()
            if len(raw) > 128 * 1024:
                continue
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != "v8.0.1-installation.v1":
                continue
            configured_app = _resolved(value.get("app_root"))
            data_root = _resolved(value.get("data_root"))
            if configured_app == candidate and data_root is not None and all(part.casefold() not in {"temp", ".git"} for part in data_root.parts):
                return data_root
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return app_root


@dataclass(frozen=True)
class HubPaths:
    """Typed roots shared by bootstrap, managers and future API routes."""

    app_root: Path
    data_root: Path

    @property
    def config_root(self) -> Path:
        return self.data_root / "Config"

    @property
    def models_root(self) -> Path:
        return self.data_root / "Models"

    @property
    def environments_root(self) -> Path:
        return self.data_root / "Environments"

    @property
    def runtime_root(self) -> Path:
        return self.data_root / "runtime"

    @property
    def output_root(self) -> Path:
        return self.data_root / "Output"

    @property
    def cache_root(self) -> Path:
        return self.data_root / "Cache"

    @property
    def temp_root(self) -> Path:
        return self.data_root / "Temp"

    @property
    def log_root(self) -> Path:
        return self.data_root / "Logs"

    @property
    def report_root(self) -> Path:
        return self.data_root / "Reports"

    @property
    def backup_root(self) -> Path:
        return self.data_root / "Backups"

    @property
    def legacy_single_root_mode(self) -> bool:
        return self.app_root == self.data_root

    def roots(self) -> dict[str, Path]:
        return {
            "app": self.app_root,
            "data": self.data_root,
            "config": self.config_root,
            "models": self.models_root,
            "environments": self.environments_root,
            "runtime": self.runtime_root,
            "output": self.output_root,
            "cache": self.cache_root,
            "temp": self.temp_root,
            "logs": self.log_root,
            "reports": self.report_root,
            "backups": self.backup_root,
        }

    def safe_projection(self) -> dict[str, object]:
        """Return a browser-safe root summary with no workstation paths."""

        return {
            "mode": "legacy_single_root" if self.legacy_single_root_mode else "split_app_data",
            "root_keys": sorted(self.roots()),
            "data_location_class": "persistent_configured" if not self.legacy_single_root_mode else "app_root",
        }


def get_paths(*, app_root: str | os.PathLike[str] | None = None, data_root: str | os.PathLike[str] | None = None) -> HubPaths:
    app = resolve_app_root(app_root)
    return HubPaths(app_root=app, data_root=resolve_data_root(app, data_root))


def _root_for(paths: HubPaths, component_id: str, component_type: str, root_class: str | None) -> Path:
    if not isinstance(component_type, str) or component_type not in COMPONENT_TYPES:
        raise ComponentPathError("unknown_component_type")
    if not isinstance(component_id, str) or not _SAFE_COMPONENT_ID.fullmatch(component_id):
        raise ComponentPathError("invalid_component_id")
    if root_class is not None and (not isinstance(root_class, str) or root_class not in ROOT_CLASSES):
        raise ComponentPathError("unknown_root_class")
    if component_type == "model":
        if root_class not in {None, "models_root"}:
            raise ComponentPathError("component_root_mismatch")
        return paths.models_root / component_id
    if root_class == "models_root":
        raise ComponentPathError("component_root_mismatch")
    if root_class == "environments_root":
        return paths.environments_root
    if root_class == "external_managed":
        return paths.runtime_root / "external"
    return paths.runtime_root


def resolve_component_root(
    paths: HubPaths,
    component_id: str,
    component_type: str,
    root_class: str | None = None,
    *,
    caller_root: str | os.PathLike[str] | None = None,
    relative_leaves: Iterable[object] | None = None,
    require_exists: bool = True,
) -> Path:
    """Resolve one fixed Hub-owned component root.

    ``caller_root`` is intentionally rejected even when it happens to point at
    a managed directory.  Roots and leaves are derived from the server-owned
    ``HubPaths`` plus catalog identity; a request must never smuggle in a
    filesystem path.  ``require_exists=False`` is used only by fast startup
    inspection so a missing installation can be reported as NOT_INSTALLED.
    """

    if caller_root is not None:
        raise ComponentPathError("caller_root_not_allowed")
    root = _root_for(paths, component_id, component_type, root_class)
    managed_boundary = paths.data_root
    try:
        root.absolute().relative_to(managed_boundary.absolute())
    except (OSError, ValueError):
        raise ComponentPathError("component_root_escape") from None
    if not _safe_ancestor_chain(managed_boundary):
        raise ComponentPathError("unsafe_managed_boundary")
    if not _safe_ancestor_chain(root, stop=managed_boundary):
        raise ComponentPathError("unsafe_component_root")
    if root.exists() and (not root.is_dir() or is_reparse_point(root)):
        raise ComponentPathError("component_root_unavailable")
    if require_exists and not root.is_dir():
        raise ComponentPathError("component_root_unavailable")
    if relative_leaves is not None:
        for relative in relative_leaves:
            resolve_component_leaf(root, relative, require_exists=require_exists)
    return root


def resolve_managed_root(*args: object, **kwargs: object) -> Path:
    """Compatibility alias for the canonical component-root resolver."""

    return resolve_component_root(*args, **kwargs)  # type: ignore[arg-type]


def resolve_component_leaf(root: Path, relative_path: object, *, require_exists: bool = True) -> Path:
    """Resolve a fixed relative catalog leaf below an already-owned root."""

    normalized = _safe_relative(relative_path)
    root = Path(root).absolute()
    if not _safe_ancestor_chain(root):
        raise ComponentPathError("unsafe_component_root")
    candidate = (root / Path(normalized)).absolute()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ComponentPathError("component_leaf_escape") from None
    if not _safe_ancestor_chain(candidate, stop=root):
        raise ComponentPathError("unsafe_component_leaf")
    try:
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        raise ComponentPathError("component_leaf_escape") from None
    if require_exists and (not candidate.is_file() or is_reparse_point(candidate)):
        raise ComponentPathError("component_leaf_unavailable")
    return candidate


__all__ = [
    "COMPONENT_TYPES", "ComponentPathError", "HubPaths", "ROOT_CLASSES",
    "get_paths", "is_reparse_point", "resolve_app_root", "resolve_component_leaf",
    "resolve_component_root", "resolve_data_root", "resolve_managed_root",
]
