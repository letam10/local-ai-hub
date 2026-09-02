"""Trusted discovery and launch boundary for managed desktop applications.

The browser is allowed to name an application, never an executable.  This
module resolves that identifier from a machine-local registry or a bounded
Windows installer/App Paths observation, verifies the target as an identity
matched regular executable, and returns only a path-free projection.

The implementation deliberately does not scan a drive, infer an executable
from an installation directory, read credentials, or mutate an external
installation.  AIRI is an installer-managed application and is the only
external application that receives Windows registry discovery in this
milestone.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import hashlib
import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from src.services.api.config import read_local_config
from src.services.process_manager.windows import popen_hidden, run_hidden
from src.shared.paths.registry import CONFIG_ROOT


LOCAL_REGISTRY = CONFIG_ROOT / "application_registry.local.json"
EXAMPLE_REGISTRY = CONFIG_ROOT / "application_registry.example.json"

APPLICATION_REGISTRY_SCHEMA_VERSION = 2
APPLICATION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_UNRESOLVED_CONFIG = re.compile(r"(?:\$\{[^}]+\}|%[^%]+%)")
_LOCAL_PATH_MARKER = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:Users|home|private)(?:[\\/]|$))", re.IGNORECASE)
_SENSITIVE_MARKER = re.compile(r"(?:api[_ -]?key|password|secret|token|authorization)", re.IGNORECASE)
_MAX_ARGUMENTS = 16
_MAX_ARGUMENT_LENGTH = 512
_MAX_APPLICATIONS = 64
_MAX_EXECUTABLE_BYTES = 1024 * 1024 * 1024
_TASKLIST_CACHE_SECONDS = 5.0

AIRI_APPLICATION_ID = "airi"
AIRI_DISPLAY_NAME = "AIRI"
AIRI_PRODUCT_NAME = "AIRI"
AIRI_PUBLISHER = "Moeru AI"

DISCOVERY_VERIFIED = "verified"
DISCOVERY_AMBIGUOUS = "ambiguous"
DISCOVERY_UNAVAILABLE = "unavailable"
LAUNCH_AVAILABLE = "available"
LAUNCH_AMBIGUOUS = "ambiguous"
LAUNCH_UNAVAILABLE = "unavailable"

_tasklist_cache: tuple[float, set[str]] | None = None
_tasklist_lock = threading.RLock()


@dataclass(frozen=True)
class _Candidate:
    """Internal verified target.  Instances never cross the API boundary."""

    executable: Path
    working_directory: Path
    arguments: tuple[str, ...]
    fingerprint: str
    source: str
    expected_product: str
    expected_publisher: str


@dataclass(frozen=True)
class _Discovery:
    state: str
    launch_state: str
    reason_code: str
    source: str
    candidate: _Candidate | None = None
    candidates: tuple[_Candidate, ...] = ()
    identity_found: bool = False


def _read_registry_state() -> dict[str, Any]:
    """Read the registry while retaining local-vs-example provenance."""

    result = read_local_config(
        LOCAL_REGISTRY.name,
        {"schema_version": APPLICATION_REGISTRY_SCHEMA_VERSION, "applications": []},
        config_dir=CONFIG_ROOT,
        example_name=EXAMPLE_REGISTRY.name,
    )
    value = result.get("value")
    return {
        "value": value if isinstance(value, Mapping) else {},
        "provenance": str(result.get("provenance") or "missing"),
        "error": str(result.get("error") or ""),
        "valid": _valid_registry_document(value),
    }


def _read_registry() -> dict[str, Any]:
    """Legacy value-only view retained for compatibility."""

    return dict(_read_registry_state()["value"])


def _valid_registry_document(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {1, 2, 3}:
        return False
    rows = value.get("applications")
    if not isinstance(rows, list) or len(rows) > _MAX_APPLICATIONS:
        return False
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        identifier = row.get("id")
        if not isinstance(identifier, str) or not APPLICATION_ID.fullmatch(identifier):
            return False
    return True


def _registry_entries(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = state.get("value")
    rows = value.get("applications") if isinstance(value, Mapping) else None
    return [dict(row) for row in rows[:_MAX_APPLICATIONS] if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _normalize_identity(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _identity_field(value: Mapping[str, Any], *names: str) -> str:
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _expected_identity(entry: Mapping[str, Any], application_id: str) -> tuple[str, str] | None:
    """Return only an identity that is in the fixed product allowlist."""

    if application_id != AIRI_APPLICATION_ID:
        # M2 has one reviewed external identity.  Other application IDs stay
        # visible as registry metadata but cannot borrow AIRI's allowlist.
        return None
    identity = entry.get("identity")
    if not isinstance(identity, Mapping):
        identity = entry.get("identity_manifest")
    if not isinstance(identity, Mapping):
        identity = entry
    product = _identity_field(identity, "product_name", "product", "ProductName")
    publisher = _identity_field(identity, "publisher", "company_name", "company", "Publisher", "CompanyName")
    product = product or AIRI_PRODUCT_NAME
    publisher = publisher or AIRI_PUBLISHER
    if not product or not publisher:
        return None
    if _normalize_identity(product) != _normalize_identity(AIRI_PRODUCT_NAME):
        return None
    if _normalize_identity(publisher) != _normalize_identity(AIRI_PUBLISHER):
        return None
    return AIRI_PRODUCT_NAME, AIRI_PUBLISHER


def _identity_matches(value: object, expected_product: str, expected_publisher: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    product = _identity_field(value, "product_name", "product", "ProductName")
    publisher = _identity_field(value, "publisher", "company_name", "company", "Publisher", "CompanyName")
    return (
        _normalize_identity(product) == _normalize_identity(expected_product)
        and _normalize_identity(publisher) == _normalize_identity(expected_publisher)
    )


def _is_reparse(path: Path) -> bool:
    """Inspect a path without resolving through a symlink or junction."""

    try:
        if path.is_symlink():
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return True


def _absolute_path(value: object) -> Path | None:
    if isinstance(value, Path):
        raw = str(value).strip()
    elif isinstance(value, str):
        raw = value.strip()
    else:
        return None
    if not raw:
        return None
    try:
        expanded = os.path.expanduser(os.path.expandvars(raw))
        if _UNRESOLVED_CONFIG.search(expanded):
            return None
        if not os.path.isabs(expanded):
            return None
        return Path(os.path.abspath(expanded))
    except (OSError, ValueError):
        return None


def _safe_existing_path(path: Path, *, kind: str) -> tuple[Path | None, str]:
    """Validate every existing path component before accepting a leaf."""

    try:
        candidate = Path(os.path.abspath(str(path)))
        if not candidate.is_absolute():
            return None, "relative_target"
        current = Path(candidate.anchor) if candidate.anchor else Path()
        for part in candidate.parts:
            if candidate.anchor and part == candidate.anchor:
                continue
            current = current / part
            if current.exists() or current.is_symlink():
                if _is_reparse(current):
                    return None, "reparse_target"
        if kind == "file":
            if not candidate.exists():
                return None, "target_missing"
            info = os.lstat(candidate)
            if not stat.S_ISREG(info.st_mode):
                return None, "target_not_regular"
            if _is_reparse(candidate):
                return None, "reparse_target"
        elif kind == "dir":
            if not candidate.exists():
                return None, "working_directory_missing"
            info = os.lstat(candidate)
            if not stat.S_ISDIR(info.st_mode):
                return None, "working_directory_not_directory"
            if _is_reparse(candidate):
                return None, "reparse_target"
        else:
            return None, "invalid_target_kind"
        return candidate, ""
    except (OSError, RuntimeError, ValueError):
        return None, "target_unavailable"


def _validated_executable(value: object) -> tuple[Path | None, str]:
    path = _absolute_path(value)
    if path is None:
        return None, "invalid_executable"
    if path.suffix.casefold() != ".exe":
        return None, "script_target"
    return _safe_existing_path(path, kind="file")


def _validated_working_directory(value: object, fallback: Path) -> tuple[Path | None, str]:
    if value in (None, ""):
        return _safe_existing_path(fallback, kind="dir")
    path = _absolute_path(value)
    if path is None:
        return None, "invalid_working_directory"
    return _safe_existing_path(path, kind="dir")


def _allowed_arguments(value: object) -> tuple[tuple[str, ...] | None, str]:
    if value in (None, ""):
        return (), ""
    if not isinstance(value, list) or len(value) > _MAX_ARGUMENTS:
        return None, "invalid_arguments"
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > _MAX_ARGUMENT_LENGTH or "\x00" in item or "\r" in item or "\n" in item:
            return None, "invalid_arguments"
        result.append(item)
    return tuple(result), ""


def _read_file_identity(path: Path) -> dict[str, str] | None:
    """Read Windows version-resource identity without spawning a shell."""

    if os.name != "nt":
        return None
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        size_handle = ctypes.c_uint32(0)
        get_size = version.GetFileVersionInfoSizeW
        get_size.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
        get_size.restype = ctypes.c_uint32
        size = int(get_size(str(path), ctypes.byref(size_handle)))
        if size <= 0 or size > 16 * 1024 * 1024:
            return None
        buffer = (ctypes.c_ubyte * size)()
        get_info = version.GetFileVersionInfoW
        get_info.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        get_info.restype = ctypes.c_int
        if not get_info(str(path), 0, size, ctypes.byref(buffer)):
            return None
        query = version.VerQueryValueW
        query.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint32)]
        query.restype = ctypes.c_int

        translations_ptr = ctypes.c_void_p()
        translations_length = ctypes.c_uint32(0)
        translations = []
        if query(ctypes.byref(buffer), r"\VarFileInfo\Translation", ctypes.byref(translations_ptr), ctypes.byref(translations_length)):
            word_count = int(translations_length.value) // ctypes.sizeof(ctypes.c_uint16)
            if translations_ptr.value and word_count >= 2:
                words = ctypes.cast(translations_ptr, ctypes.POINTER(ctypes.c_uint16))
                for index in range(0, min(word_count - 1, 8), 2):
                    translations.append((int(words[index]), int(words[index + 1])))
        if not translations:
            translations = [(0x0409, 0x04B0), (0x0409, 0x04E4), (0, 0)]

        def query_string(field: str) -> str:
            for language, codepage in translations:
                pointer = ctypes.c_void_p()
                length = ctypes.c_uint32(0)
                subpath = rf"\StringFileInfo\{language:04x}{codepage:04x}\{field}"
                if query(ctypes.byref(buffer), subpath, ctypes.byref(pointer), ctypes.byref(length)) and pointer.value:
                    return str(ctypes.cast(pointer, ctypes.c_wchar_p).value or "").strip()
            return ""

        product = query_string("ProductName")
        company = query_string("CompanyName")
        if not product or not company:
            return None
        return {"product_name": product, "publisher": company}
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _file_fingerprint(path: Path) -> str | None:
    """Hash a bounded executable and reject a change during the read."""

    try:
        before = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_size < 0 or before.st_size > _MAX_EXECUTABLE_BYTES or _is_reparse(path):
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = os.stat(path, follow_symlinks=False)
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or _is_reparse(path):
            return None
        return digest.hexdigest()
    except (OSError, ValueError):
        return None


def _candidate_from_path(
    executable: object,
    *,
    working_directory: object = None,
    arguments: object = None,
    source: str,
    expected_product: str,
    expected_publisher: str,
) -> tuple[_Candidate | None, str]:
    safe_executable, code = _validated_executable(executable)
    if safe_executable is None:
        return None, code
    safe_working_directory, code = _validated_working_directory(working_directory, safe_executable.parent)
    if safe_working_directory is None:
        return None, code
    safe_arguments, code = _allowed_arguments(arguments)
    if safe_arguments is None:
        return None, code
    identity = _read_file_identity(safe_executable)
    if not _identity_matches(identity, expected_product, expected_publisher):
        return None, "identity_not_verified"
    fingerprint = _file_fingerprint(safe_executable)
    if not fingerprint:
        return None, "fingerprint_unavailable"
    return _Candidate(
        executable=safe_executable,
        working_directory=safe_working_directory,
        arguments=safe_arguments,
        fingerprint=fingerprint,
        source=source,
        expected_product=expected_product,
        expected_publisher=expected_publisher,
    ), ""


def _candidate_from_entry(entry: Mapping[str, Any], *, source: str) -> tuple[_Candidate | None, str]:
    application_id = str(entry.get("id") or "")
    if not APPLICATION_ID.fullmatch(application_id):
        return None, "invalid_application_id"
    if entry.get("launch") is not True:
        return None, "launch_disabled"
    expected = _expected_identity(entry, application_id)
    if expected is None:
        return None, "identity_not_allowlisted"
    return _candidate_from_path(
        entry.get("executable"),
        working_directory=entry.get("working_directory"),
        arguments=entry.get("arguments"),
        source=source,
        expected_product=expected[0],
        expected_publisher=expected[1],
    )


def _deduplicate_candidates(candidates: list[_Candidate]) -> tuple[_Candidate, ...]:
    result: list[_Candidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        # Registry views and installer records may spell one physical target
        # through different path aliases (for example a short Windows name).
        # The path has already passed the no-reparse check, so realpath here
        # only canonicalizes that same verified file; it does not discover a
        # new target. Distinct files remain candidates even if their bytes are
        # identical, because the launcher must not guess between them.
        key = os.path.normcase(os.path.realpath(str(candidate.executable)))
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return tuple(result)


def _discovery_from_candidates(
    candidates: list[_Candidate],
    *,
    source: str,
    unavailable_code: str,
    identity_found: bool = False,
) -> _Discovery:
    unique = _deduplicate_candidates(candidates)
    if len(unique) == 1:
        return _Discovery(DISCOVERY_VERIFIED, LAUNCH_AVAILABLE, "airi_verified", source, unique[0], unique, identity_found)
    if len(unique) > 1:
        return _Discovery(DISCOVERY_AMBIGUOUS, LAUNCH_AMBIGUOUS, "airi_multiple_verified_candidates", source, None, unique, identity_found)
    return _Discovery(DISCOVERY_UNAVAILABLE, LAUNCH_UNAVAILABLE, unavailable_code, source, None, (), identity_found)


def _local_candidates(entries: list[dict[str, Any]], application_id: str) -> tuple[list[_Candidate], str]:
    candidates: list[_Candidate] = []
    first_error = "application_target_unavailable"
    for entry in entries:
        if str(entry.get("id") or "") != application_id:
            continue
        candidate, code = _candidate_from_entry(entry, source="local_registry")
        if candidate is not None:
            candidates.append(candidate)
        elif first_error == "application_target_unavailable":
            first_error = code or first_error
    return candidates, first_error


def _parse_registry_executable(value: object) -> str | None:
    """Parse only the exact executable token supplied by a registry value."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        if end <= 1:
            return None
        text = text[1:end]
    elif "," in text:
        text = text.split(",", 1)[0].strip()
    return text.strip() if text.strip().casefold().endswith(".exe") else None


