from __future__ import annotations

import hashlib
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.services import artifact_store
from src.services.api import api_server, core
from src.services.api.api_server import HubHTTPServer, HubHandler


class BoundedStream:
    """A fake request body that records every requested read size."""

    def __init__(self, body: bytes, *, stop_after: int | None = None) -> None:
        self._body = body
        self._offset = 0
        self._stop_after = stop_after
        self.requested: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested.append(size)
        if self._stop_after is not None and self._offset >= self._stop_after:
            return b""
        limit = len(self._body) if size < 0 else min(len(self._body), self._offset + size)
        if self._stop_after is not None:
            limit = min(limit, self._stop_after)
        value = self._body[self._offset : limit]
        self._offset += len(value)
        return value


class ArtifactTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.upload_root = root / "Temp" / "uploads"
        self.output_root = root / "Output"
        self.archive_root = root / "Archive"
        self.index_path = root / "Config" / "artifacts.json"
        self.patches = [
            patch.object(artifact_store, "UPLOAD_ROOT", self.upload_root),
            patch.object(artifact_store, "OUTPUT_ROOT", self.output_root),
            patch.object(artifact_store, "ARCHIVE_ROOT", self.archive_root),
            patch.object(artifact_store, "INDEX_PATH", self.index_path),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)

    def _server(self) -> tuple[HubHTTPServer, threading.Thread]:
        server = HubHTTPServer(("127.0.0.1", 0), HubHandler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()

        def stop() -> None:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)
        return server, thread

    @staticmethod
    def _response(url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        request = urllib.request.Request(url, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, dict(response.headers.items()), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers.items()), error.read()

    def test_artifact_download_streams_full_and_single_ranges_without_read_bytes(self) -> None:
        self.output_root.mkdir(parents=True)
        expected = bytes(range(64)) + b"Local AI Hub range test"
        path = self.output_root / "synthetic.bin"
        path.write_bytes(expected)
        artifact = artifact_store.register_path(path, name='synthetic"safe.bin', media_type="application/octet-stream")
        self.assertIsNotNone(artifact)
        server, _thread = self._server()
        url = f"http://127.0.0.1:{server.server_address[1]}{artifact['url']}"

        with patch.object(Path, "read_bytes", side_effect=AssertionError("artifact response must stream")):
            status, headers, body = self._response(url)
            self.assertEqual((status, body), (200, expected))
            self.assertEqual(headers["Accept-Ranges"], "bytes")
            self.assertEqual(headers["Content-Length"], str(len(expected)))
            self.assertIn('filename="synthetic_safe.bin"', headers["Content-Disposition"])

            status, headers, body = self._response(url, headers={"Range": "bytes=4-11"})
            self.assertEqual((status, body), (206, expected[4:12]))
            self.assertEqual(headers["Content-Range"], f"bytes 4-11/{len(expected)}")
            self.assertEqual(headers["Content-Length"], "8")

            status, headers, body = self._response(url, headers={"Range": "bytes=10-"})
            self.assertEqual((status, body), (206, expected[10:]))
            self.assertEqual(headers["Content-Range"], f"bytes 10-{len(expected) - 1}/{len(expected)}")

            status, headers, body = self._response(url, headers={"Range": "bytes=-7"})
            self.assertEqual((status, body), (206, expected[-7:]))

            status, headers, body = self._response(url, method="HEAD", headers={"Range": "bytes=0-2"})
            self.assertEqual((status, body), (206, b""))
            self.assertEqual(headers["Content-Range"], f"bytes 0-2/{len(expected)}")

            status, headers, body = self._response(url, headers={"Range": "bytes=0-1,4-5"})
            self.assertEqual((status, body), (416, b""))
            self.assertEqual(headers["Content-Range"], f"bytes */{len(expected)}")

            status, headers, body = self._response(url, headers={"Range": "bytes=999-"})
            self.assertEqual((status, body), (416, b""))

    def test_artifact_disconnect_during_headers_or_body_is_absorbed_by_the_handler(self) -> None:
        self.output_root.mkdir(parents=True)
        path = self.output_root / "disconnect.bin"
        path.write_bytes(b"fixture")

        headers_handler = object.__new__(HubHandler)
        headers_handler.headers = {}
        headers_handler.send_response = lambda _status: None
        headers_handler.send_header = lambda _name, _value: None
        headers_handler.end_headers = lambda: (_ for _ in ()).throw(BrokenPipeError())
        headers_handler._write_file(path, "disconnect.bin", "application/octet-stream")

        class BrokenWriter:
            def write(self, _chunk: bytes) -> int:
                raise ConnectionResetError()

        body_handler = object.__new__(HubHandler)
        body_handler.headers = {}
        body_handler.wfile = BrokenWriter()
        body_handler.send_response = lambda _status: None
        body_handler.send_header = lambda _name, _value: None
        body_handler.end_headers = lambda: None
        body_handler._write_file(path, "disconnect.bin", "application/octet-stream")

    def test_upload_stream_is_bounded_hashed_and_atomically_registered(self) -> None:
        body = (b"local-ai-hub-upload-" * 800) + b"end"
        stream = BoundedStream(body)
        artifact = artifact_store.stage_upload_stream(
            "report.bin",
            stream,
            len(body),
            "application/x-test; charset=utf-8",
            max_bytes=len(body) + 1,
            disk_safety_bytes=0,
            chunk_bytes=1024,
        )
        self.assertTrue(stream.requested)
        self.assertTrue(all(size <= 1024 for size in stream.requested))
        self.assertEqual(artifact["size_bytes"], len(body))
        self.assertEqual(artifact["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(artifact["media_type"], "application/x-test")
        staged = artifact_store.resolve(artifact["id"])
        self.assertIsNotNone(staged)
        self.assertEqual(staged.read_bytes(), body)
        self.assertEqual(list(self.upload_root.glob("*.part")), [])
        stored = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.assertEqual(stored[artifact["id"]]["sha256"], hashlib.sha256(body).hexdigest())

    def test_upload_rejects_oversize_and_cleans_a_short_part_file(self) -> None:
        with self.assertRaises(artifact_store.UploadError):
            artifact_store.stage_upload_stream(
                "too-large.bin",
                BoundedStream(b"abc"),
                4,
                max_bytes=3,
                disk_safety_bytes=0,
                chunk_bytes=1024,
            )
        with self.assertRaises(artifact_store.UploadError):
            artifact_store.stage_upload_stream(
                "short.bin",
                BoundedStream(b"abcdef", stop_after=3),
                6,
                max_bytes=10,
                disk_safety_bytes=0,
                chunk_bytes=1024,
            )
        self.assertFalse(self.upload_root.exists() and any(self.upload_root.iterdir()))

    def test_upload_handler_passes_request_stream_without_buffering_the_body(self) -> None:
        """The HTTP handler delegates the live stream; staging owns bounded reads."""

        body = b"bounded-handler-upload"
        stream = BoundedStream(body)
        handler = object.__new__(HubHandler)
        handler.headers = {
            "Content-Length": str(len(body)),
            "Content-Type": "application/x-test",
            "X-File-Name": "handler.bin",
        }
        handler.rfile = stream
        replies: list[tuple[int, dict]] = []
        handler._write = lambda status, payload: replies.append((status, payload))
        staged: dict[str, object] = {}

        def stage(filename: str, incoming: object, expected_size: int, content_type: str | None, **kwargs: object) -> dict[str, object]:
            staged.update({
                "filename": filename,
                "stream": incoming,
                "expected_size": expected_size,
                "content_type": content_type,
                "kwargs": kwargs,
            })
            return {"id": "artifact_" + "a" * 32, "name": "handler.bin", "size_bytes": expected_size}

        with (
            patch.object(api_server, "_upload_limits", return_value=(1024, 0)),
            patch.object(api_server, "stage_upload_stream", side_effect=stage),
        ):
            handler._upload()

        self.assertIs(staged["stream"], stream)
        self.assertEqual(staged["expected_size"], len(body))
        self.assertEqual(staged["kwargs"], {"max_bytes": 1024, "disk_safety_bytes": 0})
        self.assertEqual(stream.requested, [])
        self.assertEqual(replies[0][0], 201)
        self.assertEqual(replies[0][1]["artifact"]["id"], "artifact_" + "a" * 32)

    def test_upload_handler_rejects_missing_or_invalid_content_length_before_staging(self) -> None:
        for headers, expected_status in (({}, 411), ({"Content-Length": "not-a-number"}, 400), ({"Content-Length": "0"}, 400)):
            with self.subTest(headers=headers):
                handler = object.__new__(HubHandler)
                handler.headers = headers
                handler.rfile = BoundedStream(b"ignored")
                replies: list[tuple[int, dict]] = []
                handler._write = lambda status, payload: replies.append((status, payload))
                with patch.object(api_server, "stage_upload_stream") as stage:
                    handler._upload()
                stage.assert_not_called()
                self.assertEqual(replies[0][0], expected_status)

    def test_jobs_http_listing_defaults_to_200_and_clamps_requested_history_to_500(self) -> None:
        server, _thread = self._server()
        root = f"http://127.0.0.1:{server.server_address[1]}/api/jobs"
        with patch.object(api_server, "list_jobs", return_value=[]) as list_jobs:
            status, _headers, body = self._response(root)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["jobs"], [])
            list_jobs.assert_called_once_with(limit=200)
            list_jobs.reset_mock()
            status, _headers, body = self._response(root + "?limit=999")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["jobs"], [])
            list_jobs.assert_called_once_with(limit=500)


class _FakeTimer:
    def __init__(self, delay: float, callback: object) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class JobPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        from src.services.api import jobs

        self.jobs = jobs
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        self.path_patch = patch.object(jobs, "JOBS_PATH", root / "Config" / "jobs.json")
        self.archive_patch = patch.object(jobs, "ARCHIVE_JOBS_ROOT", root / "Archive" / "Jobs")
        self.path_patch.start()
        self.archive_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.archive_patch.stop)
        with jobs._lock:
            self.snapshot = dict(jobs._jobs)
            jobs._jobs.clear()

        def restore() -> None:
            jobs.flush()
            with jobs._lock:
                jobs._jobs.clear()
                jobs._jobs.update(self.snapshot)

        self.addCleanup(restore)
        self.addCleanup(self.temp.cleanup)

    def test_progress_writer_debounces_but_flushes_terminal_or_shutdown(self) -> None:
        from src.services.api.jobs import CoalescingWriter

        now = [0.0]
        writes: list[float] = []
        timers: list[_FakeTimer] = []

        def timer_factory(delay: float, callback: object) -> _FakeTimer:
            timer = _FakeTimer(delay, callback)
            timers.append(timer)
            return timer

        writer = CoalescingWriter(lambda: writes.append(now[0]), clock=lambda: now[0], debounce_seconds=0.5, timer_factory=timer_factory)  # type: ignore[arg-type]
        writer.request()
        now[0] = 0.1
        writer.request()
        now[0] = 0.2
        writer.request()
        self.assertEqual(writes, [0.0])
        self.assertEqual(len(timers), 1)
        self.assertTrue(timers[0].started)
        self.assertAlmostEqual(timers[0].delay, 0.4)
        writer.flush()
        self.assertEqual(writes, [0.0, 0.2])
        self.assertTrue(timers[0].cancelled)

    def test_restart_reconciles_active_jobs_and_hot_history_archives_overflow(self) -> None:
        jobs = self.jobs
        active = {
            "id": "job_active",
            "contract_version": "job.v2",
            "tool": "probe_media",
            "status": "running",
            "input": {"path": r"D:\private\input.mp4"},
            "resume_data": {"asset_id": "artifact_" + "a" * 32},
            "resume_available": True,
            "created_at": "2026-08-10T00:00:00+00:00",
        }
        jobs.JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        jobs.JOBS_PATH.write_text(json.dumps({"job_active": active}), encoding="utf-8")
        jobs._load()
        jobs.reconcile_startup()
        public = jobs.get_job("job_active")
        self.assertIsNotNone(public)
        self.assertEqual(public["status"], "interrupted")
        self.assertIn("Tạo lại", public["next_action"])
        durable = json.loads(jobs.JOBS_PATH.read_text(encoding="utf-8"))["job_active"]
        self.assertNotIn("input", durable)
        self.assertNotIn("resume_data", durable)
        self.assertNotIn("resume_available", durable)

        with jobs._lock:
            for index in range(jobs.HOT_TERMINAL_LIMIT + 2):
                job_id = f"job_old_{index}"
                jobs._jobs[job_id] = {
                    "id": job_id,
                    "contract_version": "job.v2",
                    "tool": "probe_media",
                    "status": "completed",
                    "created_at": f"2026-08-09T00:00:{index:02d}+00:00",
                    "finished_at": f"2026-08-09T00:00:{index:02d}+00:00",
                }
            jobs._save_to_disk()
            active_count = sum(1 for item in jobs._jobs.values() if item.get("status") in jobs.ACTIVE_STATUSES)
            terminal_count = sum(1 for item in jobs._jobs.values() if item.get("status") in jobs.TERMINAL_STATUSES)
        self.assertEqual(active_count, 0)
        self.assertEqual(terminal_count, jobs.HOT_TERMINAL_LIMIT)
        archive = next(jobs.ARCHIVE_JOBS_ROOT.glob("jobs-*.jsonl"))
        self.assertEqual(len(archive.read_text(encoding="utf-8").splitlines()), 3)  # active interrupted + two oldest completed
        self.assertLessEqual(len(jobs.list_jobs(limit=999)), jobs.HOT_TERMINAL_LIMIT)

    def test_archive_rotation_and_retention_bound_cold_history(self) -> None:
        jobs = self.jobs
        records = [
            {
                "id": f"job_archive_{index}",
                "contract_version": "job.v2",
                "tool": "probe_media",
                "status": "completed",
                "created_at": "2026-08-10T00:00:00+00:00",
                "finished_at": f"2026-08-10T00:00:0{index}+00:00",
            }
            for index in range(3)
        ]
        with (
            patch.object(jobs, "ARCHIVE_ROTATE_BYTES", 1),
            patch.object(jobs, "ARCHIVE_MAX_FILES", 2),
        ):
            for record in records:
                self.assertTrue(jobs._archive_terminal_records([record]))
        archives = sorted(jobs.ARCHIVE_JOBS_ROOT.glob("jobs-*.jsonl"))
        self.assertEqual(len(archives), 2)
        contents = "".join(path.read_text(encoding="utf-8") for path in archives)
        self.assertIn("job_archive_2", contents)


