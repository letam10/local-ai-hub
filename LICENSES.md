# License and dependency notes

This repository contains first-party Local AI Hub code plus adapters and
wrappers for external projects. External source is not vendored into this
repository; see `dependencies.lock.json` for the recorded upstream revision.

Before redistribution, verify the license of each upstream project and each
model separately. A model's license may differ from its code project's license.

Known upstream sources:

- GroundingDINO — https://github.com/IDEA-Research/GroundingDINO
- OmniParser — https://github.com/microsoft/OmniParser
- PaddleOCR — https://github.com/PaddlePaddle/PaddleOCR
- Qwen3-TTS — https://github.com/QwenLM/Qwen3-TTS
- Seed-VC — https://github.com/Plachtaa/seed-vc
- RF-DETR — https://github.com/roboflow/rf-detr
- Faster-Whisper — https://github.com/SYSTRAN/faster-whisper
- SAM 2 — https://github.com/facebookresearch/sam2
- AnimeSR — https://github.com/TencentARC/AnimeSR
- AIRI — https://github.com/moeru-ai/airi

LiteGraph.js is a pinned offline frontend dependency under the MIT license; its
exact license text is tracked at `src/ui/vendor/LITEGRAPH-LICENSE.txt`.

No model weights or third-party environments are distributed by this repo.