def _registry_text(key: Any, name: str = "") -> str:
    try:
        import winreg

        value = winreg.QueryValueEx(key, name)[0]
    except (ImportError, OSError, TypeError):
        return ""
    return value.strip() if isinstance(value, str) else ""


def _registry_subkeys(root: Any, path: str, access: int, *, limit: int = 512) -> list[tuple[str, Any]]:
    """Enumerate one bounded registry branch and close every handle."""

    try:
        import winreg

        parent = winreg.OpenKey(root, path, 0, access)
    except (ImportError, OSError, TypeError):
        return []
    result: list[tuple[str, Any]] = []
    try:
        for index in range(limit):
            try:
                name = winreg.EnumKey(parent, index)
                child = winreg.OpenKey(parent, name, 0, access)
            except OSError:
                break
            result.append((name, child))
    finally:
        try:
            parent.Close()
        except OSError:
            pass
    return result


def _path_is_under(child: object, parent: object) -> bool:
    child_path = _absolute_path(child)
    parent_path = _absolute_path(parent)
    if child_path is None or parent_path is None:
        return False
    safe_parent, parent_code = _safe_existing_path(parent_path, kind="dir")
    if safe_parent is None or parent_code:
        return False
    child_text = os.path.normcase(os.path.abspath(str(child_path)))
    parent_text = os.path.normcase(os.path.abspath(str(safe_parent))).rstrip("\\/")
    return child_text == parent_text or child_text.startswith(parent_text + os.sep)


