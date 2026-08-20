# V7 change impact map

Before editing a hotspot, identify its owner, contract, tests and forbidden
machine data. Never infer permission from a filename alone.

| Change area | Primary owner | Related API/UI | Tests | Security/filesystem impact |
|---|---|---|---|---|
| Settings | `src/app_config/`, settings service | settings routes/features | V6 settings + API surface | preserve local Config; no secrets in responses |
| Desktop lifecycle | `src/app/` | shell/launcher | startup/lifecycle tests | single-window/process ownership |
| Artifact Store | `src/services/artifact_store.py` | jobs/artifact routes | durable output tests | Output-only publication; opaque IDs |
| Job Manager | `src/services/job_manager/` | Jobs page | job recovery/atomicity | lifecycle reconciliation; no orphan artifacts |
| Node Studio | `src/services/node_studio/`, `src/ui/node_studio.js` | node routes/editor | node workflow tests | no raw path from browser |
| Projects/workflows | project/workflow services | workspace UI | project/workflow tests | metadata only; preserve user data |
| SAM2 | `src/modules/sam2/` | vision routes/features | SAM2 contract + bounded smoke | model/runtime containment and GPU gate |
| AnimeSR/RIFE/ESR | respective `src/modules/` | video routes | video runtime contract | fixed model leaves; no arbitrary executable/path |
| Whisper | `src/modules/whisper/`, `Services/Whisper/` | speech routes | first-party Whisper contract | Output containment; path-free receipts |
| Vision/OCR/Voice | `src/modules/vision`, `ocr`, `voice` | typed API routes | adapter contract tests | opaque artifact inputs, no secret/path echo |
| ComfyUI/FLUX/Qwen | `src/modules/image_generation/` | Image AI feature | Comfy contract | fixed workflow/profile; no downloads at startup |
| Module Manager | `src/services/module_manager/` | future modules UI | capability/module tests | manifest allowlist, no arbitrary plugin import |
| Model Manager | `src/services/model_manager/` | future model UI | catalog/install fixture tests | HTTPS/checksum/staging/receipt; no weights in Git |
| Runtime Manager | `src/services/runtime_manager/` | diagnostics/modules UI | runtime catalog tests | no driver/CUDA change; no environment overwrite |
| Installer/bootstrap | `scripts/bootstrap_core.py`, `src/services/bootstrap_core.py` | desktop launchers | clean-clone tests | model-free, offline, idempotent, preserve local files |
| Versioning | `src/shared/version.py` | health/installer/docs | version consistency | one product source; schema versions unchanged |

For every change, update the relevant example config, contract test and
architecture metadata. Do not edit `Models`, `Environments`, runtime, Output,
or user media as part of a source package.
