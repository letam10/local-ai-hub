# V7 change impact map

Before editing a hotspot, identify its owner, contract, tests and forbidden
machine data. Never infer permission from a filename alone.

| Change area | Primary owner | Related API/UI | Tests | Security/filesystem impact |
|---|---|---|---|---|
| Settings | `src/app_config/`, settings service | settings routes/features | V6 settings + API surface | preserve local Config; no secrets in responses |
| Desktop lifecycle | `src/app/` | shell/launcher | startup/lifecycle tests | single-window/process ownership |
| Artifact Store | `src/services/artifact_store.py` | jobs/artifact routes | durable output tests | Output-only publication; opaque IDs |
| Job Manager | `src/services/job_manager/` | Jobs page | job recovery/atomicity | lifecycle reconciliation; no orphan artifacts |
| Node Studio | `src/services/node_studio/`, `src/ui/features/node_studio/studio.js` (legacy facade: `src/ui/node_studio.js`) | node routes/editor | node workflow tests | no raw path from browser |
| Projects/workflows | project/workflow services | workspace UI | project/workflow tests | metadata only; preserve user data |
| SAM2 | `src/modules/sam2/` | vision routes/features | SAM2 contract + bounded smoke | model/runtime containment and GPU gate |
| AnimeSR/RIFE/ESR | respective `src/modules/` | video routes | video runtime contract | fixed model leaves; no arbitrary executable/path |
| Whisper | `src/modules/whisper/`, `Services/Whisper/` | speech routes | first-party Whisper contract | Output containment; path-free receipts |
| Vision/OCR/Voice | `src/modules/vision`, `ocr`, `voice` | typed API routes | adapter contract tests | opaque artifact inputs, no secret/path echo |
| ComfyUI/FLUX/Qwen | `src/modules/image_generation/` | Image AI feature | Comfy contract | fixed workflow/profile; no downloads at startup |
| Module Manager | `src/services/module_manager/` | future modules UI | capability/module tests | manifest allowlist, no arbitrary plugin import |
| Model Manager | `src/services/model_manager/` | future model UI | catalog/install fixture tests | HTTPS/checksum/staging/receipt; no weights in Git |
| Runtime Manager | `src/services/runtime_manager/` | diagnostics/modules UI | runtime catalog tests | no driver/CUDA change; no environment overwrite |
| Component Installer | `src/services/component_installer/` | Components / AI Setup API/UI | Phase 2 component contract tests | trusted HTTPS, bounded staging, resume/checksum/archive safety, opaque plans |
| Component maintenance | `src/services/api/components.py`, manager | Components / AI Setup | Phase 2 API/UI tests | repair/update/uninstall are plan-only; preserve shared/user data |
| Capability Graph V2 | `src/services/capability_graph/` | `/api/capabilities/v2`, Components | `tests/test_post_v8_capability_graph_v2.py` | bounded server-owned metadata/evidence with freshness; no path, provider or workload execution |
| API Feature Discovery V2 | `src/services/feature_discovery_v2.py` | `/api/features/v2` | `tests/test_post_v8_feature_discovery_v2.py` | Router-bound finite protocol catalog; absent/mismatched routes are not advertised |
| Component Lifecycle Engine V2 | `src/services/component_lifecycle_engine/` | `/api/component-lifecycle/v2`, Components | `tests/test_post_v8_component_lifecycle_engine.py` | V8 plan facade only for model/runtime; component lifecycle stays inspect-only until a real adapter exists |
| Model Manager V2 | `src/services/model_manager_v2/` | `/api/model-manager/v2`, Models & Storage | `tests/test_post_v8_model_manager_v2.py` | bounded catalog observations, opaque location IDs, identity/content-digest split and duplicate-download guard; V8 plan-only actions |
| Resource Scheduler V2 | `src/services/resource_scheduler/` | `/api/resource-scheduler/v2`, Dashboard | `tests/test_post_v8_resource_scheduler.py` | server-owned capacity simulation, revisioned reservations/leases, current-free VRAM safety policy, no GPU probe/worker launch/browser reservation mutation |
| Durable Job Engine V2 | `src/services/durable_job_engine_v2/` | `/api/durable-job-engine/v2`, Jobs | `tests/test_post_v8_durable_job_engine_v2.py` | SQLite CAS metadata plus compensated scheduler saga; opaque IDs, trusted readmission, reconstruct-only retry remains non-active, no artifact deletion or browser worker control |
| M1.1 foundation contract | `docs/architecture/POST_V8_FOUNDATION_HARDENING_M1_1.md`, `src/services/projection_cache.py` | V2 surfaces | `tests/test_post_v8_projection_cache.py` + V2 suites | exact ownership/lease/freshness/cache boundaries; no model/provider/GPU workload |
| M3 Provider Adapters & External Integrations | `src/services/provider_adapters_v2/`, `docs/architecture/POST_V8_PROVIDER_ADAPTERS_M3.md` | `/api/provider-adapters/v2`, `/api/external-integrations/v2`, AIRI route | `tests/test_post_v8_provider_adapters_m3.py` | finite first-party preflight only; external projection is path/credential/endpoint-free and never launches from V2 |
| M4 Product Experience V2 | `src/services/product_experience_v2.py`, `docs/architecture/POST_V8_PRODUCT_EXPERIENCE_M4.md` | `/api/product-experience/v2`, Dashboard shell | `tests/test_post_v8_product_experience_m4.py` | finite route/onboarding/search catalog only; no user-data search, settings write, provider or workload execution |
| M5 Platform Hardening V2 | `src/services/platform_hardening_v2.py`, `docs/architecture/POST_V8_PLATFORM_HARDENING_M5.md` | `/api/platform-hardening/v2`, Diagnostics | `tests/test_post_v8_platform_hardening_m5.py` | read-only updater/backup/process/security/performance contract; operations stay behind existing owners and confirmations |
| M6 Platform Extensibility V2 | `src/services/platform_extensibility_v2.py`, `docs/architecture/POST_V8_PLATFORM_EXTENSIBILITY_M6.md` | `/api/extensibility/v2`, Diagnostics | `tests/test_post_v8_platform_extensibility_m6.py` | declarative Plugin SDK, API versioning and future remote-worker contract; no import, endpoint, credential or dispatch |
| Installer/bootstrap | `scripts/bootstrap_core.py`, `src/services/bootstrap_core.py` | desktop launchers | clean-clone tests | model-free, offline, idempotent, preserve local files |
| Versioning | `src/shared/version.py` | health/installer/docs | version consistency | one product source; schema versions unchanged |
| Production catalog | `src/services/productization/catalog.py`, `Config/v7_production_catalog.example.json` | Models / Components / Dashboard | `tests/test_v7_final_productization.py` | fixed leaves, real disposition, no fabricated size or source |
| One-click lifecycle | `src/services/productization/lifecycle.py` | `/api/productization/*`, Components/Models features | final productization acceptance | inspect/plan/confirm/apply/verify; preserve existing installs |
| UI feature ownership | `src/ui/core/`, `src/ui/features/`, `src/ui/styles/` | navigation and feature surfaces | UI architecture + Node syntax tests | no filesystem/subprocess, static i18n only |
| Desktop setup | `scripts/setup_local_ai_hub.py`, `scripts/setup_local_ai_hub.ps1` | desktop launcher/shortcut | clean-clone/setup acceptance | absent-only Core bootstrap, no model download |
| Release package | `scripts/build_installer.py`, `distribution/installer.iss` | ZIP/Setup EXE | release manifest and package tests | excludes Models, Environments, runtime, Output, local Config |

For API route changes, update `architecture/api_routes.yaml`, the owning
`src/services/api/routes/<domain>.py` adapter and its contract test. Backup,
Components, Model/Runtime, Project/Creative and streaming changes remain in
their existing application services; route files are transport adapters.

For every change, update the relevant example config, contract test and
architecture metadata. Do not edit `Models`, `Environments`, runtime, Output,
or user media as part of a source package.