class NodeStateBoundTests(unittest.TestCase):
    def test_node_cache_is_lru_and_never_deletes_artifact_files(self) -> None:
        from src.services.node_studio.engine import NodeCache

        cache = NodeCache(max_entries=2)
        cache.put("first", {"value": 1})
        cache.put("second", {"value": 2})
        self.assertEqual(cache.get("first"), {"value": 1})  # refresh first
        cache.put("third", {"value": 3})
        self.assertIsNone(cache.get("second"))
        self.assertEqual(cache.get("first"), {"value": 1})
        self.assertEqual(cache.get("third"), {"value": 3})

    def test_graph_registry_keeps_all_active_and_recent_terminal_runs(self) -> None:
        from src.services.node_studio.state import GraphRunRegistry, MAX_TERMINAL_RUNS

        registry = GraphRunRegistry()
        graph = {"id": "bounded", "nodes": [{"id": "node", "type": "load_image"}]}
        for index in range(MAX_TERMINAL_RUNS + 3):
            job_id = f"terminal_{index}"
            registry.begin(job_id, graph)
            registry.finish(job_id, status="completed")
        registry.begin("active_queued", graph)
        registry.begin("active_running", graph)
        registry.update_node("active_running", "node", status="running", progress=10)
        self.assertIsNone(registry.snapshot("terminal_0"))
        self.assertIsNotNone(registry.snapshot(f"terminal_{MAX_TERMINAL_RUNS + 2}"))
        self.assertEqual(registry.snapshot("active_queued")["status"], "queued")
        self.assertEqual(registry.snapshot("active_running")["status"], "running")


