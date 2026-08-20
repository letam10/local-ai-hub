from __future__ import annotations

import json
import inspect
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests import v5_acceptance_ui_fixture as fixture


ROOT = Path(__file__).resolve().parents[1]


class V5AcceptanceUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), fixture.AcceptanceHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive(), "fixture thread did not stop cleanly")

    def _request(self, path: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        request = Request(self.base_url + path, method=method, headers=headers or {})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, dict(response.headers.items()), response.read()
        except HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()

    def test_fixture_bootstrap_is_deterministic_and_truthful(self) -> None:
        status, _headers, body = self._request("/api/bootstrap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual([item["id"] for item in payload["storage"]["volumes"]], ["c", "d"])
        c_volume, d_volume = payload["storage"]["volumes"]
        self.assertTrue(c_volume["low_space"])
        self.assertEqual(c_volume["total_bytes"], 100 * fixture._GIB)
        self.assertIsNone(d_volume["total_bytes"])
        self.assertEqual(d_volume["availability"], "unknown")
        self.assertNotRegex(json.dumps(payload), r"(?i)([a-z]:[\\/]|api[_-]?key|password|secret|token)")

    def test_fixture_readiness_projection_preserves_statuses_and_safe_constraints(self) -> None:
        status, _headers, body = self._request("/api/bootstrap")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        modules = payload["productization"]["capabilities"]["modules"]
        self.assertEqual({item["status"] for item in modules}, {"operational", "partial", "unavailable", "not_published", "not_run"})
        self.assertEqual(payload["productization"]["execution"], "not_run")
        self.assertTrue(payload["productization"]["dry_run"])
        resource_plan = payload["capabilities"]["module_manager"]["resource_plan"]
        self.assertEqual(set(resource_plan) & {"status", "mode", "target_gpu", "physical", "concurrent", "errors", "actions"}, {"status", "mode", "target_gpu", "physical", "concurrent", "errors", "actions"})
        self.assertIn("unknown_object", resource_plan)
        self.assertNotRegex(json.dumps(payload), r"(?i)([a-z]:[\\/]|api[_-]?key|password|secret|token)\s*[:=]")

    def test_artifact_http_fixture_supports_bounded_range_contract(self) -> None:
        artifact_id = "artifact_" + "1" * 32
        status, headers, body = self._request(f"/api/artifacts/{artifact_id}", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers["Accept-Ranges"], "bytes")
        self.assertEqual(int(headers["Content-Length"]), len(fixture.ARTIFACTS[artifact_id]["body"]))

        status, headers, body = self._request(f"/api/artifacts/{artifact_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), int(headers["Content-Length"]))

        status, headers, body = self._request(f"/api/artifacts/{artifact_id}", headers={"Range": "bytes=0-3"})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 4)
        self.assertEqual(headers["Content-Range"], f"bytes 0-3/{len(fixture.ARTIFACTS[artifact_id]['body'])}")

        for range_header in ("bytes=0-1,3-4", "bytes=999-"):
            status, headers, body = self._request(f"/api/artifacts/{artifact_id}", headers={"Range": range_header})
            self.assertEqual(status, 416)
            self.assertEqual(body, b"")
            self.assertEqual(headers["Content-Range"], f"bytes */{len(fixture.ARTIFACTS[artifact_id]['body'])}")

        self.assertNotIn("read_bytes(", inspect.getsource(fixture.AcceptanceHandler._serve_artifact))

    def test_fixture_serves_real_ui_and_all_opaque_preview_records(self) -> None:
        status, _headers, html = self._request("/ui/index.html")
        self.assertEqual(status, 200)
        self.assertIn(b"/ui/app.js", html)
        status, _headers, source = self._request("/ui/node_studio.js")
        self.assertEqual(status, 200)
        self.assertIn(b"new globalThis.LiteGraph.LGraphCanvas", source)
        self.assertEqual(len(fixture.ARTIFACTS), 5)
        self.assertEqual({item["record"]["media_type"] for item in fixture.ARTIFACTS.values()}, {"image/png", "video/mp4", "audio/wav", "application/octet-stream"})

    def test_server_artifact_preview_path_is_native_and_non_buffering(self) -> None:
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        start = app.index("const showArtifactPreview")
        end = app.index("const renderNavigation", start)
        preview = app[start:end]
        self.assertIn('preload = "metadata"', preview)
        for forbidden in ("fetch(", "arrayBuffer()", "FileReader", "Blob", "URL.createObjectURL"):
            self.assertNotIn(forbidden, preview)
        pages = (ROOT / "src" / "ui" / "pages.js").read_text(encoding="utf-8") + (ROOT / "src" / "ui" / "shared" / "rendering.js").read_text(encoding="utf-8")
        self.assertIn("data-artifact-meta", pages)
        self.assertIn("data-artifact-provenance", pages)
        self.assertIn("data-artifact-mask", pages)
        self.assertIn('available ? formatGb(volume[`${key}Bytes`]) : "\\u2014"', pages)
        self.assertNotIn("}: review storage before new writes.", pages)
        self.assertIn('not_published: "Not published"', pages)
        self.assertIn('not_run: "Not run"', pages)
        self.assertIn('ready: "Ready"', pages)
        self.assertIn('clean: "Clean"', pages)


if __name__ == "__main__":
    unittest.main()
