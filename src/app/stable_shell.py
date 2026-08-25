"""Stable installed-product shell and atomic version-pointer contract.

The stable executable is deliberately independent from the source checkout.
It derives its installation root from its own executable location, validates a
bounded product/current payload manifest, and launches only a bundled runtime
under the installed ``versions`` tree.  No PATH, current working directory,
Git branch, source ``__file__`` or Temp fallback is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


INSTALLATION_SCHEMA = "v8.0.1-installation.v1"
PRODUCT_SCHEMA = "v8.0.1-product.v1"
POINTER_SCHEMA = "v8.0.1-pointer.v1"
VERSION_MANIFEST_SCHEMA = "v8.0.1-version-manifest.v1"
PRODUCT_ID = "LocalAIHub"
APP_USER_MODEL_ID = "LocalAIHub.Desktop"
LAUNCHER_NAME = "LocalAIHub.exe"
ICON_NAME = "local-ai-hub.ico"
CURRENT_NAME = "current.json"
MAX_JSON_BYTES = 128 * 1024
VERSION_ID_MAX = 32


class StableShellError(ValueError):
    """Fixed-code refusal from the installed product boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InstallationConfig:
    app_root: Path
    data_root: Path
    app_user_model_id: str


@dataclass(frozen=True)
class LaunchPlan:
    app_root: Path
    data_root: Path
    version: str
    payload_root: Path
    app_payload: Path
    runtime_pythonw: Path
    command: tuple[str, ...]
    environment: dict[str, str]


def _reject_constant(_value: str) -> None:
    raise StableShellError("NONFINITE_JSON")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise StableShellError("MANIFEST_UNREADABLE") from None
    if len(raw) > MAX_JSON_BYTES:
        raise StableShellError("MANIFEST_TOO_LARGE")
    try:
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            keys = [key for key, _value in items]
            if len(keys) != len(set(keys)):
                raise StableShellError("DUPLICATE_JSON_KEY")
            return dict(items)

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=_reject_constant)
    except StableShellError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise StableShellError("MANIFEST_INVALID_JSON") from None
    if not isinstance(value, dict):
        raise StableShellError("MANIFEST_ROOT_INVALID")
    return value


def _canonical_json(value: dict[str, Any]) -> bytes:
    try:
        return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise StableShellError("MANIFEST_SERIALIZATION_FAILED") from None


def _is_reparse(path: Path) -> bool:
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

            attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _safe_chain(path: Path, boundary: Path | None = None) -> None:
    current = path.absolute()
    root = boundary.absolute() if boundary is not None else None
    if root is not None:
        try:
            current.relative_to(root)
        except ValueError:
            raise StableShellError("PATH_OUTSIDE_INSTALL_ROOT") from None
    while True:
        if _is_reparse(current):
            raise StableShellError("INSTALL_ROOT_REPARSE")
        if root is not None and current == root:
            return
        if current.parent == current:
            if root is None:
                return
            raise StableShellError("INSTALL_ROOT_CHAIN_INVALID")
        current = current.parent


def _safe_relative(value: object, *, allow_slash: bool = True) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or "\x00" in value:
        raise StableShellError("RELATIVE_PATH_INVALID")
    if value.startswith(("/", "\\")) or ":" in value or "\\" in value:
        raise StableShellError("RELATIVE_PATH_INVALID")
    parts = value.split("/") if allow_slash else [value]
    if any(part in {"", ".", ".."} for part in parts):
        raise StableShellError("RELATIVE_PATH_INVALID")
    return "/".join(parts)


def _safe_version(value: object) -> str:
    if not isinstance(value, str) or len(value) > VERSION_ID_MAX or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None:
        raise StableShellError("VERSION_INVALID")
    if value.startswith((".", "-")) or value.endswith((".", "-")) or ".." in value or "--" in value:
        raise StableShellError("VERSION_INVALID")
    return value


