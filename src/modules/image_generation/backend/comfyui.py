"""Run FLUX and Qwen Image through the local ComfyUI HTTP API.

The Hub uses stored API workflows supplied with the current local image
runtime.  ComfyUI is a hidden, Hub-owned backend only when the Hub starts it;
an already-running external ComfyUI is observed but never stopped by Hub.
"""

from __future__ import annotations

import json
import os
import re
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
from src.shared.paths.registry import OUTPUT_ROOT, ROOT
from src.shared.utils.adapter_common import configured_path, local_root, unavailable


BRIDGE_SCHEMA_VERSION = 1
BRIDGE_WORKFLOW_ROOT = ROOT / "workflows" / "comfyui"
BRIDGE_LOCAL_ROOT = ROOT / "workflows" / "local" / "comfyui"
BRIDGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
BRIDGE_INPUTS = {"text", "image", "mask", "video", "audio", "metadata"}
_WINDOWS_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/]|\\\\)")


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
        "advanced_url": _base_url() if response else None,
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
    # A portable ComfyUI installation legitimately needs several minutes to
    # initialize CUDA and its frontend.  Do not terminate a healthy startup
    # just because the former 45-second default expired.
    timeout_seconds = max(300.0, float(hub_config().get("comfyui_start_timeout_seconds", 300)))
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _json_request("/system_stats", timeout=2):
            return True, "ComfyUI nền đã sẵn sàng."
        time.sleep(0.5)
    background_processes.stop("comfyui")
    return False, "ComfyUI không sẵn sàng trong thời gian khởi động cho phép."


def shutdown_owned_idle() -> dict[str, Any]:
    stopped = background_processes.stop("comfyui")
    return {"status": "completed", "stopped": ["comfyui"] if stopped else []}


def start_advanced() -> tuple[int, dict[str, Any]]:
    """Start ComfyUI only as a hidden backend and return its loopback iframe URL."""

    available, reason = ensure_running()
    if not available:
        return 503, {"status": "unavailable", "error": reason, "comfyui": health()}
    return 200, {"status": "completed", "url": _base_url(), "comfyui": health()}


def _bridge_path(workflow_id: str, *, local: bool = False) -> Path | None:
    if not BRIDGE_NAME.fullmatch(workflow_id):
        return None
    root = BRIDGE_LOCAL_ROOT if local else BRIDGE_WORKFLOW_ROOT
    candidate = (root / f"{workflow_id}.bridge.json").resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _has_private_path(value: object) -> bool:
    if isinstance(value, str):
        return bool(_WINDOWS_PATH.search(value) or value.startswith(("/", "~")))
    if isinstance(value, dict):
        return any(_has_private_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_private_path(item) for item in value)
    return False


