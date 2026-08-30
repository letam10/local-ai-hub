# Post-V8 Phase 3 — Model Manager V2

Model Manager V2 is the typed, path-free inventory and planning surface for
catalog-backed models. It composes the existing bounded V7 production catalog,
Capability Graph V2, Component Lifecycle Engine V2, and durable V8 plan
authority. It does not replace the existing model catalog, import executor, or
V8 confirmation flow.

## Public model record

Every record includes these stable public fields:

- `model_id`, `display_name`, `family`, `provider`, `purpose`, `source`, and
  `license`;
- `format`, `precision`, `size_bytes`, opaque `files`, and an opaque
  `canonical_location` (`models_root` plus `model:<id>`, never a path);
- `installed`, `verified`, `verification_state`, `runtime_compatibility`,
  `vram_estimate_mb`, `ram_estimate_mb`, `last_verified`, and `checksum`;
- `update_available`, exact bounded duplicate/move analyses, `reason`, and
  `next_action`.

Categories are normalized to `Image`, `Video`, `Audio`, `Vision`, `LLM`, or
`Utility` for filtering. An `INSTALLED` fixed-leaf observation remains
`INSTALLED_UNVERIFIED` in this V2 projection unless current verification or
operational evidence explicitly proves more. File-relative names, absolute
paths, source URLs, credentials, and model bytes never leave the server.

## Duplicate, move, and download guard

The engine compares only supplied server-owned, bounded observations. It can
report `candidate_detected` or `possible_moved` using opaque location IDs, but
does not claim a full filesystem duplicate/move scan took place. When a model
is already installed/observed or a duplicate candidate exists, the download
preflight is blocked and instructs the user to use the existing record or a
native import/reuse workflow instead of duplicating multi-GB bytes.

## Supported actions

```text
PLAN_INSTALL       -> existing V8 plan_install
IMPORT_EXISTING    -> existing V8 native-selection import plan
REGISTER_EXISTING  -> existing V8 receipt-only reuse plan
VERIFY_CHECKSUM    -> existing V8 bounded verify plan
REPAIR_REGISTRY    -> existing V8 receipt-only reuse plan
SAFE_REMOVE        -> existing V8 uninstall plan
CHECK_DUPLICATES / CHECK_MOVED / REVIEW_LICENSE / CHECK_COMPATIBILITY
                    -> read-only bounded metadata results
UPDATE_METADATA    -> explicitly unavailable until Configuration V2 owns a
                      revisioned typed metadata writer
```

Each action that may ultimately change data creates only a durable V8 plan
with `execution: not_run` and `dry_run: true`. Confirmation remains the
existing V8 explicit-confirmation route. Browser clients may supply only an
opaque native `selection_id` for `IMPORT_EXISTING`; filesystem paths, URLs,
files, checksums, or registry documents are rejected.

## API and UI

```text
GET  /api/model-manager/v2
GET  /api/model-manager/v2/{model_id}
GET  /api/model-manager/v2/{model_id}/preflight
POST /api/model-manager/v2/{model_id}/plans
```

The Models & Storage page contains a V2 panel with install/verification state,
byte size, VRAM estimate, runtime compatibility, checksum declaration,
duplicate/move status, and buttons that create a plan only. It does not add a
path input, a provider control, a download button that bypasses V8, or an
execution confirmation.

## Scope boundary

This phase does not run a deep scan of `Models`, hash multi-GB weights during
page load, download model files, load models into RAM/VRAM, or alter the
current local registry/configuration. Deep verification and native import are
still explicitly confirmed V8 flows. Revisioned metadata writes belong to the
future Configuration V2 phase.