def _under(app_root: Path, relative: str, *, require_file: bool = False) -> Path:
    candidate = app_root / Path(relative)
    _safe_chain(candidate.parent, app_root)
    try:
        candidate.absolute().relative_to(app_root.absolute())
    except ValueError:
        raise StableShellError("PATH_OUTSIDE_INSTALL_ROOT") from None
    if _is_reparse(candidate):
        raise StableShellError("PAYLOAD_REPARSE")
    if require_file and not candidate.is_file():
        raise StableShellError("PAYLOAD_FILE_MISSING")
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except (OSError, ValueError):
        raise StableShellError("PAYLOAD_READ_FAILED") from None
    return digest.hexdigest()


def load_installation_config(app_root: Path, *, allow_test_root: bool = False) -> InstallationConfig:
    root = app_root.absolute()
    if not allow_test_root and (root.name.casefold() in {"temp", ".git", "test", "tests", "worktree"} or any(part.casefold() in {"temp", ".git"} for part in root.parts)):
        raise StableShellError("INSTALL_ROOT_NOT_PRODUCTION")
    # Validate the complete physical chain. A safe-looking leaf under a
    # junction/reparse parent is not a production installation root.
    _safe_chain(root)
    value = _load_json(root / "installation.json")
    expected = {"schema_version", "product_id", "app_root", "data_root", "app_user_model_id", "launcher"}
    if set(value) != expected or value.get("schema_version") != INSTALLATION_SCHEMA or value.get("product_id") != PRODUCT_ID:
        raise StableShellError("INSTALLATION_MANIFEST_INVALID")
    try:
        configured_app = Path(str(value["app_root"])).expanduser().absolute()
        data_root = Path(str(value["data_root"])).expanduser().absolute()
    except (TypeError, ValueError):
        raise StableShellError("INSTALLATION_PATH_INVALID") from None
    if configured_app != root or not data_root.is_absolute() or data_root == root:
        raise StableShellError("INSTALLATION_PATH_INVALID")
    if not allow_test_root and any(part.casefold() in {"temp", ".git"} for part in data_root.parts):
        raise StableShellError("DATA_ROOT_NOT_PRODUCTION")
    _safe_chain(data_root)
    if value.get("app_user_model_id") != APP_USER_MODEL_ID or value.get("launcher") != LAUNCHER_NAME:
        raise StableShellError("INSTALLATION_IDENTITY_INVALID")
    return InstallationConfig(root, data_root, APP_USER_MODEL_ID)


def load_product_manifest(app_root: Path) -> dict[str, Any]:
    value = _load_json(app_root / "product.json")
    expected = {"schema_version", "product_id", "version", "launcher", "icon", "current_pointer"}
    if set(value) != expected or value.get("schema_version") != PRODUCT_SCHEMA or value.get("product_id") != PRODUCT_ID:
        raise StableShellError("PRODUCT_MANIFEST_INVALID")
    if value.get("launcher") != LAUNCHER_NAME or value.get("icon") != ICON_NAME or value.get("current_pointer") != CURRENT_NAME:
        raise StableShellError("PRODUCT_IDENTITY_INVALID")
    _safe_version(value.get("version"))
    return value


