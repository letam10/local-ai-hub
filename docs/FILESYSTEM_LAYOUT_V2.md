# Filesystem Layout V2

Local AI Hub V2 separates tracked first-party source from machine-local AI
installations. GitHub stores source, wrappers, configuration examples, patches,
tests and documentation. It never stores model weights, virtual environments,
runtime clones, cache, output, temporary media, logs or credentials.

## Canonical repository layout

```text
<local-ai-hub>/
├── src/
│   ├── app/             # bootstrap, routing and lifecycle
│   ├── app_config/      # settings defaults and validation
│   ├── components/      # reusable UI/component boundaries
│   ├── modules/         # capability modules with manifest.json
│   ├── services/        # API, MCP and infrastructure services
│   └── shared/          # paths, schemas, validation and utilities
├── Config/              # tracked examples + ignored machine-local settings
├── runtime/             # ignored managed engines, tools and applications
├── Models/              # ignored model/checkpoint tree
├── Cache/ Output/ Temp/ Logs/  # ignored machine state
├── scripts/ patches/ tests/ docs/
└── dependencies.lock.json
```

`Config/` and `Models/` retain the existing Windows casing. Windows is
case-insensitive, so a parallel `config/` or `models/` path must never be
created. The singular `runtime/` tree is the V2 target; an existing `Runtimes/`
tree remains legacy data unless explicitly migrated.

## Module contract

Each `src/modules/<module>/` owns a `module.py` and `manifest.json`. Backend,
UI and tests are added only when that module uses them. A manifest records its
component state separately from tool state, so an installed external app is not
misrepresented as an operational Hub tool.

Existing import paths (`Hub.*`, `Adapters.*`, and `MCP.*`) are compatibility
shims. New code must import from `src.*`; the shims are removed only in a later,
dedicated cleanup PR after all launchers pass bounded smoke tests.

## Runtime and model policy

The path registry defines these managed targets without creating duplicate
content:

- `runtime/engines/{vision,speech,voice,image,video}` for upstream engines.
- `runtime/tools/ffmpeg` for one canonical FFmpeg/FFprobe runtime.
- `runtime/applications` for portable external applications or safe link files.
- `Models/{Vision,OCR,Speech,Voice,Image,Video}` for model categories.

Use configuration, the model registry, framework extra-model paths, or a
verified junction before considering a duplicate model file. Qwen3-TTS belongs
under Voice; Qwen Image belongs under Image and is never mixed with TTS assets.
An existing shared ComfyUI runtime is shared by FLUX and Qwen Image when their
compatibility is verified; V2 never creates a second copy by default.

## Current migration boundary

The host-specific inventory is kept only in
`Config/layout_migration.local.json` (ignored). It records source/destination,
size, processes, strategy, rollback, status and verification for every found
component. The Stage B implementation reorganizes first-party source and adds
safe migration tooling; it does not copy, delete or blindly relocate large
legacy installations.
