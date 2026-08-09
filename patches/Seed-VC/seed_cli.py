from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()
SERVICE = Path(os.path.expandvars(os.environ.get("SEED_VC_HOME") or str(Path(__file__).resolve().parent))).expanduser()
CHECKPOINT = Path(os.path.expandvars(os.environ.get("SEED_VC_CHECKPOINT", str(ROOT / "Models" / "Voice" / "Seed-VC" / "seed-uvit-tat-xlsr-tiny" / "DiT_uvit_tat_xlsr_ema.pth")))).expanduser()
CONFIG = SERVICE / "configs" / "presets" / "config_dit_mel_seed_uvit_xlsr_tiny.yml"


def emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") == "completed" else 2


def main() -> int:
    try:
        request_bytes = Path(sys.argv[1]).read_bytes() if len(sys.argv) > 1 else sys.stdin.buffer.read()
        request = json.loads(request_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return emit({"status": "error", "error": f"Invalid JSON request: {exc}"})
    source = Path(os.path.expandvars(str(request.get("source", "")))).expanduser()
    target = Path(os.path.expandvars(str(request.get("target", "")))).expanduser()
    if not source.is_file() or not target.is_file():
        return emit({"status": "error", "error": f"Source/target audio not found: {source} / {target}"})
    checkpoint = Path(os.path.expandvars(str(request.get("checkpoint", CHECKPOINT)))).expanduser()
    config = Path(os.path.expandvars(str(request.get("config", CONFIG)))).expanduser()
    if not checkpoint.is_file() or not config.is_file():
        return emit({"status": "error", "error": f"Seed-VC model/config missing: {checkpoint} / {config}"})
    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "Voice"
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / f"seed_vc_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    run_dir.mkdir(parents=True, exist_ok=False)
    steps = max(1, int(request.get("diffusion_steps", 4)))
    command = [sys.executable, str(SERVICE / "inference.py"), "--source", str(source), "--target", str(target), "--output", str(run_dir), "--diffusion-steps", str(steps), "--length-adjust", str(float(request.get("length_adjust", 1.0))), "--inference-cfg-rate", str(float(request.get("inference_cfg_rate", 0.7))), "--f0-condition", "False", "--auto-f0-adjust", "False", "--semi-tone-shift", "0", "--checkpoint", str(checkpoint), "--config", str(config), "--fp16", "True" if request.get("fp16", True) else "False"]
    cache_root = Path(os.path.expandvars(os.environ.get("LOCAL_AI_CACHE", str(ROOT / "Cache")))).expanduser()
    environment = {**os.environ, "HF_HOME": str(cache_root / "HuggingFace"), "HF_HUB_CACHE": str(cache_root / "HuggingFace" / "hub"), "LOCALAIHUB_HF_HUB_CACHE": str(cache_root / "HuggingFace" / "hub"), "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(command, cwd=str(SERVICE), capture_output=True, timeout=int(request.get("timeout_seconds", 900)), check=False, env=environment)
    except subprocess.TimeoutExpired as exc:
        return emit({"status": "error", "error": "Seed-VC timed out.", "stdout": str(exc.stdout)[-2000:]})
    except OSError as exc:
        return emit({"status": "error", "error": str(exc)})
    wavs = sorted(run_dir.glob("*.wav"), key=lambda item: item.stat().st_mtime)
    if result.returncode != 0 or not wavs:
        return emit({"status": "error", "error": "Seed-VC inference failed or returned no WAV output.", "returncode": result.returncode, "stdout": result.stdout.decode("utf-8", errors="replace")[-4000:], "stderr": result.stderr.decode("utf-8", errors="replace")[-6000:]})
    output = wavs[-1]
    return emit({"status": "completed", "operation": "convert_voice", "model": str(checkpoint.parent), "source": str(source), "target": str(target), "diffusion_steps": steps, "output": str(output), "device": "cuda:0" if os.environ.get("LOCALAIHUB_FORCE_CPU") != "1" else "cpu"})


if __name__ == "__main__":
    raise SystemExit(main())