def load_current_pointer(app_root: Path) -> dict[str, Any]:
    value = _load_json(app_root / CURRENT_NAME)
    expected = {"schema_version", "version", "payload_relative", "manifest_sha256"}
    if set(value) != expected or value.get("schema_version") != POINTER_SCHEMA:
        raise StableShellError("CURRENT_POINTER_INVALID")
    version = _safe_version(value.get("version"))
    payload_relative = _safe_relative(value.get("payload_relative"))
    if payload_relative != f"versions/{version}":
        raise StableShellError("CURRENT_POINTER_INVALID")
    digest = value.get("manifest_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise StableShellError("CURRENT_POINTER_INVALID")
    return value


def resolve_launch_plan(app_root: Path, *, allow_test_root: bool = False) -> LaunchPlan:
    root = app_root.absolute()
    installation = load_installation_config(root, allow_test_root=allow_test_root)
    product = load_product_manifest(root)
    pointer = load_current_pointer(root)
    version = str(pointer["version"])
    payload_root = _under(root, str(pointer["payload_relative"]))
    manifest_path = _under(payload_root, "manifest.json", require_file=True)
    if _sha256(manifest_path) != pointer["manifest_sha256"]:
        raise StableShellError("CURRENT_MANIFEST_HASH_MISMATCH")
    manifest = _load_json(manifest_path)
    expected = {"schema_version", "product_id", "version", "app_relative", "runtime_relative", "entrypoint"}
    if set(manifest) != expected or manifest.get("schema_version") != VERSION_MANIFEST_SCHEMA or manifest.get("product_id") != PRODUCT_ID or manifest.get("version") != version:
        raise StableShellError("VERSION_MANIFEST_INVALID")
    if manifest.get("entrypoint") != "src.app.launcher":
        raise StableShellError("ENTRYPOINT_INVALID")
    app_payload = _under(payload_root, _safe_relative(manifest.get("app_relative")))
    runtime_pythonw = _under(payload_root, _safe_relative(manifest.get("runtime_relative")), require_file=True)
    if not app_payload.is_dir():
        raise StableShellError("APP_PAYLOAD_MISSING")
    if runtime_pythonw.suffix.casefold() != ".exe" or runtime_pythonw.name.casefold() != "pythonw.exe":
        raise StableShellError("RUNTIME_INVALID")
    environment = dict(os.environ)
    environment.update({
        "LOCALAIHUB_INSTALL_ROOT": str(root),
        "LOCALAIHUB_APP_ROOT": str(app_payload),
        "LOCALAIHUB_DATA_ROOT": str(installation.data_root),
        "PYTHONPATH": str(app_payload),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    })
    # Build identity is optional for legacy payloads, but when present it is
    # propagated into the API health handshake so restart/watchdog checks can
    # prove the exact payload rather than trusting process launch alone.
    build_path = payload_root / "build.json"
    try:
        build = _load_json(build_path) if build_path.is_file() and not build_path.is_symlink() else {}
        source_commit = build.get("source_commit") if isinstance(build, dict) else None
        if isinstance(source_commit, str) and re.fullmatch(r"[0-9a-f]{40}", source_commit) and source_commit == source_commit.lower():
            expected_payload = f"main-{source_commit[:12]}"
            if version == expected_payload:
                environment.update({"LOCALAIHUB_BUILD_SHA": source_commit, "LOCALAIHUB_BUILD_PAYLOAD": version})
    except (OSError, StableShellError, UnicodeError, json.JSONDecodeError):
        pass
    return LaunchPlan(root, installation.data_root, version, payload_root, app_payload, runtime_pythonw, (str(runtime_pythonw), "-m", "src.app.launcher"), environment)


def atomic_activate_pointer(app_root: Path, *, version: str, manifest_sha256: str) -> dict[str, Any]:
    root = app_root.absolute()
    _safe_version(version)
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise StableShellError("CURRENT_POINTER_INVALID")
    _safe_chain(root)
    payload = _under(root, f"versions/{version}")
    if not payload.is_dir():
        raise StableShellError("PAYLOAD_DIRECTORY_MISSING")
    manifest_path = _under(payload, "manifest.json", require_file=True)
    if _sha256(manifest_path) != manifest_sha256:
        raise StableShellError("VERSION_MANIFEST_HASH_MISMATCH")
    manifest = _load_json(manifest_path)
    if (
        set(manifest) != {"schema_version", "product_id", "version", "app_relative", "runtime_relative", "entrypoint"}
        or manifest.get("schema_version") != VERSION_MANIFEST_SCHEMA
        or manifest.get("product_id") != PRODUCT_ID
        or manifest.get("version") != version
        or manifest.get("entrypoint") != "src.app.launcher"
    ):
        raise StableShellError("VERSION_MANIFEST_INVALID")
    pointer = {"schema_version": POINTER_SCHEMA, "version": version, "payload_relative": f"versions/{version}", "manifest_sha256": manifest_sha256}
    target = root / CURRENT_NAME
    temporary = root / f".{CURRENT_NAME}.{os.getpid()}.tmp"
    raw = _canonical_json(pointer)
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except (OSError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise StableShellError("CURRENT_POINTER_WRITE_FAILED") from None
    return pointer


__all__ = [
    "APP_USER_MODEL_ID", "CURRENT_NAME", "ICON_NAME", "InstallationConfig", "LaunchPlan",
    "StableShellError", "atomic_activate_pointer", "load_current_pointer", "load_installation_config",
    "load_product_manifest", "resolve_launch_plan",
]
