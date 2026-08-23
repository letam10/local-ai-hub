"""Controlled real-loopback V8 API and artifact streaming acceptance.

The test binds the production HubHTTPServer/HubHandler only to 127.0.0.1:8765
with an injected V8-only context and task-owned artifact roots.  It never
starts the normal API process, provider workloads or a browser.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services import artifact_store
from src.services.api import api_server
from src.services.api.context import ApiContext
from src.services.component_enablement_v8 import ComponentEnablementService
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.v8_output_bridge import V8OutputBridge
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


@unittest.skipUnless(os.name == "nt", "V8 loopback acceptance is Windows-only")
class V8LoopbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        # Never contend with the user's production/default listener. The
        # production application remains 127.0.0.1:8765; this integration
        # fixture owns an ephemeral loopback port and passes it to every
        # request/cleanup helper.
        self.port = self._free_test_port()
        self._port_env = patch.dict(os.environ, {"LOCALAIHUB_PORT": str(self.port)}, clear=False)
        self._port_env.start()
        self.addCleanup(self._port_env.stop)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()
        self.paths.output_root.mkdir(parents=True)
        self.index = self.paths.config_root / "artifacts.json"
        self.index.parent.mkdir(parents=True)
        self.bridge = V8OutputBridge(
            ProductionOutputAuthority(
                paths=self.paths,
                store=V8ProductionTransactionStore(self.paths.config_root / "v8_control.sqlite3"),
            )
        )
        self.previous_context = api_server._api_context_cache
        self.previous_router = api_server._api_router
        self.server: api_server.HubHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _free_test_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port == 8765:
            return V8LoopbackApiTests._free_test_port()
        return port

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)
        api_server._api_context_cache = self.previous_context
        api_server._api_router = self.previous_router
        self.temp.cleanup()

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        result = response.status, {key.lower(): value for key, value in response.getheaders()}, body
        connection.close()
        return result

    def _wait_for_port_free(self) -> bool:
        for _ in range(25):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) != 0:
                    return True
            time.sleep(0.05)
        return False

    def _start(self, *, operations: list[dict[str, object]]) -> None:
        acceptance = ComponentEnablementService()
        api_server._api_context_cache = ApiContext(
            {
                "component_operations": lambda *, limit: {"status": "completed", "operations": operations[:limit]},
                "component_operation": lambda operation_id: next(
                    (item for item in operations if item.get("operation_id") == operation_id), None
                ),
                "component_confirm_operation": lambda operation_id, confirmed=False: {
                    "status": "waiting_confirmation" if not confirmed else "completed",
                    "operation_id": operation_id,
                    "execution": "not_run" if not confirmed else "completed",
                },
                "component_cancel_operation": lambda operation_id: {
                    "status": "cancelled",
                    "operation_id": operation_id,
                    "execution": "not_run",
                },
                "component_source_acceptance_snapshot": acceptance.snapshot,
                "component_source_acceptance": acceptance.assess,
            }
        )
        api_server._api_router = None
        self.server = api_server.HubHTTPServer(("127.0.0.1", self.port), api_server.HubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def _stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None
        self.assertTrue(self._wait_for_port_free())

    def test_v8_and_legacy_artifacts_stream_over_restartable_loopback(self) -> None:
        self.assertNotEqual(self.port, 8765)
        job_id = "jobv5_" + "a" * 32
        v8_payload = b"V8-opaque-artifact"
        v8 = self.bridge.publish_bytes(
            job_id=job_id,
            content=v8_payload,
            name="v8.bin",
            media_type="application/octet-stream",
            provenance={
                "job_id": job_id,
                "job_spec_fingerprint": "b" * 64,
                "adapter_id": "test.loopback.v8",
                "attempt": 1,
                "status": "completed",
            },
        )
        self.assertIsNotNone(v8)
        assert v8 is not None
        legacy_path = self.paths.output_root / "legacy.bin"
        legacy_payload = b"V7-historical-artifact"
        legacy_path.write_bytes(legacy_payload)

        with (
            patch.object(artifact_store, "OUTPUT_ROOT", self.paths.output_root),
            patch.object(artifact_store, "INDEX_PATH", self.index),
        ):
            legacy = artifact_store.register_path(legacy_path, media_type="application/octet-stream")
            self.assertIsNotNone(legacy)
            assert legacy is not None

            def resolve(artifact_id: str) -> Path | None:
                return self.bridge.resolve(artifact_id) or artifact_store.resolve(artifact_id)

            def describe(artifact_id: str) -> dict[str, object] | None:
                return self.bridge.describe(artifact_id) or artifact_store.describe(artifact_id)

            operation_id = "compop_" + "c" * 32
            operations = [{"operation_id": operation_id, "state": "planned", "execution": "not_run"}]
            with patch.object(api_server, "resolve_artifact", resolve), patch.object(api_server, "describe_artifact", describe):
                self._start(operations=operations)
                status, _headers, body = self._request("GET", "/api/components/operations?limit=12")
                self.assertEqual(status, 200)
                payload = json.loads(body.decode("utf-8"))
                self.assertEqual(payload["operations"][0]["operation_id"], operation_id)
                self.assertNotIn(str(self.paths.output_root), json.dumps(payload))

                v8_id = str(v8["id"])
                status, headers, body = self._request("GET", f"/api/artifacts/{v8_id}")
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-length"], str(len(v8_payload)))
                self.assertEqual(body, v8_payload)
                status, headers, body = self._request("HEAD", f"/api/artifacts/{v8_id}")
                self.assertEqual(status, 200)
                self.assertEqual(headers["content-length"], str(len(v8_payload)))
                self.assertEqual(body, b"")
                status, headers, body = self._request("GET", f"/api/artifacts/{v8_id}", headers={"Range": "bytes=3-8"})
                self.assertEqual(status, 206)
                self.assertEqual(headers["content-range"], f"bytes 3-8/{len(v8_payload)}")
                self.assertEqual(body, v8_payload[3:9])

                legacy_id = str(legacy["id"])
                status, _headers, body = self._request("GET", f"/api/artifacts/{legacy_id}")
                self.assertEqual(status, 200)
                self.assertEqual(body, legacy_payload)

                concurrent: list[bytes] = []
                failures: list[BaseException] = []

                def read_v8() -> None:
                    try:
                        read_status, _read_headers, read_body = self._request("GET", f"/api/artifacts/{v8_id}")
                        if read_status != 200:
                            raise AssertionError(read_status)
                        concurrent.append(read_body)
                    except BaseException as exc:  # pragma: no cover - asserted after joins
                        failures.append(exc)

                readers = [threading.Thread(target=read_v8) for _ in range(3)]
                for reader in readers:
                    reader.start()
                for reader in readers:
                    reader.join(3)
                self.assertEqual(failures, [])
                self.assertEqual(concurrent, [v8_payload, v8_payload, v8_payload])

                self._stop()
                self._start(operations=operations)
                status, _headers, body = self._request("GET", f"/api/artifacts/{v8_id}")
                self.assertEqual(status, 200)
                self.assertEqual(body, v8_payload)


if __name__ == "__main__":
    unittest.main()
