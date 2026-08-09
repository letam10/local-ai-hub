"""Run FLUX and Qwen Image through the local ComfyUI HTTP API.

The Hub uses stored API workflows supplied with the current local image
runtime.  ComfyUI is a hidden, Hub-owned backend only when the Hub starts it;
an already-running external ComfyUI is observed but never stopped by Hub.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.api.config import component, hub_config
from src.services.job_manager.manager import JobContext
from src.services.process_manager.managed import background_processes
from src.shared.paths.registry import OUTPUT_ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


def _port() -> int:
    return int(hub_config().get("comfyui_port", 8188))


def _base_url() -> str:
    return f"http://127.0.0.1:{_port()}"


def _json_request(path: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10) -> dict[str, Any] | None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        _base_url() + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"} if body is not None else {"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _runtime() -> tuple[Path | None, Path | None]:
    runtime = configured_path("comfyui", "path", "COMFYUI_HOME")
    python = configured_path("comfyui", "executable", "COMFYUI_EXECUTABLE")
    if runtime is None:
        runtime = local_root() / "runtime" / "engines" / "image" / "ComfyUI"
    if python is None:
        python = runtime / "python_embeded" / "python.exe"
    main = runtime / "ComfyUI" / "main.py"
    if not main.is_file():
        candidates = list(runtime.glob("**/main.py"))
        main = next((candidate for candidate in candidates if candidate.parent.name == "ComfyUI"), main)
    return python if python.is_file() else None, main if main.is_file() else None


def _studio_root() -> Path | None:
    for component_id, variable in (("flux_klein_studio", "FLUX_STUDIO_HOME"), ("qwen_image", "QWEN_IMAGE_HOME")):
        path = configured_path(component_id, "path", variable)
        if path and path.is_dir():
            return path
    return None


def health() -> dict[str, Any]:
    response = _json_request("/system_stats", timeout=2)
    owned = background_processes.get("comfyui") is not None
    return {
        "status": "running" if response else "stopped",
        "reachable": bool(response),
        "hub_owned": owned,
        "port": _port(),
    }


def ensure_running() -> tuple[bool, str]:
    if _json_request("/system_stats", timeout=2):
        return True, "ComfyUI đã sẵn sàng."
    python, main = _runtime()
    if python is None or main is None:
        return False, "Không tìm thấy Python hoặc main.py của ComfyUI canonical."
    try:
        background_processes.start(
            "comfyui",
            [str(python), str(main), "--listen", "127.0.0.1", "--port", str(_port())],
            cwd=main.parent,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        return False, str(exc)
    deadline = time.monotonic() + float(hub_config().get("comfyui_start_timeout_seconds", 45))
    while time.monotonic() < deadline:
        if _json_request("/system_stats", timeout=2):
            return True, "ComfyUI nền đã sẵn sàng."
        time.sleep(0.5)
    background_processes.stop("comfyui")
    return False, "ComfyUI không sẵn sàng trong thời gian khởi động cho phép."


def shutdown_owned_idle() -> dict[str, Any]:
    stopped = background_processes.stop("comfyui")
    return {"status": "completed", "stopped": ["comfyui"] if stopped else []}


def _workflow_path(engine: str, has_input_image: bool) -> Path | None:
    root = _studio_root()
    if root is None:
        return None
    if engine == "qwen":
        name = "qwen_image_2512_t2i_api.json"
    elif has_input_image:
        name = "flux2_klein_i2i_base_api.json"
    else:
        name = "flux2_klein_t2i_api.json"
    path = root / "workflows" / name
    return path if path.is_file() else None


def _workflow(engine: str, request: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    image_path = request.get("input_image")
    has_input = isinstance(image_path, str) and Path(image_path).is_file()
    path = _workflow_path(engine, has_input)
    if path is None:
        return None, "Không tìm thấy API workflow cục bộ cho engine image đã chọn."
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "API workflow image không đúng định dạng JSON object."
    prompt = str(request.get("prompt") or "").strip()
    if not prompt:
        return None, "Nhập prompt trước khi tạo ảnh."
    negative = str(request.get("negative_prompt") or "")
    width = max(256, min(2048, int(request.get("width", 768))))
    height = max(256, min(2048, int(request.get("height", 768))))
    steps = max(1, min(80, int(request.get("steps", 20))))
    seed = max(0, int(request.get("seed", int(time.time() * 1000) % 2_147_483_647)))
    prefix = f"LocalAIHub/{engine}/{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if engine == "qwen":
        value["5"]["inputs"]["text"] = prompt
        value["6"]["inputs"]["text"] = negative
        value["7"]["inputs"].update({"width": width, "height": height})
        value["8"]["inputs"].update({"seed": seed, "steps": steps})
        value["10"]["inputs"]["filename_prefix"] = prefix
    else:
        value["74"]["inputs"]["text"] = prompt
        value["67"]["inputs"]["text"] = negative
        value["62"]["inputs"].update({"steps": steps, "width": width, "height": height})
        value["66"]["inputs"].update({"width": width, "height": height})
        value["73"]["inputs"]["noise_seed"] = seed
        value["9"]["inputs"]["filename_prefix"] = prefix
        if has_input:
            uploaded, error = _upload_input_image(Path(str(image_path)))
            if not uploaded:
                return None, error
            value["80"]["inputs"]["image"] = uploaded
    return value, None


def _upload_input_image(path: Path) -> tuple[str | None, str | None]:
    boundary = f"----LocalAIHub{uuid.uuid4().hex}"
    content = path.read_bytes()
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode("utf-8"),
        b"Content-Type: application/octet-stream\r\n\r\n",
        content,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        _base_url() + "/upload/image",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    name = value.get("name") if isinstance(value, dict) else None
    return (str(name), None) if isinstance(name, str) and name else (None, "ComfyUI không trả tên input image.")


def _copy_history_outputs(history: dict[str, Any], prompt_id: str, engine: str) -> list[str]:
    entry = history.get(prompt_id, {}) if isinstance(history, dict) else {}
    outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
    _python, main = _runtime()
    output_root = main.parent / "output" if main else None
    copied: list[str] = []
    if output_root is None:
        return copied
    target_root = OUTPUT_ROOT / "Image" / engine
    target_root.mkdir(parents=True, exist_ok=True)
    for node in outputs.values() if isinstance(outputs, dict) else []:
        for item in node.get("images", []) if isinstance(node, dict) else []:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            subfolder = item.get("subfolder") or ""
            if not isinstance(filename, str) or Path(filename).name != filename:
                continue
            source = (output_root / str(subfolder) / filename).resolve()
            try:
                source.relative_to(output_root.resolve())
            except (OSError, ValueError):
                continue
            if not source.is_file():
                continue
            target = target_root / f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{source.name}"
            shutil.copy2(source, target)
            copied.append(str(target))
    return copied


def generate(engine: str, request: dict[str, Any], context: JobContext | None = None) -> dict[str, Any]:
    if engine not in {"flux", "qwen"}:
        return {"status": "error", "error": "Image engine không nằm trong allowlist."}
    available, reason = ensure_running()
    if not available:
        return unavailable("comfyui", reason)
    workflow, error = _workflow(engine, request)
    if workflow is None:
        return {"status": "error", "error": error or "Không tạo được workflow image."}
    response = _json_request("/prompt", method="POST", payload={"prompt": workflow, "client_id": f"local-ai-hub-{uuid.uuid4().hex}"}, timeout=30)
    prompt_id = response.get("prompt_id") if isinstance(response, dict) else None
    if not isinstance(prompt_id, str) or not prompt_id:
        return {"status": "error", "error": (response or {}).get("error", "ComfyUI không nhận workflow.") if isinstance(response, dict) else "ComfyUI không nhận workflow."}
    deadline = time.monotonic() + float(request.get("timeout_seconds", 1800))
    while time.monotonic() < deadline:
        if context and context.cancelled:
            _json_request("/queue", method="DELETE", payload={"delete": [prompt_id]}, timeout=5)
            return {"status": "cancelled", "reason": "Đã yêu cầu ComfyUI hủy prompt do Hub tạo."}
        history = _json_request(f"/history/{prompt_id}", timeout=10)
        if history and prompt_id in history:
            outputs = _copy_history_outputs(history, prompt_id, engine)
            if outputs:
                return {"status": "completed", "operation": "generate_image", "engine": engine, "outputs": outputs, "seed": request.get("seed"), "prompt": request.get("prompt")}
            return {"status": "error", "error": "ComfyUI hoàn tất nhưng không tìm thấy image output hợp lệ."}
        if context:
            context.progress(20, "ComfyUI đang xử lý prompt image trong nền.")
        time.sleep(0.8)
    _json_request("/queue", method="DELETE", payload={"delete": [prompt_id]}, timeout=5)
    return {"status": "error", "error": "Image workflow vượt quá thời gian cho phép của Hub."}