def _validate_bridge_workflow(value: object, *, required_id: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "Bridge workflow phải là JSON object."
    workflow_id = str(value.get("id") or required_id or "")
    if not BRIDGE_NAME.fullmatch(workflow_id):
        return None, "ID bridge workflow không hợp lệ."
    if required_id and workflow_id != required_id:
        return None, "ID workflow không khớp tên tệp an toàn."
    if _has_private_path(value):
        return None, "Bridge workflow không được chứa đường dẫn cục bộ. Dùng binding artifact Hub."
    kind = str(value.get("kind") or "")
    title = str(value.get("title") or workflow_id).strip()[:160]
    if kind == "quick_api":
        engine = str(value.get("engine") or "")
        if engine not in {"flux", "qwen"}:
            return None, "Quick bridge chỉ hỗ trợ FLUX hoặc Qwen."
        return {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "id": workflow_id,
            "title": title,
            "kind": kind,
            "engine": engine,
            "description": str(value.get("description") or "")[:500],
            "inputs": [item for item in value.get("inputs", ["text", "image"]) if item in BRIDGE_INPUTS],
        }, None
    if kind != "raw_comfy_api":
        return None, "Bridge workflow phải là quick_api hoặc raw_comfy_api."
    prompt = value.get("prompt")
    bindings = value.get("bindings") if isinstance(value.get("bindings"), dict) else {}
    if not isinstance(prompt, dict) or not prompt:
        return None, "raw_comfy_api cần object prompt của ComfyUI."
    if len(prompt) > 600:
        return None, "Bridge prompt vượt giới hạn 600 node."
    normalized_bindings: dict[str, list[dict[str, str]]] = {}
    for name, targets in bindings.items():
        if name not in BRIDGE_INPUTS or not isinstance(targets, list):
            return None, "Binding bridge không hợp lệ."
        normalized: list[dict[str, str]] = []
        for target in targets:
            if not isinstance(target, dict):
                return None, "Mỗi binding phải chỉ định node và input."
            node_id = str(target.get("node") or "")
            input_name = str(target.get("input") or "")
            if node_id not in prompt or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", input_name):
                return None, "Binding trỏ tới node hoặc input không hợp lệ."
            normalized.append({"node": node_id, "input": input_name})
        normalized_bindings[name] = normalized
    return {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "id": workflow_id,
        "title": title,
        "kind": kind,
        "description": str(value.get("description") or "")[:500],
        "prompt": prompt,
        "bindings": normalized_bindings,
    }, None


def _read_bridge(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    workflow, _error = _validate_bridge_workflow(value, required_id=path.name.removesuffix(".bridge.json"))
    return workflow


def _bridge_workflows() -> list[tuple[dict[str, Any], bool]]:
    found: dict[str, tuple[dict[str, Any], bool]] = {}
    for root, local in ((BRIDGE_WORKFLOW_ROOT, False), (BRIDGE_LOCAL_ROOT, True)):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.bridge.json")):
            workflow = _read_bridge(path)
            if workflow:
                found[str(workflow["id"])] = (workflow, local)
    return [found[key] for key in sorted(found)]


def list_bridge_workflows() -> list[dict[str, Any]]:
    """Return only safe metadata; raw prompts stay in a selected local workflow."""

    result: list[dict[str, Any]] = []
    for workflow, local in _bridge_workflows():
        result.append({
            "id": workflow["id"],
            "title": workflow["title"],
            "kind": workflow["kind"],
            "engine": workflow.get("engine"),
            "description": workflow.get("description", ""),
            "inputs": workflow.get("inputs") or sorted((workflow.get("bindings") or {}).keys()),
            "local": local,
        })
    return result


def load_bridge_workflow(workflow_id: str) -> dict[str, Any] | None:
    if not BRIDGE_NAME.fullmatch(workflow_id):
        return None
    for workflow, _local in _bridge_workflows():
        if workflow["id"] == workflow_id:
            return workflow
    return None


def save_bridge_workflow(workflow_id: str, value: object) -> tuple[int, dict[str, Any]]:
    workflow, error = _validate_bridge_workflow(value, required_id=workflow_id)
    if workflow is None:
        return 400, {"status": "error", "error": error or "Bridge workflow không hợp lệ."}
    path = _bridge_path(workflow_id, local=True)
    assert path is not None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        return 500, {"status": "error", "error": str(exc)}
    return 201, {"status": "completed", "workflow": {item: workflow[item] for item in ("id", "title", "kind", "description") if item in workflow}, "local": True}


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


def _submit_workflow(workflow: dict[str, Any], engine: str, request: dict[str, Any], context: JobContext | None = None) -> dict[str, Any]:
    """Queue one already-sanitized Comfy API graph and collect image outputs."""

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


def _set_binding(workflow: dict[str, Any], targets: list[dict[str, str]], value: object) -> str | None:
    for target in targets:
        node = workflow.get(target["node"])
        if not isinstance(node, dict):
            return "Bridge binding trỏ tới node không còn tồn tại."
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            return "Bridge binding trỏ tới node không có inputs."
        node_inputs[target["input"]] = value
    return None


def _apply_bridge_bindings(workflow: dict[str, Any], bindings: dict[str, list[dict[str, str]]], inputs: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    value = json.loads(json.dumps(workflow, ensure_ascii=False))
    if bindings.get("text"):
        error = _set_binding(value, bindings["text"], str(inputs.get("text") or ""))
        if error:
            return None, error
    if bindings.get("metadata") and inputs.get("metadata") is not None:
        error = _set_binding(value, bindings["metadata"], json.dumps(inputs["metadata"], ensure_ascii=False))
        if error:
            return None, error
    for name in ("image", "mask"):
        artifact = inputs.get(name)
        if artifact is None:
            continue
        if not bindings.get(name):
            return None, f"Bridge workflow không có binding {name.upper()} cho input đang nối."
        path = Path(str(artifact))
        if not path.is_file():
            return None, f"Artifact {name.upper()} không còn tồn tại."
        uploaded, error = _upload_input_image(path)
        if not uploaded:
            return None, error or f"Không upload được {name.upper()} vào ComfyUI."
        error = _set_binding(value, bindings[name], uploaded)
        if error:
            return None, error
    for name in ("video", "audio"):
        if inputs.get(name) is not None:
            return None, f"ComfyUI bridge hiện chưa có uploader an toàn cho {name.upper()}; chuyển output FFmpeg thành IMAGE hoặc dùng workflow plugin có adapter riêng."
    return value, None


def run_bridge_workflow(workflow_id: str, inputs: dict[str, Any], data: dict[str, Any], context: JobContext | None = None) -> dict[str, Any]:
    """Run a saved bridge without exposing a path or arbitrary command to the browser."""

    workflow = load_bridge_workflow(workflow_id)
    if workflow is None:
        return {"status": "unavailable", "error": "Bridge workflow không tồn tại hoặc không an toàn."}
    if workflow["kind"] == "quick_api":
        if inputs.get("mask") is not None:
            return {"status": "unavailable", "error": "Quick bridge không có mask binding. Lưu workflow Advanced có binding MASK để dùng SAM2 trực tiếp."}
        request = dict(data)
        request["prompt"] = str(inputs.get("text") or data.get("prompt") or "")
        image = inputs.get("image")
        if image is not None:
            request["input_image"] = str(image)
        result = generate(str(workflow["engine"]), request, context)
        if result.get("status") == "completed":
            result["bridge_workflow"] = workflow_id
        return result
    available, reason = ensure_running()
    if not available:
        return unavailable("comfyui", reason)
    prompt, error = _apply_bridge_bindings(workflow["prompt"], workflow.get("bindings") or {}, inputs)
    if prompt is None:
        return {"status": "unavailable", "error": error or "Không thể bind input của Hub vào workflow ComfyUI."}
    result = _submit_workflow(prompt, f"bridge_{workflow_id}", data, context)
    if result.get("status") == "completed":
        result["bridge_workflow"] = workflow_id
    return result


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