def _windows_candidate_descriptors() -> tuple[list[dict[str, Any]], bool]:
    """Return bounded exact registry targets, never guessed filenames."""

    if os.name != "nt":
        return [], False
    try:
        import winreg

        read_access = winreg.KEY_READ
        views = [0]
        for view_name in ("KEY_WOW64_64KEY", "KEY_WOW64_32KEY"):
            view = getattr(winreg, view_name, 0)
            if view and view not in views:
                views.append(view)
        roots = [(winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")]
    except (ImportError, AttributeError):
        return [], False

    installers: list[dict[str, str]] = []
    identity_found = False
    for root, root_name in roots:
        for view in views:
            for key_name, key in _registry_subkeys(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", read_access | view):
                try:
                    product = _registry_text(key, "DisplayName")
                    publisher = _registry_text(key, "Publisher")
                    if _normalize_identity(product) != _normalize_identity(AIRI_PRODUCT_NAME) or _normalize_identity(publisher) != _normalize_identity(AIRI_PUBLISHER):
                        continue
                    identity_found = True
                    installers.append({
                        "root": root_name,
                        "key": key_name,
                        "install_location": _registry_text(key, "InstallLocation"),
                        "display_icon": _registry_text(key, "DisplayIcon"),
                    })
                finally:
                    try:
                        key.Close()
                    except OSError:
                        pass

    descriptors: list[dict[str, Any]] = []
    for installer in installers:
        display_icon = _parse_registry_executable(installer.get("display_icon"))
        if display_icon:
            descriptors.append({
                "executable": display_icon,
                "working_directory": None,
                "arguments": [],
                "source": "windows_uninstall",
            })

    app_paths_roots = (
        r"Software\Microsoft\Windows\CurrentVersion\App Paths",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
    )
    for root, _root_name in roots:
        for view in views:
            for app_paths_root in app_paths_roots:
                for key_name, key in _registry_subkeys(root, app_paths_root, read_access | view):
                    try:
                        executable = _parse_registry_executable(_registry_text(key))
                        if not executable:
                            continue
                        direct_identity = (
                            _normalize_identity(_registry_text(key, "DisplayName")) == _normalize_identity(AIRI_PRODUCT_NAME)
                            and _normalize_identity(_registry_text(key, "Publisher") or _registry_text(key, "CompanyName")) == _normalize_identity(AIRI_PUBLISHER)
                        )
                        tied_to_installer = any(
                            _path_is_under(executable, installer.get("install_location"))
                            for installer in installers
                            if installer.get("install_location")
                        )
                        if not direct_identity and not tied_to_installer:
                            continue
                        descriptors.append({
                            "executable": executable,
                            "working_directory": _registry_text(key, "Path") or None,
                            "arguments": [],
                            "source": "windows_app_paths",
                        })
                    finally:
                        try:
                            key.Close()
                        except OSError:
                            pass
    return descriptors, identity_found


def _discover_windows_candidates() -> tuple[list[_Candidate], str, bool]:
    descriptors, identity_found = _windows_candidate_descriptors()
    candidates: list[_Candidate] = []
    for descriptor in descriptors:
        candidate, _code = _candidate_from_path(
            descriptor.get("executable"),
            working_directory=descriptor.get("working_directory"),
            arguments=descriptor.get("arguments"),
            source=str(descriptor.get("source") or "windows_registry"),
            expected_product=AIRI_PRODUCT_NAME,
            expected_publisher=AIRI_PUBLISHER,
        )
        if candidate is not None:
            candidates.append(candidate)
    reason = "airi_installer_identity_target_missing" if identity_found else "airi_not_discovered"
    return list(_deduplicate_candidates(candidates)), reason, identity_found


def _discover_application(
    application_id: str,
    *,
    state: Mapping[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> tuple[_Discovery, str, dict[str, Any] | None]:
    if not APPLICATION_ID.fullmatch(application_id):
        return _Discovery(DISCOVERY_UNAVAILABLE, LAUNCH_UNAVAILABLE, "application_invalid_id", "none"), "missing", None
    state = _read_registry_state() if state is None else state
    entries = _registry_entries(state) if entries is None else entries
    provenance = str(state.get("provenance") or "missing")
    valid = bool(state.get("valid"))
    matching_entries = [entry for entry in entries if str(entry.get("id") or "") == application_id]

    if application_id == AIRI_APPLICATION_ID:
        if provenance == "malformed_local" and str(state.get("error") or "") in {
            "reparse_config_root",
            "reparse_target",
            "config_target_outside_root",
            "invalid_config_target",
        }:
            discovery = _Discovery(DISCOVERY_UNAVAILABLE, LAUNCH_UNAVAILABLE, "airi_local_registry_invalid", "local_registry")
            return discovery, provenance, matching_entries[0] if matching_entries else None
        if provenance == "local" and not valid:
            discovery = _Discovery(DISCOVERY_UNAVAILABLE, LAUNCH_UNAVAILABLE, "airi_local_registry_invalid", "local_registry")
            return discovery, provenance, matching_entries[0] if matching_entries else None
        if provenance == "local" and matching_entries:
            candidates, first_error = _local_candidates(matching_entries, application_id)
            discovery = _discovery_from_candidates(
                candidates,
                source="local_registry",
                unavailable_code=f"airi_local_{first_error}",
            )
            return discovery, provenance, matching_entries[0]
        candidates, unavailable_code, identity_found = _discover_windows_candidates()
        discovery = _discovery_from_candidates(
            candidates,
            source="windows_registry",
            unavailable_code=unavailable_code,
            identity_found=identity_found,
        )
        return discovery, provenance, matching_entries[0] if matching_entries else None

    if not valid or provenance != "local":
        discovery = _Discovery(DISCOVERY_UNAVAILABLE, LAUNCH_UNAVAILABLE, "application_registry_not_local", "local_registry")
        return discovery, provenance, matching_entries[0] if matching_entries else None
    candidates: list[_Candidate] = []
    first_error = "target_unavailable"
    for entry in matching_entries:
        candidate, code = _candidate_from_entry(entry, source="local_registry")
        if candidate is not None:
            candidates.append(candidate)
        elif first_error == "target_unavailable":
            first_error = code or first_error
    discovery = _discovery_from_candidates(
        candidates,
        source="local_registry",
        unavailable_code=f"application_{first_error}",
    )
    return discovery, provenance, matching_entries[0] if matching_entries else None


def _running_executables(*, force: bool = False) -> set[str]:
    """Return executable names observed by Windows without accepting input."""

    global _tasklist_cache
    if os.name != "nt":
        return set()
    now = time.monotonic()
    with _tasklist_lock:
        if not force and _tasklist_cache and now - _tasklist_cache[0] < _TASKLIST_CACHE_SECONDS:
            return set(_tasklist_cache[1])
    try:
        result = run_hidden(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except OSError:
        return set()
    names: set[str] = set()
    for line in result.stdout.splitlines():
        if not line.startswith('"'):
            continue
        name = line.split('",', 1)[0].strip('"').casefold()
        if name:
            names.add(name)
    with _tasklist_lock:
        _tasklist_cache = (now, set(names))
    return names


def _candidate_running(candidate: _Candidate, *, force: bool = False) -> bool:
    return candidate.executable.name.casefold() in _running_executables(force=force)


def _safe_public_label(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())[:96]
    if not text or "/" in text or "\\" in text or _LOCAL_PATH_MARKER.search(text) or _SENSITIVE_MARKER.search(text):
        return fallback
    return text


def _public_airi_entry(discovery: _Discovery, *, provenance: str, force: bool = False) -> dict[str, Any]:
    running = bool(discovery.candidate and _candidate_running(discovery.candidate, force=force))
    component_status = "running" if running else "installed" if discovery.candidate else "unavailable"
    public_provenance = discovery.source if discovery.candidate is not None or discovery.candidates else provenance
    if discovery.state == DISCOVERY_AMBIGUOUS:
        notes = "Nhiều launcher AIRI hợp lệ; Hub không tự chọn candidate."
    elif discovery.candidate:
        notes = "AIRI do installer Windows quản lý; Hub chỉ dùng launcher đã xác minh."
    elif discovery.identity_found:
        notes = "Đã thấy identity installer AIRI nhưng chưa có executable hợp lệ để mở."
    else:
        notes = "Chưa tìm thấy AIRI qua registry local hoặc Windows installer identity."
    return {
        "id": AIRI_APPLICATION_ID,
        "display_name": AIRI_DISPLAY_NAME,
        "category": "assistant",
        "classification": "SYSTEM_INSTALLED_APP",
        "management_status": "external_system_app",
        "component_status": component_status,
        "status": component_status,
        "launchable": discovery.candidate is not None,
        "running": running,
        "discovery_state": discovery.state,
        "launch_state": discovery.launch_state,
        "reason_code": discovery.reason_code,
        "registry_provenance": public_provenance,
        "discovery_source": discovery.source,
        "managed_location": "External managed",
        "execution": "not_run",
        "notes": notes,
    }


def _public_entry(entry: Mapping[str, Any], discovery: _Discovery, *, provenance: str, force: bool = False) -> dict[str, Any]:
    application_id = str(entry.get("id") or "")
    display_name = _safe_public_label(entry.get("display_name"), application_id or "Application")
    running = bool(discovery.candidate and _candidate_running(discovery.candidate, force=force))
    component_status = "running" if running else "installed" if discovery.candidate else "unavailable"
    return {
        "id": application_id,
        "display_name": display_name,
        "category": _safe_public_label(entry.get("category"), "application"),
        "classification": _safe_public_label(entry.get("classification"), "UNKNOWN"),
        "management_status": "configured_allowlist" if provenance == "local" else provenance,
        "component_status": component_status,
        "status": component_status,
        "launchable": discovery.candidate is not None,
        "running": running,
        "discovery_state": discovery.state,
        "launch_state": discovery.launch_state,
        "reason_code": discovery.reason_code,
        "registry_provenance": provenance,
        "discovery_source": discovery.source,
        "managed_location": "LocalAIHub" if str(entry.get("classification") or "").upper() == "PORTABLE_APP" else "External managed",
        "execution": "not_run",
        "notes": "Ứng dụng chỉ được mở bằng target allowlist đã xác minh." if discovery.candidate else "Ứng dụng chưa có target allowlist đã xác minh.",
    }


def applications(*, force: bool = False) -> list[dict[str, Any]]:
    """Return a finite path-free projection of the managed application list."""

    state = _read_registry_state()
    provenance = str(state.get("provenance") or "missing")
    entries = _registry_entries(state)
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in entries:
        application_id = str(entry.get("id") or "")
        if not APPLICATION_ID.fullmatch(application_id) or application_id in seen_ids:
            continue
        seen_ids.add(application_id)
        discovery, _entry_provenance, _matching = _discover_application(
            application_id,
            state=state,
            entries=entries,
        )
        if application_id == AIRI_APPLICATION_ID:
            result.append(_public_airi_entry(discovery, provenance=provenance, force=force))
        else:
            result.append(_public_entry(entry, discovery, provenance=provenance, force=force))

    if AIRI_APPLICATION_ID not in seen_ids:
        discovery, _entry_provenance, _matching = _discover_application(
            AIRI_APPLICATION_ID,
            state=state,
            entries=entries,
        )
        result.append(_public_airi_entry(discovery, provenance=provenance, force=force))
    return result


def application(application_id: str, *, force: bool = False) -> dict[str, Any] | None:
    """Return one sanitized application projection."""

    if not isinstance(application_id, str) or not APPLICATION_ID.fullmatch(application_id):
        return None
    return next((item for item in applications(force=force) if item.get("id") == application_id), None)


def _entry_by_id(application_id: str) -> dict[str, Any] | None:
    """Legacy internal lookup; launch never trusts its values without revalidation."""

    if not isinstance(application_id, str) or not APPLICATION_ID.fullmatch(application_id):
        return None
    state = _read_registry_state()
    for entry in _registry_entries(state):
        if entry.get("id") == application_id:
            return entry
    return None


def _revalidate_candidate(candidate: _Candidate) -> _Candidate | None:
    refreshed, _code = _candidate_from_path(
        candidate.executable,
        working_directory=candidate.working_directory,
        arguments=list(candidate.arguments),
        source=candidate.source,
        expected_product=candidate.expected_product,
        expected_publisher=candidate.expected_publisher,
    )
    if refreshed is None or refreshed.fingerprint != candidate.fingerprint:
        return None
    return refreshed


def launch(application_id: str) -> tuple[int, dict[str, Any]]:
    """Launch only a freshly reverified target resolved from an application ID."""

    if not isinstance(application_id, str) or not APPLICATION_ID.fullmatch(application_id):
        return 404, {"status": "error", "application": "unknown", "error": "application_unknown"}
    state = _read_registry_state()
    entries = _registry_entries(state)
    discovery, _provenance, _matching = _discover_application(application_id, state=state, entries=entries)
    if discovery.state == DISCOVERY_AMBIGUOUS:
        return 409, {
            "status": "ambiguous",
            "application": application_id,
            "error": "application_ambiguous",
            "reason_code": discovery.reason_code,
            "execution": "not_run",
        }
    if discovery.candidate is None:
        return 503, {
            "status": "unavailable",
            "application": application_id,
            "error": "application_unavailable",
            "reason_code": discovery.reason_code,
            "execution": "not_run",
        }
    candidate = _revalidate_candidate(discovery.candidate)
    if candidate is None:
        return 409, {
            "status": "error",
            "application": application_id,
            "error": "application_candidate_changed",
            "reason_code": "application_candidate_changed",
            "execution": "not_run",
        }
    try:
        process = popen_hidden(
            [str(candidate.executable), *candidate.arguments],
            cwd=candidate.working_directory,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid = int(getattr(process, "pid"))
    except (OSError, TypeError, ValueError, AttributeError):
        return 500, {
            "status": "error",
            "application": application_id,
            "error": "application_launch_failed",
            "reason_code": "application_launch_failed",
            "execution": "attempted",
        }
    return 202, {
        "status": "launching",
        "application": application_id,
        "pid": pid,
        "message": "Ứng dụng allowlist đã được xác minh và đang khởi chạy.",
    }


__all__ = [
    "AIRI_APPLICATION_ID",
    "AIRI_DISPLAY_NAME",
    "APPLICATION_REGISTRY_SCHEMA_VERSION",
    "application",
    "applications",
    "launch",
]
