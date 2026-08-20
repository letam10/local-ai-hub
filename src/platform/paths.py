"""Central application/data path resolution.

The source checkout (``APP_ROOT``) and mutable installation (``DATA_ROOT``)
are separate concepts. The owner installation may deliberately use legacy
single-root mode, while a clean clone can point data at another directory via
``LOCALAIHUB_DATA_ROOT``. No module should invent a workstation path.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


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
    """Resolve mutable data, preserving the current single-root installation."""

    explicit = _resolved(value)
    if explicit is not None:
        return explicit
    configured = _resolved(os.environ.get("LOCALAIHUB_DATA_ROOT"))
    return configured or app_root


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
        }


def get_paths(*, app_root: str | os.PathLike[str] | None = None, data_root: str | os.PathLike[str] | None = None) -> HubPaths:
    app = resolve_app_root(app_root)
    return HubPaths(app_root=app, data_root=resolve_data_root(app, data_root))


__all__ = ["HubPaths", "get_paths", "resolve_app_root", "resolve_data_root"]