class JobShutdownTests(unittest.TestCase):
    def test_queued_job_cancellation_sets_context_and_timeout_never_forces_exit(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        context = manager_module.JobContext("job_queued")
        manager._contexts["job_queued"] = context
        record = {"id": "job_queued", "status": "queued"}
        updates: list[dict] = []
        with (
            patch.object(manager_module, "get_job_internal", return_value=record),
            patch.object(manager_module, "update_job", side_effect=lambda _id, **values: updates.append(values)),
            patch.object(manager_module, "active_jobs", return_value=[record]),
        ):
            ok, _message = manager.cancel("job_queued")
            self.assertTrue(ok)
            self.assertTrue(context.cancelled)
            self.assertTrue(any(item.get("status") == "cancelling" for item in updates))
            complete, message = manager.cancel_all_and_wait(0.01)
        self.assertFalse(complete)
        self.assertIn("vẫn giữ cửa sổ mở", message)
        self.assertIn("job_queued", message)

    def test_terminal_interrupted_job_cannot_reenter_cancelling(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        record = {"id": "job_interrupted", "status": "interrupted"}
        with (
            patch.object(manager_module, "get_job_internal", return_value=record),
            patch.object(manager_module, "update_job") as update,
        ):
            ok, message = manager.cancel("job_interrupted")
        self.assertFalse(ok)
        self.assertIn("kết thúc", message)
        update.assert_not_called()

    def test_cancel_rechecks_terminal_state_before_mutating_a_racing_completed_job(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        current = [{"id": "job_finish_race", "status": "queued"}]
        result: list[bool] = []
        lookup_called = threading.Event()

        def lookup(_job_id: str) -> dict[str, str]:
            lookup_called.set()
            return dict(current[0])

        with (
            patch.object(manager_module, "get_job_internal", side_effect=lookup),
            patch.object(manager_module, "update_job") as update,
        ):
            # Hold the context lock while the worker changes its durable state.
            # A pre-lock snapshot would still see queued and regress it; the
            # implementation must fetch after this hand-off instead.
            manager._lock.acquire()
            try:
                thread = threading.Thread(target=lambda: result.append(manager.cancel("job_finish_race")[0]), daemon=True)
                thread.start()
                # Older code performs lookup before acquiring this lock; make
                # that stale queued snapshot deterministic before changing the
                # durable record. Current code waits for the lock first.
                lookup_called.wait(0.2)
                current[0] = {"id": "job_finish_race", "status": "completed"}
            finally:
                manager._lock.release()
            thread.join(timeout=1)
        self.assertEqual(result, [False])
        update.assert_not_called()

    def test_cancel_between_durable_queue_record_and_context_install_cancels_future_context(self) -> None:
        """The hand-off tombstone prevents a queued worker from slipping past cancel."""

        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        record = {"id": "job_queue_race", "status": "queued", "tool": "probe_media", "resume_data": {}}
        updates: list[dict[str, object]] = []
        runner_called = threading.Event()

        class InlineThread:
            def __init__(self, *, target: object, args: tuple[object, ...], **_kwargs: object) -> None:
                self._target = target
                self._args = args

            def start(self) -> None:
                self._target(*self._args)  # type: ignore[operator]

        def create_before_context(*_args: object, **_kwargs: object) -> dict[str, object]:
            ok, _message = manager.cancel(record["id"])
            self.assertTrue(ok)
            return dict(record)

        with (
            patch.object(manager_module, "create_job", side_effect=create_before_context),
            patch.object(manager_module, "get_job_internal", return_value=record),
            patch.object(manager_module, "update_job", side_effect=lambda _id, **values: updates.append(values)),
            patch.object(manager_module.threading, "Thread", InlineThread),
        ):
            manager.submit("probe_media", {}, lambda _payload, _context: runner_called.set() or {"status": "completed"}, heavy=False)

        self.assertFalse(runner_called.is_set())
        self.assertTrue(any(item.get("status") == "cancelled" for item in updates))
        self.assertEqual(manager._contexts, {})
        self.assertEqual(manager._pending_cancellations, set())

    def test_public_retry_contract_excludes_completed_and_interrupted(self) -> None:
        from src.services.api.jobs import public_job

        for status in ("completed", "interrupted"):
            with self.subTest(status=status):
                value = public_job({
                    "id": f"job_{status}",
                    "tool": "probe_media",
                    "status": status,
                    "resume_data": {"asset_id": "artifact_" + "a" * 32},
                    "resume_available": True,
                })
                self.assertFalse(value["resumable"])
        interrupted = public_job({"id": "job_stale", "tool": "probe_media", "status": "interrupted"})
        self.assertIn("tạo lại", interrupted["next_action"].lower())


class SubmissionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._was_quiesced = core._submissions_quiesced
        core._submissions_quiesced = False
        self.addCleanup(self._restore_gate)

    def _restore_gate(self) -> None:
        core._submissions_quiesced = self._was_quiesced

    def test_prepare_close_quiesces_new_submission_only_after_an_atomic_empty_recheck(self) -> None:
        with patch.object(core, "active_jobs", return_value=[]):
            status, payload = core.prepare_owned_shutdown()
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ready_to_close")
        with (
            patch.object(core, "component_statuses", return_value=[]),
            patch.object(core, "_tool_readiness", return_value={"tool_status": "partial"}),
            patch.object(core, "_resolve_assets", return_value=({}, None)),
            patch.object(core.job_manager, "submit") as submit,
        ):
            submit_status, submit_payload = core.submit_tool("probe_media", {})
        self.assertEqual(submit_status, 409)
        self.assertEqual(submit_payload["status"], "closing")
        submit.assert_not_called()

    def test_prepare_close_reopens_submission_when_the_server_recheck_sees_active_work(self) -> None:
        with patch.object(core, "active_jobs", return_value=[{"id": "job_race", "status": "queued"}]):
            status, payload = core.prepare_owned_shutdown()
        self.assertEqual(status, 409)
        self.assertEqual(payload["status"], "active_jobs")
        self.assertFalse(core._submissions_quiesced)


class ProductVersionTests(unittest.TestCase):
    def test_product_version_projects_to_health_http_and_example_components(self) -> None:
        from src.services.api.api_server import HubHandler
        from src.services.api.core import health
        from src.shared.version import PRODUCT_VERSION

        self.assertEqual(PRODUCT_VERSION, "8.0.1")
        with patch("src.services.api.core.query_gpu", return_value={"available": False}):
            self.assertEqual(health(probe_gpu=False)["version"], PRODUCT_VERSION)
        self.assertEqual(HubHandler.server_version, f"LocalAIHub/{PRODUCT_VERSION}")
        examples = json.loads((Path(__file__).resolve().parents[1] / "Config" / "components.example.json").read_text(encoding="utf-8"))
        versions = {item["id"]: item["version"] for item in examples["components"]}
        self.assertEqual(versions["local_ai_api"], PRODUCT_VERSION)
        self.assertEqual(versions["local_ai_mcp"], PRODUCT_VERSION)
        extension_examples = json.loads((Path(__file__).resolve().parents[1] / "Config" / "extensions.example.json").read_text(encoding="utf-8"))
        self.assertEqual(extension_examples["hub_version"], PRODUCT_VERSION)


if __name__ == "__main__":
    unittest.main()
