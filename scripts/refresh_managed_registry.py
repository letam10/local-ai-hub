"""Refresh ignored local registries after a verified layout migration.

Only known Local AI Hub records are updated. Existing unrelated component and
model records are retained, and no credential-bearing configuration is read or
written to reports or source control.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "Config"
RUNTIME = ROOT / "runtime"
MODELS = ROOT / "Models"


def system_application_paths(application: str) -> tuple[Path, Path]:
    """Build per-user installer paths without storing a person's home path in Git."""

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    application_dir = local_app_data / "Programs" / application
    executable = application_dir / f"{application}.exe"
    return application_dir, executable


def environment_path(component: str) -> Path:
    """Use the canonical V3 environment after import verification, else retain a safe fallback.

    V3 migration is intentionally staged: the legacy environment remains selected
    until a replacement interpreter exists.  This avoids publishing a registry
    that points at an environment that has not been created or smoke-tested.
    """

    canonical = ROOT / "Environments" / component
    migration = load(CONFIG / "environment_migration_v3.local.json", {})
    state = migration.get(component) if isinstance(migration, dict) else None
    verified = isinstance(state, dict) and state.get("status") in {"verified", "functional_smoke_passed"}
    if verified and (canonical / "Scripts" / "python.exe").is_file():
        return canonical
    return Path(r"D:\AI_4K_TEMP") / f"{component}_venv"


