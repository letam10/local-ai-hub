"""Run a registry-selected Real-ESRGAN tool model in a Hub-owned job."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.services.process_manager.windows import run_hidden
from src.shared.utils.adapter_common import safe_output_namespace


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") == "completed" else 2


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_tree(root: Path, relative: Path) -> Path | None:
    current = root
    if _is_reparse(current):
        return None
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                return None
        if not _inside(root, current):
            return None
    return current


def _safe_existing_under(root: Path, candidate: Path) -> bool:
    try:
        lexical_root = Path(os.path.abspath(str(root)))
        lexical_candidate = Path(os.path.abspath(str(candidate)))
        relative = lexical_candidate.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    if _is_reparse(lexical_root):
        return False
    current = lexical_root
    for part in relative.parts:
        current = current / part
        if _is_reparse(current):
            return False
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved = lexical_candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return resolved == current.resolve(strict=True)


def _safe_model_leaf(root: Path, candidate: Path) -> bool:
    expected = root / Path("Video") / "Real-ESRGAN" / "realesr-animevideov3.pth"
    return (
        root.is_dir()
        and not _is_reparse(root)
        and os.path.normcase(os.path.abspath(str(candidate))) == os.path.normcase(os.path.abspath(str(expected)))
        and candidate.is_file()
        and _safe_existing_under(root, candidate)
    )


def _safe_artifact_input(hub_root: Path, candidate: Path) -> bool:
    for relative in (Path("Temp") / "uploads", Path("Output"), Path("Archive")):
        root = _safe_tree(hub_root, relative)
        if root is not None and _safe_existing_under(root, candidate) and candidate.is_file():
            return True
    return False


def _discard_task(hub_root: Path, path: Path) -> None:
    try:
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            expected_jobs is not None
            and path.name.startswith("realesrgan_")
            and path.parent.resolve(strict=False) == expected_jobs.resolve(strict=False)
            and not _is_reparse(path)
        ):
            shutil.rmtree(path)
    except OSError:
        pass


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        runtime = Path(str(request.get("runtime") or ""))
        source = Path(str(request.get("path") or ""))
        model = Path(str(request.get("model_path") or ""))
        output_root = Path(str(request.get("output_root") or ""))
        temp_root = Path(str(request.get("temp_root") or ""))
        hub_root = Path(os.environ.get("LOCALAIHUB_ROOT", ""))
        script = runtime / "inference_realesrgan.py"
        expected_runtime = _safe_tree(hub_root, Path("runtime"))
        expected_models = _safe_tree(hub_root, Path("Models"))
        namespace_value = request.get("output_namespace")
        expected_output = safe_output_namespace(hub_root, namespace_value) if namespace_value else _safe_tree(hub_root, Path("Output") / "Real-ESRGAN")
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            not hub_root.is_dir()
            or expected_runtime is None
            or expected_models is None
            or expected_output is None
            or expected_jobs is None
            or not runtime.is_dir()
            or not script.is_file()
            or not _safe_artifact_input(hub_root, source)
            or not _safe_model_leaf(expected_models, model)
            or not _safe_existing_under(hub_root, runtime)
            or not _safe_existing_under(runtime, script)
            or output_root.resolve(strict=False) != expected_output.resolve(strict=False)
            or temp_root.parent.resolve(strict=False) != expected_jobs.resolve(strict=False)
            or not temp_root.name.startswith("realesrgan_")
            or temp_root.parent.name != "jobs"
        ):
            return _emit({"status": "error", "error": "Real-ESRGAN runtime contract không hợp lệ."})
        output_root.mkdir(parents=True, exist_ok=True)
        expected_output = safe_output_namespace(hub_root, namespace_value) if namespace_value else _safe_tree(hub_root, Path("Output") / "Real-ESRGAN")
        if expected_output is None or output_root.is_symlink() or output_root.resolve(strict=False) != expected_output.resolve(strict=False):
            return _emit({"status": "error", "error": "Output Real-ESRGAN không khả dụng."})
        temp_root.parent.mkdir(parents=True, exist_ok=True)
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if expected_jobs is None or temp_root.parent.resolve(strict=False) != expected_jobs.resolve(strict=False):
            return _emit({"status": "error", "error": "Temp Real-ESRGAN không khả dụng."})
        temp_root.mkdir(parents=True, exist_ok=False)
        scale = max(1, min(4, int(request.get("scale", 2))))
        tile = max(0, min(2048, int(request.get("tile", 0))))
        result = run_hidden(
            [sys.executable, str(script), "-i", str(source), "-o", str(temp_root), "-n", "realesr-animevideov3", "--model_path", str(model), "-s", str(scale), "-t", str(tile), "--ext", "png"],
            cwd=str(runtime), capture_output=True, timeout=int(request.get("inner_timeout_seconds", 900)), check=False,
        )
        images = sorted((item for item in temp_root.glob("*.png") if item.is_file() and item.stat().st_size), key=lambda item: item.stat().st_mtime)
        if result.returncode != 0 or not images:
            return _emit({"status": "error", "error": "Real-ESRGAN không tạo được IMAGE output."})
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = output_root / f"{source.stem}_RealESRGAN_x{scale}_{stamp}.png"
        shutil.move(str(images[-1]), str(target))
        _discard_task(hub_root, temp_root)
        return _emit({"status": "completed", "operation": "image_upscale", "backend": "real_esrgan", "output": str(target), "model": "realesr-animevideov3", "scale": scale})
    except subprocess.TimeoutExpired:
        return _emit({"status": "error", "error": "Real-ESRGAN vượt quá thời gian bounded của Hub."})
    except Exception:
        return _emit({"status": "error", "error": "Real-ESRGAN worker không thể hoàn tất job."})


if __name__ == "__main__":
    raise SystemExit(main())
