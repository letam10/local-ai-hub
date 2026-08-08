from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(os.path.expandvars(os.environ.get("LOCALAIHUB_ROOT") or str(Path(__file__).resolve().parents[2]))).expanduser()
MODEL_ROOT = Path(os.path.expandvars(os.environ.get("QWEN3_TTS_MODEL_HOME", str(ROOT / "Models" / "Voice" / "Qwen3-TTS")))).expanduser()
CUSTOM_MODEL = MODEL_ROOT / "Qwen3-TTS-12Hz-0.6B-CustomVoice"
DESIGN_MODEL = MODEL_ROOT / "Qwen3-TTS-12Hz-1.7B-VoiceDesign"
BASE_MODEL = MODEL_ROOT / "Qwen3-TTS-12Hz-1.7B-Base"


def main() -> int:
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    operation = str(request.get("operation", "text_to_speech"))
    text = str(request.get("text", "Local AI Hub is ready."))
    language = str(request.get("language", "English"))
    if language.lower() == "auto":
        language = "English"
    speaker = str(request.get("speaker", "Ryan"))
    defaults = {"text_to_speech": CUSTOM_MODEL, "design_voice": DESIGN_MODEL, "clone_voice": BASE_MODEL}
    model_path = Path(os.path.expandvars(str(request.get("model_path", defaults.get(operation, CUSTOM_MODEL))))).expanduser()
    if not model_path.is_dir():
        print(json.dumps({"status": "error", "error": f"Qwen model directory does not exist: {model_path}"}, ensure_ascii=False))
        return 2
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    device = "cpu" if os.environ.get("LOCALAIHUB_FORCE_CPU") == "1" else "cuda:0"
    kwargs = {"dtype": torch.float32 if device == "cpu" else torch.bfloat16}
    if device != "cpu":
        kwargs["device_map"] = device
    model = Qwen3TTSModel.from_pretrained(str(model_path), **kwargs)
    max_new_tokens = int(request.get("max_new_tokens", 512))
    if operation == "design_voice":
        wavs, sample_rate = model.generate_voice_design(text=text, language=language, instruct=str(request.get("instruct", "natural, warm, clear speaking voice")), max_new_tokens=max_new_tokens)
    elif operation == "clone_voice":
        reference_audio = Path(os.path.expandvars(str(request.get("reference_audio", "")))).expanduser()
        if not reference_audio.is_file():
            print(json.dumps({"status": "error", "error": f"Reference audio does not exist: {reference_audio}"}, ensure_ascii=False))
            return 2
        clone_prompt = model.create_voice_clone_prompt(ref_audio=str(reference_audio), ref_text=str(request.get("reference_text", "")), x_vector_only_mode=not bool(str(request.get("reference_text", "")).strip()))
        wavs, sample_rate = model.generate_voice_clone(text=text, language=language, voice_clone_prompt=clone_prompt, max_new_tokens=max_new_tokens)
    else:
        operation = "text_to_speech"
        wavs, sample_rate = model.generate_custom_voice(text=text, language=language, speaker=speaker, instruct=request.get("instruct"), max_new_tokens=max_new_tokens)
    output_base = Path(os.path.expandvars(os.environ.get("LOCAL_AI_OUTPUT", str(ROOT / "Output")))).expanduser()
    output_root = output_base / "Voice"
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f"qwen3_{operation}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
    audio = wavs[0].detach().cpu().numpy() if hasattr(wavs[0], "detach") else wavs[0]
    sf.write(str(output_path), audio, sample_rate)
    payload = {"status": "completed", "operation": operation, "model": str(model_path), "language": language, "sample_rate": int(sample_rate), "duration_seconds": round(len(audio) / sample_rate, 3), "output": str(output_path), "device": device}
    if operation == "text_to_speech":
        payload["speaker"] = speaker
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