def load(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def replace_by_id(items: list[dict[str, Any]], item_id: str, updates: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    matched = False
    for item in items:
        if item.get("id") == item_id:
            result.append({**item, **updates})
            matched = True
        else:
            result.append(item)
    if not matched:
        result.append(updates)
    return result


def component_records() -> list[dict[str, Any]]:
    applications = RUNTIME / "applications"
    engines = RUNTIME / "engines"
    animesr_environment = environment_path("animesr")
    sam2_environment = environment_path("sam2")
    return [
        {
            "id": "local_ai_api", "name": "Local AI API", "kind": "service", "status": "installed", "version": "3.0.0",
            "path": str(ROOT), "executable": str(ROOT / "Environments" / "hub" / "Scripts" / "python.exe"),
            "environment": str(ROOT / "Environments" / "hub"), "port": 8765, "adapter": "first-party-single-window", "source": "Local AI Hub",
        },
        {
            "id": "animesr", "name": "AnimeSR", "kind": "portable_engine", "status": "partial", "version": "bundled",
            "path": str(engines / "video" / "AnimeSR"), "executable": None,
            "environment": str(animesr_environment), "model": str(MODELS / "Video" / "AnimeSR" / "AnimeSR_v2.pth"), "port": None, "adapter": "direct-hidden-worker", "source": "https://github.com/TencentARC/AnimeSR",
        },
        {
            "id": "sam2", "name": "SAM 2", "kind": "portable_engine", "status": "partial", "version": "2.1",
            "path": str(engines / "vision" / "SAM2"), "executable": None,
            "environment": str(sam2_environment), "model": str(MODELS / "Vision" / "SAM2" / "sam2.1_hiera_small.pt"), "port": None, "adapter": "direct-hidden-worker", "source": "https://github.com/facebookresearch/sam2",
        },
        {
            "id": "ffmpeg", "name": "FFmpeg / FFprobe", "kind": "shared_runtime", "status": "managed", "version": "bundled",
            "path": str(RUNTIME / "tools" / "ffmpeg"), "executable": str(RUNTIME / "tools" / "ffmpeg" / "ffmpeg.exe"),
            "environment": None, "port": None, "adapter": "ffmpeg", "source": "https://ffmpeg.org/",
        },
        {
            "id": "comfyui", "name": "ComfyUI", "kind": "shared_runtime", "status": "partial", "version": "local portable",
            "path": str(engines / "image" / "ComfyUI"), "executable": str(engines / "image" / "ComfyUI" / "python_embeded" / "python.exe"),
            "environment": str(engines / "image" / "ComfyUI" / "python_embeded"), "model": str(MODELS / "Image" / "Shared-Local-Image-Studio"), "port": 8188, "adapter": "hidden-comfyui-api", "source": "https://github.com/comfyanonymous/ComfyUI",
        },
        {
            "id": "flux_klein_studio", "name": "FLUX Klein Studio", "kind": "image_engine", "status": "partial", "version": "local portable",
            "path": str(applications / "FLUX-Klein-Studio"), "executable": str(applications / "FLUX-Klein-Studio" / "Local Image Studio.exe"),
            "environment": str(engines / "image" / "ComfyUI" / "python_embeded"), "model": str(MODELS / "Image" / "FLUX"), "port": None, "adapter": "direct-comfyui-workflow", "source": "local portable installation",
        },
        {
            "id": "qwen_image", "name": "Qwen Image 2512", "kind": "image_engine", "status": "partial", "version": "2512 FP8",
            "path": str(MODELS / "Image" / "Qwen-Image"), "executable": str(applications / "Qwen-Image-Studio" / "Local Image Studio.exe"),
            "environment": str(engines / "image" / "ComfyUI" / "python_embeded"), "model": str(MODELS / "Image" / "Qwen-Image"), "port": None, "adapter": "direct-comfyui-workflow", "source": "local model manifest",
        },
        {
            "id": "practical_rife", "name": "Practical-RIFE", "kind": "portable_engine", "status": "managed", "version": "unknown",
            "path": str(engines / "video" / "Practical-RIFE"), "executable": None, "environment": None, "port": None, "adapter": "registered-engine", "source": "https://github.com/hzwer/Practical-RIFE",
        },
        {
            "id": "real_esrgan", "name": "Real-ESRGAN", "kind": "portable_engine", "status": "managed", "version": "unknown",
            "path": str(engines / "video" / "Real-ESRGAN"), "executable": None, "environment": None, "port": None, "adapter": "registered-engine", "source": "https://github.com/xinntao/Real-ESRGAN",
        },
        {
            "id": "whisper", "name": "Faster-Whisper ASR", "kind": "external", "status": "not_installed", "version": "unknown",
            "path": str(engines / "speech" / "Faster-Whisper"), "executable": None, "environment": None, "model": None, "port": None, "adapter": "whisper", "source": "https://github.com/SYSTRAN/faster-whisper",
        },
    ]


def model_records() -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    return [
        {
            "id": "animesr-v2", "engine": "AnimeSR", "model_name": "AnimeSR v2", "version": "bundled", "source": "https://github.com/TencentARC/AnimeSR",
            "local_path": str(MODELS / "Video" / "AnimeSR" / "AnimeSR_v2.pth"), "file_size": 5996559, "precision": "configured locally", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "sam2.1-hiera-small", "engine": "SAM 2", "model_name": "sam2.1_hiera_small", "version": "2.1", "source": "https://github.com/facebookresearch/sam2",
            "local_path": str(MODELS / "Vision" / "SAM2" / "sam2.1_hiera_small.pt"), "file_size": 184416285, "precision": "configured locally", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "flux-2-klein-base-4b-fp8", "engine": "FLUX", "model_name": "FLUX.2 Klein Base 4B FP8", "version": "local manifest", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "FLUX" / "diffusion_models" / "flux-2-klein-base-4b-fp8.safetensors"), "file_size": 4089498488, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "qwen-image-2512-fp8", "engine": "Qwen Image", "model_name": "Qwen Image 2512 FP8", "version": "2512", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "Qwen-Image" / "diffusion_models" / "qwen_image_2512_fp8_e4m3fn.safetensors"), "file_size": 20430679144, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "qwen-3-4b", "engine": "Qwen Image", "model_name": "Qwen 3 4B text encoder", "version": "local manifest", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "Qwen-Image" / "text_encoders" / "qwen_3_4b.safetensors"), "file_size": 8044982048, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "flux2-vae", "engine": "FLUX", "model_name": "FLUX.2 VAE", "version": "local manifest", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "FLUX" / "vae" / "flux2-vae.safetensors"), "file_size": 336213556, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "qwen-2.5-vl-7b-fp8", "engine": "Qwen Image", "model_name": "Qwen 2.5 VL 7B text encoder", "version": "local manifest", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "Qwen-Image" / "text_encoders" / "qwen_2.5_vl_7b_fp8_scaled.safetensors"), "file_size": 9384670680, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
        {
            "id": "qwen-image-vae", "engine": "Qwen Image", "model_name": "Qwen Image VAE", "version": "local manifest", "source": "local model manifest",
            "local_path": str(MODELS / "Image" / "Qwen-Image" / "vae" / "qwen_image_vae.safetensors"), "file_size": 253806246, "precision": "FP8", "vram_profile": "configured locally", "license": "verify upstream", "installed_at": None, "last_verified": now,
        },
    ]


