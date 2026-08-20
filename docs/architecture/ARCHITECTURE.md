# Local AI Hub V7 architecture foundation

V7 is an incremental architecture layer over the verified V6 product. V5 and
V6 remain historical branches; V7 does not rewrite their history or move the
owner's installation.

## Layers

| Layer | Canonical owner | Responsibility |
|---|---|---|
| Desktop | `src/app/` | shell, WebView, lifecycle and bootstrap composition |
| Presentation | `src/ui/` | typed loopback API rendering; no filesystem or subprocess |
| Transport | `src/services/api/` | parse/validate/authorize/serialize routes |
| Domain | `src/services/` | jobs, artifacts, projects, workflows, managers |
| AI modules | `src/modules/` | manifest, adapter, worker and capability contract |
| Platform | `src/platform/` | roots, containment, reparse/process boundaries |
| Shared | `src/shared/` | versioned schemas and low-level contracts only |

The allowed dependency direction is UI → API → services → modules → platform /
shared. Shared never imports a service, module or UI package. New modules are
registered through validated data manifests, not arbitrary Python imports.

## Source and data roots

`src/platform/paths.py` defines `APP_ROOT` and `DATA_ROOT`. `DATA_ROOT` may be
the current legacy single-root installation (`D:\LocalAIHub`) or a separate
machine-local directory selected through environment/configuration. Existing
Models, Environments, runtime, Output, Projects, Backups and Config are reused
in place; the foundation does not migrate or duplicate them.

## Module state and evidence

The standard states are `NOT_INSTALLED`, `INSTALLING`, `INSTALLED_UNVERIFIED`,
`PARTIAL`, `OPERATIONAL`, `UNAVAILABLE`, `BROKEN`, and `UPDATE_AVAILABLE`.
Presence of a file or runtime is only an installation observation. A module is
`OPERATIONAL` only after a matching, bounded smoke receipt is recorded.

## Managers

- Model Manager owns catalog metadata, safe discovery, install plans,
  installation receipts and references. It never performs inference.
- Runtime Manager owns executable/environment metadata and bounded leaf
  inspection. It never owns model weights or changes drivers/CUDA.
- Module Manager composes manifest + runtime + model + evidence state.

Both new managers are offline/read-only at startup. Real installation is an
explicit future action with source, license, checksum, disk and user approval.

## Compatibility

Existing `job.v2`, `node-run.v2`, opaque artifact IDs, project/backup/settings
contracts and legacy imports remain unchanged. Existing `src/shared/paths` is a
compatibility facade over the new platform registry. `Services/Whisper/` is a
first-party backend implementation; `src/modules/whisper/` is the Hub adapter
and contract boundary.
