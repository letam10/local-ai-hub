# Local AI Hub V7 final productization contract

This document is the final V7 productization boundary. The UI, API, catalog,
installer, desktop shell and owner installation use one architecture. The
catalog is data-driven, fixed-root and path-free at the public boundary.

## Ownership

`src/ui/features/` owns feature presentation; `src/ui/core/` owns bootstrap and
feature registration; `src/services/productization/` composes the production
catalog and one-click lifecycle; `src/services/model_manager/`,
`runtime_manager/` and `component_installer/` remain the single execution
implementations for owner and clean-machine fixtures.

`pages.js` and `app.js` remain compatibility facades during the strangler
migration. They may compose features but must not grow new feature business
logic. New catalog data enters through `/api/productization/catalog` and
opaque plans through `/api/productization/plans`.

## State truth

File presence is `INSTALLED`, not `OPERATIONAL`. Operational status requires a
matching bounded runtime-evidence receipt. Size is read from an installation
receipt or explicit user-requested bounded refresh; otherwise the UI says
`Size unavailable`. Unknown upstream size/checksum is never fabricated.

## Installation contract

`inspect -> plan -> confirmation -> apply -> verify` is one server-owned flow.
The public API accepts fixed IDs only. A real apply must pass source revision,
disk, reparse, process, checksum, license and dependency checks. Tests use the
same model/runtime managers with tiny fixture leaves; no model weights are
committed or downloaded by source acceptance.

## Desktop and release

`scripts/setup_local_ai_hub.ps1` is the canonical clean-clone setup command.
The default is dry-run; `-Apply` is explicit and absent-only. The release
builder emits a Core ZIP without machine-local data and an optional Inno Setup
EXE. Models, Environments, runtime, Output, Config local state, Backups and
Reports are excluded from release artifacts.

## External limitations

Provider authentication, license acceptance, missing official pinned assets and
real GPU smoke are reported as explicit limitations. They are not fake PASS
states. A fresh clone launches without models and displays every supported
catalog entry even when optional capabilities are unavailable.
