# Local AI Hub Extension Authoring

## What an extension is

An Extension Platform v1 extension is a static descriptor tree under repository `extensions/`. It is not Python plugin code and it has no executable lifecycle. The platform reads only allowlisted JSON and Markdown metadata to produce discovery, preflight, resource, and compatibility information.

Start from a scaffold:

```powershell
python scripts/validate_extensions.py --generate my-capability --display-name "My Capability"
```

Or copy the shape of a tracked sample without copying its identifiers:

```text
extensions/
  my-capability/
    extension.json
    capability-pack.json
    cards/
      model-cards.json
      runtime-cards.json
    README.md
```

The directory name must exactly match the manifest `id`. Use a lowercase hyphenated identifier of 3–64 characters. Do not use a symlink, a machine path, `..`, an executable file, or a nested dynamic loader.

## `extension-manifest.v1`

`extension.json` must be a JSON object with these required fields.

| Field | Contract |
| --- | --- |
| `schema_version` | Exactly `extension-manifest.v1`. |
| `id`, `version`, `display_name` | Safe lowercase identifier, semantic version, and concise single-line name. |
| `author` | Object containing `name`; optional `url` is public HTTP(S) only. |
| `license`, `source` | Concise license/provenance and a public HTTP(S) source URL without credentials or query data. |
| `capabilities` | Non-empty unique values from the capability allowlist. |
| `compatibility` | A Hub `min_version`, optional `max_version`, and at least one platform (`windows`, `linux`, or `darwin`). |
| `required_components`, `required_models` | Lists of `{id, version?, optional?}` records. Version is a comma-separated semantic comparison such as `>=4.0.0, <5.0.0`. |
| `permissions` | Metadata-only permission allowlist. Shell/process/file-system permissions do not exist in v1. |
| `entrypoints` | Static descriptor records `{kind, path}`. Each path is a safe relative file name, never a raw local path. |
| `resource_profile` | CPU, GPU type, VRAM/RAM/disk estimates, and exclusive resource groups. |
| `availability` | Honest `status`, `reason`, and `action`. |

Optional `description` is concise single-line text.

### Capability allowlist

`audio_transcription`, `capability_pack`, `document_analysis`, `image_analysis`, `image_generation`, `metadata_catalog`, `model_cards`, `resource_planning`, `runtime_cards`, `text_generation`, and `workflow_templates` are the only v1 capability values.

### Permission allowlist

Use only `read_extension_metadata`, `read_capability_cards`, `inspect_component_status`, `inspect_model_status`, `plan_resources`, and `render_compatibility_report`.

In particular, do not add a permission for shell execution, arbitrary imports, filesystem scanning, network downloads, an environment variable, a token, or a secret. The schema rejects unknown fields and permissions rather than interpreting them permissively.

### Entrypoint allowlist

| Kind | Allowed suffix | Purpose |
| --- | --- | --- |
| `capability_pack` | `.json` | A `capability-pack.v1` descriptor. |
| `model_cards` | `.json` | A `model-cards.v1` collection. |
| `runtime_cards` | `.json` | A `runtime-cards.v1` collection. |
| `documentation` | `.md` | Human documentation; it is only checked for safe presence. |

The platform resolves each descriptor relative to the extension directory and rejects symlinks, absolute paths, backslashes, drive prefixes, traversal, non-allowlisted suffixes, and descriptors over the static size limit. No entrypoint is imported or executed.

## Resource profile

Use the complete structure below. Estimates describe peak planning needs; they do not reserve resources.

```json
{
  "cpu": {"class": "moderate", "threads": 4},
  "gpu": {"required": true, "vendor": "nvidia", "device_class": "discrete"},
  "vram_gb": 7.5,
  "ram_gb": 12,
  "disk_gb": 20,
  "exclusive_resource_groups": ["gpu:primary"]
}
```

CPU class is `light`, `moderate`, or `heavy`. A no-GPU profile must use `required: false`, `vendor: "none"`, `device_class: "none"`, and `vram_gb: 0`. GPU vendor is `nvidia`, `amd`, `intel`, or `any`; device class is `integrated`, `discrete`, or `any` when required. Use an exclusive group when two capabilities must not be planned in parallel. The planner marks shared groups as a serialization conflict; it never starts either workload.

## Capability packs and cards

`capability-pack.json` uses `capability-pack.v1` and declares a safe `id`, display name, allowlisted capabilities, and optional `model_card_ids` / `runtime_card_ids`. Its capabilities must be a subset of the extension manifest capabilities, and every card identifier it references must appear in the matching collection.

Model and runtime cards require provenance details:

- `schema_version`, `id`, `display_name`, and semantic `version`.
- Non-empty `lineage`, `license`, and public `source` URL.
- Non-empty `intended_use` and `limitations` lists.
- `compatibility` with platforms and optional declared component/model requirements.

Runtime cards also require `runtime_kind` (`adapter`, `engine`, `library`, or `service`) and may declare allowlisted capabilities. Cards must not expose an installed location, command line, activation token, log path, or claim that a runtime is running.

## Statuses and evidence

Use `operational` only for a static-only descriptor after its metadata validates. Use `partial` for anything where a runtime, model, capability, hardware fact, or functional smoke remains unresolved. Use `unavailable` for a known blocker and `planned` for a descriptor not ready to enable. Every state must give a direct `reason` and actionable next step.

Static discovery cannot validate a backend. When you declare a component/model/runtime requirement, dependency preflight remains a dry-run result. Do not change it to operational solely because an inventory record exists; an integration owner needs a bounded functional smoke before making an operational route/tool claim.

## Configure, validate, and export

Use ignored `Config/extensions.local.json` for local non-secret inventory data when necessary. It has the same shape as `Config/extensions.example.json`:

```json
{
  "schema_version": "extensions-config.v1",
  "hub_version": "4.0.0",
  "platform": "windows",
  "enabled_extensions": ["my-capability"],
  "components": [{"id": "example-runtime", "version": "1.0.0", "status": "partial"}],
  "models": [],
  "hardware": {"cpu_threads": 8, "ram_gb": 32, "disk_gb": 100, "gpus": []}
}
```

Never place API keys, tokens, passwords, raw paths, model locations, or command lines in this configuration. Unknown local fields are intentionally ignored by the platform and never exported into reports.

Then run:

```powershell
python scripts/validate_extensions.py --format json
python scripts/validate_extensions.py --format markdown
```

The report is written to standard output, making it easy to save through an explicit user-owned shell redirection if desired. `--strict` returns non-zero if any extension is not operational. The standard validator returns non-zero for an unavailable report; partial and planned results are intentionally visible because they include remediation actions.
