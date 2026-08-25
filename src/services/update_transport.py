"""Authenticated transports for the private-repository main updater.

The updater must not make ``gh`` the only authentication path.  This module
keeps the transport boundary small and testable: a native GitHub Device Flow
transport is preferred when a repository-owned OAuth client id is configured,
the already-authenticated GitHub CLI is a safe fallback, and an explicit
configuration/unavailable state is returned when neither path is usable.

Tokens are kept in Windows Credential Manager when available.  They are never
placed in repository files, Config, logs, UI payloads, subprocess arguments or
diagnostic text.  The Device Flow client id is configuration, never a secret,
and is intentionally not hard-coded in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPOSITORY = "letam10/local-ai-hub"
GITHUB_HOST = "github.com"
GITHUB_API = "https://api.github.com"
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
CREDENTIAL_TARGET = "LocalAIHub/github/oauth"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DEVICE_WAIT_SECONDS = 15 * 60
DEFAULT_DEVICE_INTERVAL_SECONDS = 5


class TransportError(RuntimeError):
    """Fixed-code, non-secret transport refusal."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


class CredentialStore(Protocol):
    def read(self, target: str) -> str | None: ...
    def write(self, target: str, value: str) -> bool: ...
    def delete(self, target: str) -> bool: ...


class WindowsCredentialStore:
    """Minimal Windows Credential Manager adapter with no plaintext fallback."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise TransportError("CREDENTIAL_STORE_UNAVAILABLE")
        self._advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    def read(self, target: str) -> str | None:
        credential = ctypes.c_void_p()
        read = self._advapi.CredReadW
        read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        read.restype = wintypes.BOOL
        if not read(target, 1, 0, ctypes.byref(credential)):
            return None
        try:
            pointer = ctypes.cast(credential, ctypes.POINTER(_CREDENTIAL)).contents
            if not pointer.CredentialBlob or pointer.CredentialBlobSize <= 0:
                return None
            raw = ctypes.string_at(pointer.CredentialBlob, pointer.CredentialBlobSize)
            return raw.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return None
        finally:
            self._advapi.CredFree(credential)

    def write(self, target: str, value: str) -> bool:
        if not value or "\x00" in value:
            return False
        raw = value.encode("utf-8")
        buffer = ctypes.create_string_buffer(raw)
        credential = _CREDENTIAL()
        credential.Type = 1
        credential.TargetName = target
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE
        write = self._advapi.CredWriteW
        write.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
        write.restype = wintypes.BOOL
        return bool(write(ctypes.byref(credential), 0))

    def delete(self, target: str) -> bool:
        delete = self._advapi.CredDeleteW
        delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        delete.restype = wintypes.BOOL
        return bool(delete(target, 1, 0))


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class MemoryCredentialStore:
    """Test-only store; production selection never uses it implicitly."""

    def __init__(self, value: str | None = None) -> None:
        self.value = value

    def read(self, target: str) -> str | None:
        return self.value

    def write(self, target: str, value: str) -> bool:
        self.value = value
        return True

    def delete(self, target: str) -> bool:
        self.value = None
        return True


def _credential_store() -> CredentialStore | None:
    try:
        return WindowsCredentialStore()
    except TransportError:
        return None


@dataclass(frozen=True)
class AuthState:
    status: str
    transport: str
    code: str | None = None
    action: str = ""

    def public(self) -> dict[str, str]:
        value = {"status": self.status, "transport": self.transport, "action": self.action}
        if self.code:
            value["code"] = self.code
        return value


class GitHubTransport(Protocol):
    name: str

    def auth_state(self) -> AuthState: ...
    def api_json(self, endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]: ...
    def download_artifact(self, run_id: int, destination: Path, artifact_name: str) -> None: ...
    def begin_device_login(self) -> dict[str, Any]: ...
    def logout(self) -> dict[str, Any]: ...


Runner = Callable[..., subprocess.CompletedProcess[str]]


class GhCliTransport:
    name = "github_cli"

    def __init__(self, *, runner: Runner | None = None, gh_path: str | None = None) -> None:
        self._runner = runner or subprocess.run
        self._gh_path = gh_path

    def _gh(self) -> str:
        candidate = self._gh_path or os.environ.get("LOCALAIHUB_GH_CLI") or shutil.which("gh")
        if not candidate:
            raise TransportError("GITHUB_CLI_REQUIRED")
        path = Path(candidate).expanduser()
        if not path.is_file() or path.is_symlink():
            raise TransportError("GITHUB_CLI_INVALID")
        return str(path)

    def _run(self, args: list[str], *, timeout: float = 25.0) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                [self._gh(), *args], text=True, capture_output=True,
                timeout=timeout, check=False,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
                env=dict(os.environ),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransportError("GITHUB_CLI_FAILED", type(exc).__name__) from exc

    def auth_state(self) -> AuthState:
        try:
            result = self._run(["auth", "status", "--hostname", GITHUB_HOST], timeout=10.0)
        except TransportError as exc:
            return AuthState("unavailable", self.name, exc.code, "Cài GitHub CLI hoặc cấu hình native GitHub login.")
        if result.returncode == 0:
            return AuthState("ready", self.name, action="GitHub CLI đã xác thực an toàn.")
        return AuthState("auth_required", self.name, "GITHUB_AUTH_REQUIRED", "Đăng nhập GitHub CLI để kiểm tra repo private.")

    def api_json(self, endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
        args = ["api", "--method", "GET", endpoint]
        for key, value in (fields or {}).items():
            args.extend(["-f", f"{key}={value}"])
        result = self._run(args)
        if result.returncode != 0:
            raise TransportError("GITHUB_API_FAILED")
        if len(result.stdout.encode("utf-8", "replace")) > MAX_RESPONSE_BYTES:
            raise TransportError("GITHUB_RESPONSE_TOO_LARGE")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise TransportError("GITHUB_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise TransportError("GITHUB_RESPONSE_INVALID")
        return value

    def download_artifact(self, run_id: int, destination: Path, artifact_name: str) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        result = self._run(["run", "download", str(run_id), "--repo", REPOSITORY, "--name", artifact_name, "--dir", str(destination)], timeout=180.0)
        if result.returncode != 0:
            raise TransportError("UPDATE_DOWNLOAD_FAILED")

    def begin_device_login(self) -> dict[str, Any]:
        return AuthState("oauth_configuration_required", self.name, "OAUTH_CONFIGURATION_REQUIRED", "GitHub OAuth App client_id chưa được cấu hình.").public()

    def logout(self) -> dict[str, Any]:
        return {"status": "unavailable", "code": "GITHUB_CLI_LOGOUT_NOT_OWNED", "transport": self.name}


class GitHubDeviceFlowTransport:
    name = "github_oauth_device"

    def __init__(self, *, client_id: str | None = None, store: CredentialStore | None = None, opener: Callable[..., Any] = urlopen, clock: Callable[[], float] = time.monotonic) -> None:
        self.client_id = client_id or os.environ.get("LOCALAIHUB_GITHUB_OAUTH_CLIENT_ID")
        self.store = store if store is not None else _credential_store()
        self._opener = opener
        self._clock = clock

    def configured(self) -> bool:
        return isinstance(self.client_id, str) and 8 <= len(self.client_id) <= 128 and "\x00" not in self.client_id

    def _request(self, url: str, *, data: dict[str, str] | None = None, token: str | None = None) -> dict[str, Any]:
        body = urlencode(data or {}).encode("ascii") if data is not None else None
        headers = {"Accept": "application/json", "User-Agent": "LocalAIHub-updater"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
        try:
            with self._opener(request, timeout=15.0) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, HTTPError, URLError) as exc:
            raise TransportError("OAUTH_NETWORK_FAILED", type(exc).__name__) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError("OAUTH_RESPONSE_TOO_LARGE")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError("OAUTH_RESPONSE_INVALID") from exc
        if not isinstance(value, dict):
            raise TransportError("OAUTH_RESPONSE_INVALID")
        return value

    def _token(self) -> str | None:
        return self.store.read(CREDENTIAL_TARGET) if self.store is not None else None

    def _validate_token(self, token: str) -> bool:
        if not token or len(token) > 512 or any(char.isspace() for char in token):
            return False
        identity = self._request(f"{GITHUB_API}/user", token=token)
        if not isinstance(identity.get("login"), str) or not identity.get("login"):
            return False
        repository = self._request(f"{GITHUB_API}/repos/{REPOSITORY}", token=token)
        return repository.get("full_name") == REPOSITORY

    def auth_state(self) -> AuthState:
        if not self.configured():
            return AuthState("oauth_configuration_required", self.name, "OAUTH_CONFIGURATION_REQUIRED", "Cần GitHub OAuth App client_id do chủ repo cung cấp.")
        if self.store is None:
            return AuthState("auth_required", self.name, "CREDENTIAL_STORE_UNAVAILABLE", "Windows Credential Manager chưa khả dụng; không lưu token plaintext.")
        token = self._token()
        if not token:
            return AuthState("auth_required", self.name, "OAUTH_LOGIN_REQUIRED", "Bấm Đăng nhập GitHub để dùng Device Flow.")
        try:
            valid = self._validate_token(token)
        except TransportError:
            valid = False
        if not valid:
            self.store.delete(CREDENTIAL_TARGET)
            return AuthState("auth_required", self.name, "OAUTH_TOKEN_INVALID", "Phiên GitHub hết hạn hoặc không có quyền đọc repo/artifact.")
        return AuthState("ready", self.name, action="GitHub OAuth đã xác thực qua Credential Manager.")

    def api_json(self, endpoint: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
        state = self.auth_state()
        if state.status != "ready":
            raise TransportError(state.code or "OAUTH_AUTH_REQUIRED")
        token = self._token()
        url = f"{GITHUB_API}/{endpoint.lstrip('/')}"
        if fields:
            url += "?" + urlencode(fields)
        return self._request(url, token=token)

    def download_artifact(self, run_id: int, destination: Path, artifact_name: str) -> None:
        state = self.auth_state()
        if state.status != "ready":
            raise TransportError(state.code or "OAUTH_AUTH_REQUIRED")
        # Artifact downloads are streamed only after the metadata endpoint has
        # confirmed the exact run/name.  The public service keeps the bounded
        # ZIP/hash validation after this transport returns.
        token = self._token()
        metadata = self._request(f"{GITHUB_API}/repos/{REPOSITORY}/actions/runs/{run_id}/artifacts", token=token)
        rows = metadata.get("artifacts") if isinstance(metadata.get("artifacts"), list) else []
        artifact = next((row for row in rows if isinstance(row, dict) and row.get("name") == artifact_name and row.get("expired") is not True), None)
        download_url = artifact.get("archive_download_url") if isinstance(artifact, dict) else None
        if not isinstance(download_url, str) or not download_url.startswith("https://api.github.com/"):
            raise TransportError("UPDATE_ARTIFACT_NOT_FOUND")
        destination.mkdir(parents=True, exist_ok=False)
        request = Request(download_url, headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}", "User-Agent": "LocalAIHub-updater"})
        try:
            with self._opener(request, timeout=180.0) as response:
                target = destination / f"{artifact_name}.zip"
                with target.open("xb") as sink:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        sink.write(chunk)
        except (OSError, HTTPError, URLError) as exc:
            raise TransportError("UPDATE_DOWNLOAD_FAILED", type(exc).__name__) from exc

    def begin_device_login(self) -> dict[str, Any]:
        if not self.configured():
            return AuthState("oauth_configuration_required", self.name, "OAUTH_CONFIGURATION_REQUIRED", "Cần GitHub OAuth App client_id do chủ repo cung cấp.").public()
        response = self._request(GITHUB_DEVICE_CODE_URL, data={"client_id": self.client_id or "", "scope": "repo"})
        required = ("device_code", "user_code", "verification_uri", "expires_in")
        if any(not isinstance(response.get(key), (str, int)) for key in required):
            raise TransportError("OAUTH_DEVICE_RESPONSE_INVALID")
        return {
            "status": "device_login_required",
            "transport": self.name,
            "verification_uri": str(response["verification_uri"]),
            "user_code": str(response["user_code"]),
            "expires_in": max(60, min(900, int(response["expires_in"]))),
            "interval": max(2, min(15, int(response.get("interval", DEFAULT_DEVICE_INTERVAL_SECONDS)))),
        }

    def poll_device_login(self, device_code: str, *, interval_seconds: int = DEFAULT_DEVICE_INTERVAL_SECONDS, expires_in: int = 900, cancel: Callable[[], bool] | None = None) -> dict[str, Any]:
        if not self.configured() or not isinstance(device_code, str) or not device_code or len(device_code) > 512:
            raise TransportError("OAUTH_DEVICE_CODE_INVALID")
        deadline = self._clock() + min(MAX_DEVICE_WAIT_SECONDS, max(60, int(expires_in)))
        interval = max(2, min(15, int(interval_seconds)))
        while self._clock() < deadline:
            if cancel and cancel():
                return {"status": "cancelled", "transport": self.name}
            response = self._request(GITHUB_ACCESS_TOKEN_URL, data={"client_id": self.client_id or "", "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            token = response.get("access_token")
            if isinstance(token, str) and token and self.store is not None and self._validate_token(token):
                if not self.store.write(CREDENTIAL_TARGET, token):
                    raise TransportError("CREDENTIAL_STORE_WRITE_FAILED")
                return {"status": "authenticated", "transport": self.name}
            error = response.get("error")
            if error in {"authorization_pending", "slow_down"}:
                time.sleep(interval + (2 if error == "slow_down" else 0))
                continue
            if error in {"expired_token", "access_denied"}:
                return {"status": "auth_required", "transport": self.name, "code": "OAUTH_" + str(error).upper()}
            raise TransportError("OAUTH_TOKEN_RESPONSE_INVALID")
        return {"status": "auth_required", "transport": self.name, "code": "OAUTH_DEVICE_TIMEOUT"}

    def logout(self) -> dict[str, Any]:
        if self.store is None:
            return {"status": "unavailable", "transport": self.name, "code": "CREDENTIAL_STORE_UNAVAILABLE"}
        return {"status": "logged_out" if self.store.delete(CREDENTIAL_TARGET) else "unavailable", "transport": self.name}


class TransportSelector:
    """Select native authenticated transport, then gh fallback, fail closed."""

    def __init__(self, *, runner: Runner | None = None, gh_path: str | None = None, native: GitHubDeviceFlowTransport | None = None) -> None:
        self.native = native or GitHubDeviceFlowTransport()
        self.gh = GhCliTransport(runner=runner, gh_path=gh_path)

    def select(self) -> tuple[GitHubTransport, AuthState]:
        native_state = self.native.auth_state()
        if native_state.status == "ready":
            return self.native, native_state
        gh_state = self.gh.auth_state()
        if gh_state.status == "ready":
            return self.gh, gh_state
        if native_state.status == "oauth_configuration_required":
            return self.native, native_state
        if native_state.status in {"auth_required", "unavailable"}:
            return self.native, native_state
        return self.gh, gh_state


def build_transport(*, runner: Runner | None = None, gh_path: str | None = None) -> TransportSelector:
    return TransportSelector(runner=runner, gh_path=gh_path)


__all__ = [
    "AuthState", "CREDENTIAL_TARGET", "GitHubDeviceFlowTransport", "GhCliTransport",
    "MemoryCredentialStore", "TransportError", "TransportSelector", "build_transport",
]
