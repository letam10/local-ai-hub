"""Deterministic resolution of the small Core Python runtime.

The resolver is deliberately separate from module runtimes.  It only considers
fixed Hub-owned candidates and an explicitly allowed development interpreter;
it never scans drives, installs packages, starts a process or changes PATH.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import sys
from typing import Any

from src.platform.paths import HubPaths, get_paths


CORE_RUNTIME_SCHEMA = "core-runtime.v1"
MIN_PYTHON = (3, 10)


def _is_reparse(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        mode = path.lstat().st_mode
    except OSError:
        return True
    if stat.S_ISLNK(mode):
        return True
    if os.name == "nt":
        try:
            import ctypes

            FILE_ATTRIBUTE_REPARSE_POINT = 0x400
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            attrs = int(attrs) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
        except (AttributeError, OSError):
            return False
    return False


def _safe_file(path: Path, root: Path) -> bool:
    """Validate a fixed candidate without resolving through a reparse point."""

    try:
        lexical_root = root.absolute()
        lexical = path.absolute()
        lexical.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    current = lexical
    while current.parent != current:
        if _is_reparse(current):
            return False
        current = current.parent
    if _is_reparse(current):
        return False
    try:
        root = lexical_root.resolve()
        lexical.resolve().relative_to(root)
        return lexical.is_file() and not _is_reparse(lexical)
    except OSError:
        return False


def _version_for(path: Path) -> tuple[int, int] | None:
    """Return a known version without launching an arbitrary executable."""

    try:
        if path.resolve() == Path(sys.executable).resolve():
            return (sys.version_info.major, sys.version_info.minor)
    except OSError:
        return None
    # A managed environment may expose a pyvenv.cfg next to its Scripts leaf.
    cfg = path.parent.parent / "pyvenv.cfg"
    try:
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("version ="):
                value = line.split("=", 1)[1].strip().split(".")
                return (int(value[0]), int(value[1]))
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    return None


@dataclass(frozen=True)
class CoreRuntimeCandidate:
    source: str
    python: Path
    pythonw: Path | None
    version: tuple[int, int] | None
    status: str

    def safe_projection(self) -> dict[str, Any]:
        version = f"{self.version[0]}.{self.version[1]}" if self.version else None
        return {
            "source": self.source,
            "status": self.status,
            "version": version,
            "pythonw_available": self.pythonw is not None,
            "location_class": {
                "managed_core": "core_environment",
                "legacy_hub": "legacy_hub_environment",
                "system": "development_python",
            }.get(self.source, "unknown"),
        }


class CoreRuntimeResolver:
    """Resolve Core Python in managed → legacy → development order."""

    def __init__(self, *, paths: HubPaths | None = None, allow_system: bool = True) -> None:
        self.paths = paths or get_paths()
        self.allow_system = bool(allow_system)

    def _fixed_candidate(self, source: str, environment: Path) -> CoreRuntimeCandidate | None:
        scripts = environment / "Scripts"
        python = scripts / "python.exe"
        pythonw = scripts / "pythonw.exe"
        if not _safe_file(python, self.paths.environments_root):
            return None
        version = _version_for(python)
        status = "available" if version is None or version >= MIN_PYTHON else "unsupported_version"
        return CoreRuntimeCandidate(source, python, pythonw if _safe_file(pythonw, self.paths.environments_root) else None, version, status)

    def _system_candidate(self) -> CoreRuntimeCandidate | None:
        candidates = [Path(sys.executable)]
        found = shutil.which("python")
        if found:
            candidates.append(Path(found))
        seen: set[str] = set()
        for python in candidates:
            try:
                key = str(python.resolve()).casefold()
            except OSError:
                continue
            if key in seen or not python.is_file() or _is_reparse(python):
                continue
            seen.add(key)
            version = _version_for(python)
            if version is None or version < MIN_PYTHON:
                continue
            pythonw = python.with_name("pythonw.exe")
            return CoreRuntimeCandidate("system", python, pythonw if pythonw.is_file() and not _is_reparse(pythonw) else None, version, "available")
        return None

    def candidates(self) -> list[CoreRuntimeCandidate]:
        values: list[CoreRuntimeCandidate] = []
        managed = self._fixed_candidate("managed_core", self.paths.environments_root / "core")
        legacy = self._fixed_candidate("legacy_hub", self.paths.environments_root / "hub")
        if managed:
            values.append(managed)
        if legacy:
            values.append(legacy)
        if self.allow_system:
            system = self._system_candidate()
            if system:
                values.append(system)
        return values

    def resolve(self) -> CoreRuntimeCandidate | None:
        for candidate in self.candidates():
            if candidate.status == "available":
                return candidate
        return None

    def inspect(self) -> dict[str, Any]:
        candidates = self.candidates()
        selected = self.resolve()
        if selected is None:
            return {
                "schema_version": CORE_RUNTIME_SCHEMA,
                "status": "MISSING",
                "selected_source": None,
                "candidates": [item.safe_projection() for item in candidates],
                "location_class": "core_environment",
                "reason": "No compatible Core Python was found in managed, legacy or development locations.",
                "next_action": "Run the explicit Core bootstrap plan or install a supported Python 3.10+ prerequisite.",
            }
        return {
            "schema_version": CORE_RUNTIME_SCHEMA,
            "status": "AVAILABLE",
            "selected_source": selected.source,
            "candidates": [item.safe_projection() for item in candidates],
            "location_class": selected.safe_projection()["location_class"],
            "reason": "Core Python resolved without probing optional AI runtimes.",
            "next_action": "Continue with the Core dependency and WebView2 preflight.",
        }

    def resolve_python(self) -> Path | None:
        selected = self.resolve()
        return selected.python if selected else None

    def resolve_pythonw(self) -> Path | None:
        selected = self.resolve()
        return selected.pythonw if selected else None


def inspect_core_runtime(*, paths: HubPaths | None = None, allow_system: bool = True) -> dict[str, Any]:
    return CoreRuntimeResolver(paths=paths, allow_system=allow_system).inspect()


__all__ = ["CORE_RUNTIME_SCHEMA", "MIN_PYTHON", "CoreRuntimeCandidate", "CoreRuntimeResolver", "inspect_core_runtime"]
