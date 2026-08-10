# Milestone 4B — Extension Platform, Capability Packs & Resource Planner

## Outcome

Milestone 4B adds a declarative extension foundation. It can discover repository-managed descriptors, validate their static contract, inspect declared component/model dependencies, build dry-run resource plans, and render compatibility reports. It does not add a UI, API route, job runner, dynamic module loader, package installer, model downloader, or workload launcher.

The implementation lives in these new boundaries:

- `src/shared/schemas/extension_manifest.py` — `extension-manifest.v1` validation and allowlists.
- `src/services/extension_platform/` — static discovery, dependency preflight, report export, and a safe scaffold generator.
- `src/services/capability_planner/` — model/runtime cards and dry-run CPU/GPU/VRAM/RAM/disk planning.
- `extensions/` — managed sample descriptor trees only.

## Security and ownership boundary

Discovery is deliberately static. It searches only the immediate directories below repository `extensions/`, ignores symlinks, accepts only `extension.json`, allowlisted JSON card/pack descriptors, and allowlisted Markdown documentation. It never recursively imports Python, executes an entrypoint, invokes a shell, starts a process, probes a GPU, downloads a model, or reads a machine-local runtime path.

The v1 permission allowlist is metadata-only:

- `read_extension_metadata`
- `read_capability_cards`
- `inspect_component_status`
- `inspect_model_status`
- `plan_resources`
- `render_compatibility_report`

No `shell`, executable command, environment variable, raw local path, secret, token, or arbitrary entrypoint is accepted by the manifest or card schemas. Reports deliberately omit filesystem locations. Entrypoint descriptor paths are validated as safe, relative, repository-contained names before they are read.

## Honest availability states

Every extension manifest has an availability object containing `status`, `reason`, and `action`.

| State | Meaning in Milestone 4B |
| --- | --- |
| `operational` | Static descriptors validate and the extension declares no runtime workload. It is not evidence that a backend has been launched. |
| `partial` | Metadata is usable, but a runtime dependency, model, hardware claim, or functional smoke is still unresolved. |
| `unavailable` | The static descriptor is invalid, missing, incompatible, or a required dependency is unavailable. |
| `planned` | The descriptor is intentionally not ready or not enabled. |

An extension that declares components, models, or a runtime capability cannot be upgraded to operational by static discovery alone. Dependency preflight stays dry-run and reports the remediation action; a separate bounded functional smoke is needed before an integration owner changes an operational claim.

## Dependency and capability preflight

`preflight_extension` and `preflight_extensions` accept declarative inventories from `Config/extensions.local.json` when present, falling back to `Config/extensions.example.json`. Only these normalized inputs are used:

- Hub semantic version and platform.
- Component and model identifiers, semantic versions, and availability state.
- Optional sanitized hardware capacity.
- An explicit enabled-extension list.

Preflight returns per-requirement status, a reason, and a clear corrective action. It neither confirms files on disk nor tries to repair, install, import, or launch a dependency.

`enabled_extensions` is an explicit planning allowlist when supplied: a non-empty list plans only listed ids, and an explicit empty list plans none. Entries outside that allowlist remain visible as `planned` metadata but do not consume capacity or affect the aggregate status of enabled work. If the setting is omitted by a direct service caller, all discovered records are in scope for planning.

## Resource Planner

`plan_resources` and `ResourcePlanner.plan` consume valid manifests and optional sanitized capacity metadata. They always return `dry_run: true` and report:

- CPU class and estimated threads.
- Required GPU vendor/device class and requested VRAM.
- RAM and disk estimates.
- GPU assignments when a supplied inventory can satisfy them.
- Aggregate resource totals.
- Collisions in declared `exclusive_resource_groups`.
- Capacity or inventory remediation actions.

The planner is a scheduling aid only. It does not allocate memory, inspect a physical GPU, start an inference backend, or alter a workload queue.

For GPU requests, the planner first checks whether each request can physically fit on a compatible known device. An individually oversized request is `unavailable`; only requests that fit alone can become `partial` from parallel contention. In serial mode, independently fitting requests may reuse the same device after the prior request completes.

## Model and runtime cards

Card collections use `model-cards.v1` and `runtime-cards.v1`. Each card has lineage, license, source URL, intended use, limitations, and compatibility metadata. Runtime cards additionally identify a safe descriptor kind and allowlisted capabilities. They describe provenance and constraints; they never carry a local installation path, executable command, or credential.

## Samples and configuration

`extensions/metadata-catalog/` is static-only and can be reported as operational after descriptor validation. `extensions/image-resource-advisor/` intentionally remains partial because it documents separately managed ComfyUI/model requirements without launching or verifying them.

`Config/extensions.example.json` provides only non-secret example inventory/capacity metadata. A local machine may add ignored `Config/extensions.local.json`; it must use the same schema and must not be committed.

## Validation and report export

Run the static validator from the repository root:

```powershell
python scripts/validate_extensions.py --format json
python scripts/validate_extensions.py --format markdown
python scripts/validate_extensions.py --strict
```

The CLI writes the JSON or Markdown compatibility report to standard output. `--strict` returns non-zero unless all extensions are operational. The default returns non-zero for an unavailable report, while planned or partial findings remain reportable so owners can see the remediation path.

The optional generator is intentionally limited to a new descriptor directory below this repository's `extensions/` root:

```powershell
python scripts/validate_extensions.py --generate sample-extension --display-name "Sample Extension"
```

It writes JSON and Markdown descriptors only, refuses to overwrite a descriptor, and does not create code, install packages, or download models.

## Integration handoff

This milestone deliberately does not connect to shared UI/API/job/module surfaces. An integration owner can consume `discover_extensions`, `preflight_extensions`, `build_compatibility_report`, and `plan_resources` after deciding the appropriate bounded API/UI contract. Until that owner adds and smoke-tests a route or tool, no route/tool should be presented as operational.

Any future integration must treat discovery/preflight output as server-owned data and pass only those validated outputs to `build_compatibility_report`. It must not expose a route that accepts client-supplied manifests, discovery records, preflight records, or report mappings for public projection. `workflow_templates` remains runtime/unverified and therefore `partial` until a separate, strictly validated static template-descriptor contract is added.
