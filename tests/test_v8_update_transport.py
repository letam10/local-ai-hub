from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from src.services.app_update import (
    API_PROTOCOL_VERSION,
    AppUpdateError,
    UPDATE_KIND_APP_ONLY,
    UPDATE_KIND_FULL,
    _safe_extract_app_archive,
    _safe_update_contract,
)
from src.services.update_transport import (
    GitHubDeviceFlowTransport,
    MemoryCredentialStore,
    TransportSelector,
)


class V8UpdateTransportTests(unittest.TestCase):
    def _contract(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": "local-ai-hub-update-contract.v1",
            "update_kind": UPDATE_KIND_APP_ONLY,
            "app_protocol": API_PROTOCOL_VERSION,
            "runtime_contract": "reuse-current",
            "runtime_version": None,
            "runtime_hash": None,
            "minimum_launcher_version": "v8.0.1",
            "source_commit": "a" * 40,
        }
        value.update(overrides)
        return value

    def test_missing_oauth_client_id_is_explicit_and_not_a_fake_login(self) -> None:
        native = GitHubDeviceFlowTransport(client_id=None, store=MemoryCredentialStore())
        state = native.auth_state()
        self.assertEqual(state.status, "oauth_configuration_required")
        self.assertEqual(native.begin_device_login()["code"], "OAUTH_CONFIGURATION_REQUIRED")

    def test_device_flow_uses_opaque_session_and_supports_bounded_cancel(self) -> None:
        class Response:
            def __init__(self, value):
                self.value = value
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, limit=-1):
                return json.dumps(self.value).encode("utf-8")

        def opener(request, timeout=0):
            if request.full_url.endswith("/login/device/code"):
                return Response({"device_code": "private-device-code", "user_code": "ABCD-EFGH", "verification_uri": "https://github.com/login/device", "expires_in": 600, "interval": 2})
            return Response({"error": "authorization_pending"})

        native = GitHubDeviceFlowTransport(client_id="client-1234", store=MemoryCredentialStore(), opener=opener)
        started = native.begin_device_login()
        self.assertEqual(started["status"], "device_login_required")
        self.assertNotIn("device_code", started)
        self.assertTrue(started["session_id"])
        pending = native.poll_device_session(started["session_id"])
        self.assertEqual(pending["status"], "authorization_pending")
        self.assertEqual(native.cancel_device_session(started["session_id"])["status"], "cancelled")

    def test_gh_fallback_is_selected_when_native_is_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            gh = Path(temporary) / "gh.exe"
            gh.write_bytes(b"fixture")

            def runner(command, **kwargs):
                if command[1:3] == ["auth", "status"]:
                    return subprocess.CompletedProcess(command, 0, "", "")
                return subprocess.CompletedProcess(command, 0, "{}", "")

            selector = TransportSelector(runner=runner, gh_path=str(gh))
            transport, state = selector.select()
            self.assertEqual(transport.name, "github_cli")
            self.assertEqual(state.status, "ready")

    def test_contract_accepts_app_only_and_rejects_runtime_mismatch(self) -> None:
        self.assertEqual(_safe_update_contract(self._contract())["update_kind"], UPDATE_KIND_APP_ONLY)
        with self.assertRaisesRegex(AppUpdateError, "UPDATE_CONTRACT_INVALID"):
            _safe_update_contract(self._contract(update_kind=UPDATE_KIND_FULL, runtime_contract="bundled"))

    def test_full_contract_requires_runtime_hash(self) -> None:
        value = self._contract(update_kind=UPDATE_KIND_FULL, runtime_contract="bundled", runtime_version="3.12.10", runtime_hash="b" * 64)
        self.assertEqual(_safe_update_contract(value)["update_kind"], UPDATE_KIND_FULL)
        with self.assertRaisesRegex(AppUpdateError, "UPDATE_CONTRACT_INVALID"):
            _safe_update_contract({**value, "runtime_hash": None})

    def test_archive_runtime_is_only_accepted_for_full_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "payload.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("runtime/Python312/pythonw.exe", b"runtime")
                bundle.writestr("app/src/app.py", b"app")
            with self.assertRaisesRegex(AppUpdateError, "UPDATE_ARCHIVE_PATH_INVALID"):
                _safe_extract_app_archive(archive, root / "app-only", expected_files=2)
            self.assertEqual(_safe_extract_app_archive(archive, root / "full", expected_files=2, update_kind=UPDATE_KIND_FULL), 2)


if __name__ == "__main__":
    unittest.main()
