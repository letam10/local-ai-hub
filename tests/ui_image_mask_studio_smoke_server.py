"""Safe loopback fixture for the Image & Mask Studio browser smoke.

The fixture serves the tracked static UI and a deterministic, in-memory
projection of one Image & Mask Studio session.  It does not import or start
the production API, jobs, model runtimes, FFmpeg, ComfyUI, media processing or
GPU work.  The only image it serves is a tiny PNG assembled in memory.

Run with ``--port 0 --port-file <path>`` and open
``http://127.0.0.1:<port>/ui/#/image``.  Image AI normally opens on its Quick
tab, so select ``Chỉnh sửa & Mask`` to see the prepopulated local fixture.
Call ``GET /__shutdown`` when the browser smoke is complete.
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import struct
import sys
import threading
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "src" / "ui"
PRODUCT_VERSION = "4.0.0"

SOURCE_ARTIFACT_ID = "artifact_" + "a" * 32
SESSION_ID = "studio_" + "b" * 32
SOURCE_LAYER_ID = "layer_" + "c" * 32
ADJUSTMENT_LAYER_ID = "layer_" + "d" * 32
MASK_LAYER_ID = "layer_" + "e" * 32
PROJECT_ID = "project_" + "f" * 32


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def _fixture_png() -> bytes:
    """Return a valid two-by-two PNG without reading or writing media files."""

    width, height = 2, 2
    pixels = (
        (57, 189, 248, 255), (139, 92, 246, 255),
        (16, 185, 129, 255), (245, 158, 11, 255),
    )
    rows = []
    for row in range(height):
        start = row * width
        rows.append(b"\x00" + b"".join(bytes(pixel) for pixel in pixels[start : start + width]))
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
        _png_chunk(b"IDAT", zlib.compress(b"".join(rows), level=9)),
        _png_chunk(b"IEND", b""),
    ))


FIXTURE_PNG = _fixture_png()
TIMESTAMP = "2026-08-10T00:00:00+00:00"


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


def _source_artifact() -> dict[str, object]:
    return {
        "id": SOURCE_ARTIFACT_ID,
        "name": "fixture-source.png",
        "media_type": "image/png",
        "size_bytes": len(FIXTURE_PNG),
        "sha256": "a" * 64,
        "url": f"/api/artifacts/{SOURCE_ARTIFACT_ID}",
        "available": True,
        "created_at": TIMESTAMP,
    }


def _preflight() -> dict[str, object]:
    """Truthful fixture availability: only metadata editing is operational."""

    return {
        "status": "completed",
        "contract_version": "image-mask-studio.v1",
        "capabilities": [
            {
                "id": "local_non_destructive_layers",
                "title": "Layer, mask và snapshot cục bộ",
                "status": "operational",
                "reason": "Fixture chỉ lưu metadata/vector in-memory; không gọi model hoặc ghi đè ảnh nguồn.",
                "action": "Dùng canvas fixture để kiểm tra thao tác layer, history và snapshot.",
            },
            {
                "id": "sam2_assisted_mask",
                "title": "SAM2-assisted mask",
                "status": "unavailable",
                "reason": "Fixture browser không khởi động hoặc kiểm tra SAM2/GPU.",
                "action": "Giữ thao tác mask thủ công; chạy smoke SAM2 riêng khi được ủy quyền.",
            },
            {
                "id": "image_inpaint",
                "title": "Inpaint có mask",
                "status": "unavailable",
                "reason": "Fixture không có adapter inpaint hay runtime ảnh.",
                "action": "Chỉ kiểm tra metadata/provenance trong smoke UI này.",
            },
            {
                "id": "image_outpaint",
                "title": "Outpaint canvas",
                "status": "unavailable",
                "reason": "Fixture không có adapter outpaint hay runtime ảnh.",
                "action": "Chỉ kiểm tra canvas vector cục bộ trong smoke UI này.",
            },
        ],
    }


class FixtureConflictError(ValueError):
    """A client attempted to mutate an out-of-date fixture revision."""


class FixtureStudio:
    """Thread-safe, tiny metadata-only state for one browser fixture session."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._snapshot_number = 2
        source = {
            "id": SOURCE_LAYER_ID,
            "kind": "source",
            "name": "Ảnh nguồn fixture",
            "artifact_id": SOURCE_ARTIFACT_ID,
            "visible": True,
            "opacity": 1.0,
        }
        adjustment = {
            "id": ADJUSTMENT_LAYER_ID,
            "kind": "adjustment",
            "name": "Ánh sáng nhẹ",
            "visible": True,
            "opacity": 0.4,
            "adjustment": {"kind": "exposure", "settings": {"amount": 0.15}},
        }
        mask = {
            "id": MASK_LAYER_ID,
            "kind": "mask",
            "name": "Vùng chủ thể",
            "visible": True,
            "opacity": 1.0,
            "operations": [],
        }
        self._session: dict[str, Any] = {
            "id": SESSION_ID,
            "title": "Fixture · Subject mask",
            "project_id": PROJECT_ID,
            "source_artifact_id": SOURCE_ARTIFACT_ID,
            "layers": [source, adjustment, mask],
            "active_layer_id": MASK_LAYER_ID,
            "revision": 2,
            "dirty": True,
            "autosaved_at": TIMESTAMP,
            "explicit_saved_at": TIMESTAMP,
            "created_at": TIMESTAMP,
            "updated_at": TIMESTAMP,
            "last_action": "Vẽ nét mask fixture",
            "pending_project_attach": None,
        }
        before = self._document()
        mask["operations"].append({
            "id": "operation_" + "1" * 32,
            "operation": "brush",
            "mode": "add",
            "size": 0.08,
            "strength": 1.0,
            "points": [{"x": 0.16, "y": 0.20}, {"x": 0.48, "y": 0.56}, {"x": 0.76, "y": 0.31}],
            "created_at": TIMESTAMP,
        })
        self._history = [before]
        self._redo: list[dict[str, Any]] = []
        self._snapshots = [
            self._snapshot("Tạo fixture", 1, before, serial=1),
            self._snapshot("Brush fixture", 2, self._document(), serial=2),
        ]

    @staticmethod
    def _snapshot(label: str, revision: int, document: Mapping[str, Any], *, serial: int) -> dict[str, Any]:
        return {
            "id": f"snapshot_{serial:032x}",
            "label": label,
            "revision": revision,
            "created_at": TIMESTAMP,
            "document": _clone(dict(document)),
        }

    def _document(self) -> dict[str, Any]:
        return {
            "title": self._session["title"],
            "project_id": self._session["project_id"],
            "source_artifact_id": self._session["source_artifact_id"],
            "layers": _clone(self._session["layers"]),
            "active_layer_id": self._session["active_layer_id"],
        }

    def _apply_document(self, document: Mapping[str, Any]) -> None:
        for key in ("title", "project_id", "source_artifact_id", "layers", "active_layer_id"):
            self._session[key] = _clone(document[key])

    def _push_snapshot(self, label: str) -> None:
        self._snapshot_number += 1
        self._snapshots.append(self._snapshot(label, int(self._session["revision"]), self._document(), serial=self._snapshot_number))
        del self._snapshots[:-12]

    def _record_mutation(self, before: Mapping[str, Any], label: str) -> None:
        self._history.append(_clone(dict(before)))
        del self._history[:-12]
        self._redo.clear()
        self._session["revision"] += 1
        self._session["dirty"] = True
        self._session["autosaved_at"] = TIMESTAMP
        self._session["updated_at"] = TIMESTAMP
        self._session["last_action"] = label
        self._push_snapshot(label)

    def _assert_session(self, session_id: str) -> None:
        if session_id != SESSION_ID:
            raise KeyError(session_id)

    def _assert_revision(self, payload: Mapping[str, Any]) -> None:
        revision = payload.get("base_revision")
        if revision is not None and revision != self._session["revision"]:
            raise FixtureConflictError("Fixture revision đã thay đổi; tải lại Studio rồi thử lại.")

    def _layer(self, layer_id: str, *, mask_only: bool = False) -> dict[str, Any]:
        layer = next((item for item in self._session["layers"] if item["id"] == layer_id), None)
        if not isinstance(layer, dict) or (mask_only and layer.get("kind") != "mask"):
            raise KeyError(layer_id)
        return layer

    @staticmethod
    def _public_layer(layer: Mapping[str, Any]) -> dict[str, Any]:
        result = _clone(dict(layer))
        if result.get("kind") == "mask":
            result["operation_count"] = len(result.get("operations", []))
        if result.get("artifact_id") == SOURCE_ARTIFACT_ID:
            result["artifact"] = _source_artifact()
        return result

    @staticmethod
    def _public_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        return {key: snapshot[key] for key in ("id", "label", "revision", "created_at")}

    def _public_session(self, *, detail: bool) -> dict[str, Any]:
        session = self._session
        result: dict[str, Any] = {
            "contract_version": "image-mask-studio.v1",
            "id": session["id"],
            "title": session["title"],
            "project_id": session["project_id"],
            "source_artifact_id": session["source_artifact_id"],
            "source_artifact": _source_artifact(),
            "revision": session["revision"],
            "dirty": session["dirty"],
            "autosaved_at": session["autosaved_at"],
            "explicit_saved_at": session["explicit_saved_at"],
            "last_action": session["last_action"],
            "layer_count": len(session["layers"]),
            "mask_count": sum(1 for item in session["layers"] if item.get("kind") == "mask"),
            "history": {
                "can_undo": bool(self._history),
                "can_redo": bool(self._redo),
                "undo_count": len(self._history),
                "redo_count": len(self._redo),
            },
            "snapshots": [self._public_snapshot(item) for item in self._snapshots],
            "pending_project_attach": _clone(session.get("pending_project_attach")),
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "provenance": {
                "source_artifact_id": SOURCE_ARTIFACT_ID,
                "project_id": PROJECT_ID,
                "contract": "image-mask-studio.v1",
                "revision": session["revision"],
            },
        }
        if detail:
            result["active_layer_id"] = session["active_layer_id"]
            result["layers"] = [self._public_layer(layer) for layer in session["layers"]]
        return result

    def overview(self) -> dict[str, Any]:
        with self._lock:
            summary = self._public_session(detail=False)
            return {
                "status": "completed",
                "contract_version": "image-mask-studio.v1",
                "sessions": [summary],
                "recent_sessions": [summary],
                "presets": [],
                "recovery": {
                    "status": "clean",
                    "reason": "Fixture in-memory hợp lệ; không có file local để phục hồi.",
                    "action": "Tiếp tục smoke UI hoặc gọi /__shutdown khi hoàn tất.",
                },
                "preflight": _preflight(),
            }

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            return {
                "status": "completed",
                "session": self._public_session(detail=True),
                "preflight": _preflight(),
                "recovery": {"status": "clean", "reason": "Fixture in-memory hợp lệ.", "action": "Tiếp tục smoke UI."},
            }

    def compare(self, session_id: str, query: Mapping[str, list[str]]) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            before_id = (query.get("before") or [""])[0]
            after_id = (query.get("after") or [""])[0]

            def select(identifier: str, fallback: Mapping[str, Any]) -> Mapping[str, Any]:
                if not identifier:
                    return fallback
                selected = next((item for item in self._snapshots if item["id"] == identifier), None)
                if selected is None:
                    raise KeyError(identifier)
                return selected

            before = select(before_id, self._snapshots[-2] if len(self._snapshots) > 1 else self._snapshots[-1])
            after = select(after_id, self._snapshots[-1])

            def side(snapshot: Mapping[str, Any]) -> dict[str, Any]:
                return {
                    **self._public_snapshot(snapshot),
                    "preview_artifact": _source_artifact(),
                    "layers": [self._public_layer(item) for item in snapshot["document"]["layers"]],
                }

            before_layers = side(before)["layers"]
            after_layers = side(after)["layers"]
            differences: dict[str, object] = {}
            if before_layers != after_layers:
                differences["layers"] = {"before": before_layers, "after": after_layers}
            return {
                "status": "completed",
                "compare": {
                    "contract_version": "image-mask-studio.v1",
                    "session_id": SESSION_ID,
                    "before": side(before),
                    "after": side(after),
                    "differences": differences,
                },
                "recovery": {"status": "clean", "reason": "Fixture in-memory hợp lệ.", "action": "Tiếp tục smoke UI."},
            }

    @staticmethod
    def _number(value: object, default: float, minimum: float, maximum: float) -> float:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            candidate = default
        return max(minimum, min(maximum, candidate))

    @classmethod
    def _points(cls, raw: object) -> list[dict[str, float]]:
        if not isinstance(raw, list):
            return []
        points: list[dict[str, float]] = []
        for item in raw[:160]:
            if not isinstance(item, Mapping):
                continue
            try:
                x, y = float(item.get("x")), float(item.get("y"))
            except (TypeError, ValueError):
                continue
            points.append({"x": max(0.0, min(1.0, x)), "y": max(0.0, min(1.0, y))})
        return points

    def apply_operation(self, session_id: str, layer_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            layer = self._layer(layer_id, mask_only=True)
            operation_name = payload.get("operation")
            if operation_name not in {"brush", "invert", "feather", "grow", "shrink"}:
                raise ValueError("Fixture chỉ hỗ trợ thao tác mask allowlist.")
            before = self._document()
            operation: dict[str, Any] = {
                "id": f"operation_{self._session['revision'] + 1:032x}",
                "operation": operation_name,
                "created_at": TIMESTAMP,
            }
            if operation_name == "brush":
                operation.update({
                    "mode": "subtract" if payload.get("mode") == "subtract" else "add",
                    "size": self._number(payload.get("size"), 0.06, 0.002, 1.0),
                    "strength": self._number(payload.get("strength"), 1.0, 0.01, 1.0),
                    "points": self._points(payload.get("points")),
                })
            else:
                operation["amount"] = self._number(payload.get("amount"), 0.08, 0.001, 1.0)
            layer.setdefault("operations", []).append(operation)
            del layer["operations"][:-24]
            self._session["active_layer_id"] = layer_id
            self._record_mutation(before, "Vẽ nét mask fixture" if operation_name == "brush" else f"Mask fixture: {operation_name}")
            return {"status": "completed", "session": self._public_session(detail=True), "operation": _clone(operation)}

    def undo(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            if not self._history:
                raise ValueError("Fixture không còn thao tác để hoàn tác.")
            self._redo.append(self._document())
            self._apply_document(self._history.pop())
            self._session["revision"] += 1
            self._session["dirty"] = True
            self._session["last_action"] = "Hoàn tác fixture"
            self._session["autosaved_at"] = TIMESTAMP
            self._session["updated_at"] = TIMESTAMP
            self._push_snapshot("Hoàn tác fixture")
            return {"status": "completed", "session": self._public_session(detail=True)}

    def redo(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            if not self._redo:
                raise ValueError("Fixture không còn thao tác để làm lại.")
            self._history.append(self._document())
            self._apply_document(self._redo.pop())
            self._session["revision"] += 1
            self._session["dirty"] = True
            self._session["last_action"] = "Làm lại fixture"
            self._session["autosaved_at"] = TIMESTAMP
            self._session["updated_at"] = TIMESTAMP
            self._push_snapshot("Làm lại fixture")
            return {"status": "completed", "session": self._public_session(detail=True)}

    def save(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            self._session["dirty"] = False
            self._session["last_action"] = "Lưu bản nháp fixture"
            self._session["autosaved_at"] = TIMESTAMP
            self._session["explicit_saved_at"] = TIMESTAMP
            self._session["updated_at"] = TIMESTAMP
            self._push_snapshot("Lưu bản nháp fixture")
            return {"status": "completed", "session": self._public_session(detail=True)}

    def update_layer(self, session_id: str, layer_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Small extra stub for visible/opacity controls used by the UI."""

        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            layer = self._layer(layer_id)
            before = self._document()
            if "name" in payload and isinstance(payload["name"], str):
                layer["name"] = payload["name"].strip()[:120] or layer["name"]
            if "visible" in payload:
                layer["visible"] = bool(payload["visible"])
            if "opacity" in payload:
                layer["opacity"] = self._number(payload["opacity"], float(layer.get("opacity", 1)), 0.0, 1.0)
            if payload.get("active") is True:
                self._session["active_layer_id"] = layer_id
            self._record_mutation(before, "Cập nhật layer fixture")
            return {"status": "completed", "session": self._public_session(detail=True), "layer": self._public_layer(layer)}

    def mark_pending_link(self, session_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Produce one safe 409 path for the UI recovery/pending smoke."""

        with self._lock:
            self._assert_session(session_id)
            self._assert_revision(payload)
            if payload.get("project_id") != PROJECT_ID:
                raise ValueError("Fixture project không hợp lệ.")
            self._session["pending_project_attach"] = {
                "project_id": PROJECT_ID,
                "artifacts": [SOURCE_ARTIFACT_ID],
                "revision": self._session["revision"],
                "created_at": TIMESTAMP,
            }
            return {"status": "pending_project_attach", "session": self._public_session(detail=True)}


def _health() -> dict[str, object]:
    return {
        "status": "healthy",
        "version": PRODUCT_VERSION,
        "disk": {"free_bytes": 1},
        "gpu": {},
        "active_jobs": 0,
    }


def _bootstrap() -> dict[str, object]:
    lifecycle = {"comfyui": {"status": "partial", "reason": "Not started by the browser fixture.", "action": "No runtime action is available."}}
    return {
        "health": _health(),
        "components": [],
        "applications": [],
        "jobs": [],
        "tools": [
            {"name": "generate_flux", "tool_status": "partial", "reason": "Fixture starts no image model.", "action": "No action."},
            {"name": "generate_qwen_image", "tool_status": "partial", "reason": "Fixture starts no image model.", "action": "No action."},
            {"name": "run_media_operation", "tool_status": "partial", "reason": "Fixture runs no media work.", "action": "No action."},
            {"name": "upscale_anime_video", "tool_status": "partial", "reason": "Fixture runs no video work.", "action": "No action."},
        ],
        "settings": {},
        "lifecycle": lifecycle,
    }


def _creative_overview() -> dict[str, object]:
    artifact = _source_artifact()
    project = {
        "id": PROJECT_ID,
        "title": "Fixture creative project",
        "status": "active",
        "asset_count": 1,
        "updated_at": TIMESTAMP,
        "created_at": TIMESTAMP,
    }
    return {
        "status": "completed",
        "projects": [project],
        "recent_projects": [project],
        "assets": [artifact],
        "collections": [],
        "recipes": [],
        "recovery": {"status": "clean", "reason": "Fixture in-memory hợp lệ.", "action": "Tiếp tục smoke UI."},
    }


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, SmokeHandler)
        self.studio = FixtureStudio()


class SmokeHandler(BaseHTTPRequestHandler):
    server_version = f"LocalAIHubImageMaskSmoke/{PRODUCT_VERSION}"

    @property
    def fixture(self) -> FixtureHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, _format: str, *_args: object) -> None:
        # Keep the fixture quiet: browser assertions are its test evidence.
        return

    def _write_bytes(self, status: HTTPStatus, body: bytes, *, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # A browser can abandon an image request while rerendering.
            return

    def _write_json(self, status: HTTPStatus, payload: object) -> None:
        self._write_bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), content_type="application/json; charset=utf-8")

    def _error(self, status: HTTPStatus, message: str, **extra: object) -> None:
        self._write_json(status, {"status": "error", "error": message, **extra})

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("Content-Length fixture không hợp lệ.") from error
        if length < 0 or length > 128 * 1024:
            raise ValueError("Payload fixture vượt giới hạn metadata an toàn.")
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Payload fixture phải là JSON UTF-8 hợp lệ.") from error
        if not isinstance(parsed, dict):
            raise ValueError("Payload fixture phải là JSON object.")
        return parsed

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/ui" else (path.removeprefix("/ui/") or "index.html")
        candidate = (UI_ROOT / relative).resolve()
        try:
            candidate.relative_to(UI_ROOT)
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "Static fixture không tìm thấy.")
            return
        if not candidate.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Static fixture không tìm thấy.")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix in {".js", ".mjs"}:
            content_type = "application/javascript"
        if content_type.startswith("text/") or candidate.suffix in {".js", ".mjs"}:
            content_type += "; charset=utf-8"
        self._write_bytes(HTTPStatus.OK, candidate.read_bytes(), content_type=content_type)

    def _get_api(self, path: str, query: Mapping[str, list[str]]) -> bool:
        bootstrap = _bootstrap()
        if path == "/health":
            self._write_json(HTTPStatus.OK, bootstrap["health"])
        elif path == "/api/bootstrap":
            self._write_json(HTTPStatus.OK, bootstrap)
        elif path == "/api/jobs":
            self._write_json(HTTPStatus.OK, {"jobs": []})
        elif path == "/api/lifecycle":
            self._write_json(HTTPStatus.OK, bootstrap["lifecycle"])
        elif path == "/api/comfyui/advanced":
            self._write_json(HTTPStatus.OK, {"status": "partial", "comfyui": bootstrap["lifecycle"]["comfyui"]})
        elif path == "/api/comfyui/workflows":
            self._write_json(HTTPStatus.OK, {"workflows": []})
        elif path == "/api/creative/overview":
            self._write_json(HTTPStatus.OK, _creative_overview())
        elif path == "/api/image-mask-studio/preflight":
            self._write_json(HTTPStatus.OK, _preflight())
        elif path in {"/api/image-mask-studio/overview", "/api/image-mask-studio/sessions"}:
            self._write_json(HTTPStatus.OK, self.fixture.studio.overview())
        elif path == f"/api/image-mask-studio/sessions/{SESSION_ID}":
            self._write_json(HTTPStatus.OK, self.fixture.studio.get_session(SESSION_ID))
        elif path == f"/api/image-mask-studio/sessions/{SESSION_ID}/compare":
            self._write_json(HTTPStatus.OK, self.fixture.studio.compare(SESSION_ID, query))
        elif path == f"/api/artifacts/{SOURCE_ARTIFACT_ID}":
            self._write_bytes(HTTPStatus.OK, FIXTURE_PNG, content_type="image/png")
        else:
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/__shutdown":
            self._write_json(HTTPStatus.OK, {"status": "stopping"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        try:
            if self._get_api(path, parse_qs(parsed.query, keep_blank_values=True)):
                return
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Fixture session hoặc snapshot không tìm thấy.")
            return
        if path == "/" or path == "/ui" or path.startswith("/ui/"):
            self._serve_static("/ui" if path == "/" else path)
            return
        self._error(HTTPStatus.NOT_FOUND, "Fixture route không tìm thấy.")

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        try:
            payload = self._read_json()
            base = f"/api/image-mask-studio/sessions/{SESSION_ID}"
            if path == f"{base}/layers/{MASK_LAYER_ID}/operations":
                result = self.fixture.studio.apply_operation(SESSION_ID, MASK_LAYER_ID, payload)
            elif path == f"{base}/undo":
                result = self.fixture.studio.undo(SESSION_ID, payload)
            elif path == f"{base}/redo":
                result = self.fixture.studio.redo(SESSION_ID, payload)
            elif path == f"{base}/save":
                result = self.fixture.studio.save(SESSION_ID, payload)
            elif path == f"{base}/link-project":
                result = self.fixture.studio.mark_pending_link(SESSION_ID, payload)
                self._write_json(HTTPStatus.CONFLICT, {
                    "status": "pending_project_attach",
                    "error": "Fixture project đang chờ recovery.",
                    "session": result["session"],
                    "action": "Tải lại Studio để xem intent pending.",
                })
                return
            else:
                self._error(HTTPStatus.NOT_FOUND, "Fixture mutation không được hỗ trợ.")
                return
            self._write_json(HTTPStatus.OK, result)
        except FixtureConflictError:
            self._write_json(HTTPStatus.CONFLICT, {"status": "conflict", "current_revision": self.fixture.studio.get_session(SESSION_ID)["session"]["revision"]})
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Fixture session hoặc layer không tìm thấy.")
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = unquote(urlparse(self.path).path)
        prefix = f"/api/image-mask-studio/sessions/{SESSION_ID}/layers/"
        if not path.startswith(prefix):
            self._error(HTTPStatus.NOT_FOUND, "Fixture mutation không được hỗ trợ.")
            return
        try:
            result = self.fixture.studio.update_layer(SESSION_ID, path.removeprefix(prefix), self._read_json())
            self._write_json(HTTPStatus.OK, result)
        except FixtureConflictError:
            self._write_json(HTTPStatus.CONFLICT, {"status": "conflict", "current_revision": self.fixture.studio.get_session(SESSION_ID)["session"]["revision"]})
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Fixture layer không tìm thấy.")
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))


def main() -> int:
    # The fixture is intentionally Vietnamese-facing.  Keep --help and any
    # argparse diagnostics readable on a captured Windows cp1252 console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 lets the OS choose one.")
    parser.add_argument("--port-file", type=Path, required=True, help="Write the chosen loopback port here.")
    args = parser.parse_args()
    server = FixtureHTTPServer(("127.0.0.1", args.port))
    args.port_file.parent.mkdir(parents=True, exist_ok=True)
    args.port_file.write_text(str(server.server_address[1]), encoding="ascii")
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
