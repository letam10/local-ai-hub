# V7 Phase 2 — clean clone and managed component installation

This package keeps the V7 source checkout portable and separates it from
machine-local data. `src/platform/paths.py` resolves `APP_ROOT` and
`DATA_ROOT`; `LOCALAIHUB_DATA_ROOT` enables a split checkout while an existing
single-root installation is discovered and reused.

## Core bootstrap

`scripts/bootstrap_core.ps1` and `scripts/bootstrap_core.py` resolve a managed
Core Python environment, the legacy `Environments/hub` environment, or a
supported development Python. They inspect WebView2 separately from the
Python `webview` binding, create only missing Core roots/configuration, and
write an atomic local receipt. `--no-config` is inspect/plan-only. Optional AI
models, runtimes, drivers, CUDA and providers are never installed at startup.

## Component lifecycle

Component state and install-job state are separate. The server exposes fixed
component IDs and opaque install/import/verify/maintenance plans. A plan
fingerprints catalog and observed state; confirmation rechecks that state.
Browser payloads cannot provide URLs, paths, commands, executables, hashes or
file lists. The current tracked catalogs intentionally remain manual-review
only until a pinned source, license, size and digest are reviewed.

The shared installer provides trusted-source policy, bounded streaming,
resumable staging, cooperative cancellation, size/disk/checksum checks,
reparse-safe archive extraction and atomic no-overwrite publication. Model and
runtime managers discover bounded known leaves, preserve existing data, and
write receipts only after synthetic/future managed finalization. Native model
selection is represented by a short-lived opaque `selection_id`.

## Maintenance and evidence

Repair, update and uninstall are plan-first. Shared dependencies, user media,
Projects, Output, Config and Backups are preserved. A file or receipt does not
prove inference: only a matching bounded runtime evidence receipt may promote
an installed component to `OPERATIONAL`. Clean-clone tests use tiny fixtures and
local HTTP only; no real model download, AI inference or GPU benchmark is part
of this source package.
