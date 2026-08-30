# Post-V8 Product Experience V2 — Milestone 4

## Purpose and owner

`src/services/product_experience_v2.py` is the single owner of the finite
Product Experience V2 catalog.  Its API transport is
`src/services/api/routes/product_experience_v2.py`; the Dashboard and shell
are consumers only.

The catalog joins existing, real Hub surfaces: Dashboard, workspaces, Jobs,
Models & Storage, Projects, Settings and Diagnostics.  It does not replace
their underlying services or invent an operational state.

## State and API

There is no new persistent state.  Onboarding is a skippable, reusable seven-
step guide for system, GPU, storage, models, runtimes, external applications
and privacy/update settings; it has no browser-local completion flag. Global
search uses bounded projections of server-owned models, tools, projects,
workflows, jobs, artifacts and a fixed Settings result. It never exposes
paths, secrets or artifact bytes and never searches arbitrary filesystem data.

The shell also provides an accessible `Ctrl+K` command palette with fixed
navigation commands (open Models, Diagnostics, Projects, update/storage
review, or Search Artifact). Commands navigate or request metadata only; they
do not silently start an update, scan, workflow or destructive action.

- `GET /api/product-experience/v2` — dashboard/onboarding/settings and
  diagnostics contract.
- `GET /api/product-experience/v2/onboarding` — finite guide.
- `GET /api/product-experience/v2/search?q=...` — bounded product-surface
  search (maximum eight results, sourced from safe Hub metadata projections).

All responses are `execution=not_run`, `dry_run=true`.  Settings still take
effect only after the existing server accepts **Áp dụng & lưu**.

## Failure and recovery

If the M4 endpoint is unavailable, Dashboard keeps the existing status and
shows that onboarding is loading/unavailable; it does not claim completion.
The search panel shows no result. Recovery is to refresh the loopback Hub
snapshot and use the existing Diagnostics route. Skipping onboarding only
changes the current in-memory view. Neither behavior saves settings, starts a
provider, launches a desktop application or performs a filesystem scan.

## Tests

`tests/test_post_v8_product_experience_m4.py` verifies the finite catalog,
path-free bounded search, nonpersistent onboarding, router binding, route
inventory and Hub-API-only Dashboard/search integration.
