"""Explicit, bounded runtime/environment verification helpers.

Catalog discovery never invokes these functions.  A caller must explicitly
request verification and provide a fixed catalog runtime ID; the helper then
checks only the catalog-selected Python leaf and bounded import/pip commands.
It never installs packages, changes drivers/CUDA, or accepts a client command.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .catalog import ProductionCatalog, ProductionCatalogError, _is_reparse, _safe_leaf


class RuntimeProbeError(ValueError):
    pass


def inspect_runtime_environment(catalog: ProductionCatalog, runtime_id: str) -> dict[str, Any]:
    record = catalog.runtimes.get(runtime_id)
    if record is None:
        raise ProductionCatalogError("unknown_runtime_id")
    runtime = catalog.inspect_runtime(runtime_id)
    python_leaf = next((item for item in record["required_leaves"] if item.casefold().endswith(("python.exe", "python"))), None)
    if record["kind"] == "python" and python_leaf:
        root = catalog._root_for_runtime(record)
        target = _safe_leaf(root, python_leaf)
        runtime["python"] = {"present": bool(target and target.is_file()), "location_class": "environment_leaf"}
        if not target or not target.is_file():
            runtime["status"] = "NOT_INSTALLED"
            runtime["reason"] = "The catalog-selected Python leaf is missing."
    runtime["verification"] = "not_run"
    runtime["execution"] = "not_run"
    return runtime


def verify_python_environment(
    catalog: ProductionCatalog,
    runtime_id: str,
    *,
    required_imports: Iterable[str] = (),
    run_pip_check: bool = True,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Run the fixed environment's bounded version/import/pip checks."""

    record = catalog.runtimes.get(runtime_id)
    if record is None or record.get("kind") != "python":
        return {"status": "unavailable", "code": "python_runtime_required", "execution": "not_run"}
    python_leaf = next((item for item in record["required_leaves"] if item.casefold().endswith(("python.exe", "python"))), None)
    if not python_leaf:
        return {"status": "unavailable", "code": "python_leaf_missing_from_catalog", "execution": "not_run"}
    target = _safe_leaf(catalog._root_for_runtime(record), python_leaf)
    if not target or not target.is_file() or _is_reparse(target):
        return {"status": "unavailable", "code": "python_executable_missing", "execution": "not_run"}
    imports = sorted({item for item in required_imports if isinstance(item, str) and item.isidentifier()})
    script = "import sys;" + ";".join(f"import {item}" for item in imports) + ";print(sys.version_info[0])"
    try:
        version = subprocess.run([str(target), "-c", script], capture_output=True, text=True, timeout=max(1.0, min(60.0, float(timeout_seconds))), check=False)
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "code": "python_probe_failed", "execution": "completed", "imports": imports}
    if version.returncode != 0:
        return {"status": "partial", "code": "python_import_failed", "execution": "completed", "imports": imports}
    pip_state = "not_run"
    if run_pip_check:
        try:
            pip = subprocess.run([str(target), "-m", "pip", "check"], capture_output=True, text=True, timeout=max(1.0, min(60.0, float(timeout_seconds))), check=False)
            pip_state = "pass" if pip.returncode == 0 else "fail"
        except (OSError, subprocess.SubprocessError):
            pip_state = "unavailable"
    status = "OPERATIONAL" if pip_state in {"pass", "not_run"} else "PARTIAL"
    return {"status": status, "runtime_id": runtime_id, "execution": "completed", "python": {"major": version.stdout.strip()[:8], "location_class": "environment_leaf"}, "imports": imports, "pip_check": pip_state, "reason": "Bounded Python/import/pip verification completed; this does not run an AI workload."}


__all__ = ["RuntimeProbeError", "inspect_runtime_environment", "verify_python_environment"]