def application_records() -> list[dict[str, Any]]:
    applications = RUNTIME / "applications"
    airi_home, airi_executable = system_application_paths("airi")
    ollama_home, ollama_executable = system_application_paths("Ollama")
    return [
        {
            "id": "anime-upscale-studio", "display_name": "Anime Upscale Studio", "category": "video", "classification": "PORTABLE_APP", "status": "managed",
            "path": str(applications / "Anime-Upscale-Studio"), "executable": str(applications / "Anime-Upscale-Studio" / "Anime Upscale Studio.exe"),
            "working_directory": str(applications / "Anime-Upscale-Studio"), "arguments": [], "launch": True, "advanced_only": True,
            "notes": "Portable application is managed under LocalAIHub; the legacy path is retained as a verified junction.",
        },
        {
            "id": "sam2-mask-studio", "display_name": "SAM2 Mask Studio", "category": "vision", "classification": "PORTABLE_APP", "status": "managed",
            "path": str(applications / "SAM2-Mask-Studio"), "executable": str(applications / "SAM2-Mask-Studio" / "SAM2 Mask Studio.exe"),
            "working_directory": str(applications / "SAM2-Mask-Studio"), "arguments": [], "launch": True, "advanced_only": True,
            "notes": "Portable GUI relocated under LocalAIHub; user projects remain outside the application folder.",
        },
        {
            "id": "local-image-studio", "display_name": "Local Image Studio (FLUX)", "category": "image", "classification": "PORTABLE_APP", "status": "managed",
            "path": str(applications / "FLUX-Klein-Studio"), "executable": str(applications / "FLUX-Klein-Studio" / "Local Image Studio.exe"),
            "working_directory": str(applications / "FLUX-Klein-Studio"), "arguments": [], "launch": True, "advanced_only": True,
            "notes": "FLUX and Qwen Image share this application, one ComfyUI runtime and one physical model store.",
        },
        {
            "id": "qwen-image-studio", "display_name": "Local Image Studio (Qwen Image)", "category": "image", "classification": "PORTABLE_APP", "status": "managed",
            "path": str(applications / "Qwen-Image-Studio"), "executable": str(applications / "Qwen-Image-Studio" / "Local Image Studio.exe"),
            "working_directory": str(applications / "Qwen-Image-Studio"), "arguments": [], "launch": True, "advanced_only": True,
            "notes": "Alias for the installed Local Image Studio; no Qwen model is duplicated.",
        },
        {
            "id": "airi", "display_name": "AIRI", "category": "assistant", "classification": "SYSTEM_INSTALLED_APP", "status": "external_system_app",
            "path": str(airi_home), "executable": str(airi_executable),
            "working_directory": str(airi_home), "arguments": [], "launch": True,
            "notes": "Installer-managed external application; credentials are not read or copied.",
        },
        {
            "id": "ollama", "display_name": "Ollama", "category": "model-service", "classification": "SYSTEM_INSTALLED_APP", "status": "external_system_app",
            "path": str(ollama_home), "executable": str(ollama_executable),
            "working_directory": str(ollama_home), "arguments": [], "launch": True,
            "notes": "System-installed external service; model location is unchanged.",
        },
    ]


def write_external_records() -> None:
    external = RUNTIME / "applications" / "external"
    now = datetime.now(UTC).isoformat()
    _airi_home, airi_executable = system_application_paths("airi")
    _ollama_home, ollama_executable = system_application_paths("Ollama")
    write(external / "airi.json", {
        "display_name": "AIRI", "executable": str(airi_executable), "version": "unknown",
        "status": "external_system_app", "launch": "allowlisted", "settings": "external installer-managed", "integration_status": "registered", "updated_at": now,
    })
    write(external / "ollama.json", {
        "display_name": "Ollama", "executable": str(ollama_executable), "version": "unknown",
        "status": "external_system_app", "launch": "allowlisted", "settings": "external installer-managed", "integration_status": "registered", "updated_at": now,
    })


def main() -> int:
    components_path = CONFIG / "components.json"
    components = load(components_path, {"schema_version": 1, "components": []})
    items = [item for item in components.get("components", []) if isinstance(item, dict)]
    for component in component_records():
        items = replace_by_id(items, component["id"], component)
    write(components_path, {**components, "schema_version": 3, "components": items})

    hub_path = CONFIG / "hub_config.json"
    hub = load(hub_path, {})
    write(hub_path, {
        **hub,
        "schema_version": 3,
        "bind_host": "127.0.0.1",
        "api_port": int(hub.get("api_port", 8765)),
        "start_maximized": True,
        "minimum_width": 1280,
        "minimum_height": 720,
        "max_heavy_gpu_jobs": 1,
        "model_load_policy": "on_demand",
        "auto_start_heavy_services": False,
        "comfyui_port": int(hub.get("comfyui_port", 8188)),
        "comfyui_start_timeout_seconds": int(hub.get("comfyui_start_timeout_seconds", 45)),
        "ffmpeg_path": str(RUNTIME / "tools" / "ffmpeg" / "ffmpeg.exe"),
        "ffprobe_path": str(RUNTIME / "tools" / "ffmpeg" / "ffprobe.exe"),
    })

    models_path = CONFIG / "model_registry.json"
    models = load(models_path, {"schema_version": 1, "models": []})
    model_items = [item for item in models.get("models", []) if isinstance(item, dict)]
    for model in model_records():
        model_items = replace_by_id(model_items, model["id"], model)
    write(models_path, {**models, "schema_version": 3, "models": model_items})

    write(CONFIG / "application_registry.local.json", {"schema_version": 2, "applications": application_records()})
    write_external_records()
    print("Refreshed local component, model, application and external registries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
