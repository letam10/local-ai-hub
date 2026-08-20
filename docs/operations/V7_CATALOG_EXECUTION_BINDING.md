# V7 catalog execution binding

V7 production install executors are reached only through an explicit lifecycle
plan and confirmation. The lifecycle passes a typed, server-owned
`CatalogBindingContext` into the model or runtime archive executor.

For `v7-production-catalog.v2`, the binding is the exact current tuple:

- `catalog_schema = v7-production-catalog.v2`;
- `catalog_revision = catalog_version`;
- the current catalog SHA-256 fingerprint; and
- the component's normalized `source_identity`, including explicit `null`.

The executor validates that tuple before reading the staged payload or archive,
before staging/copy/extraction, and again immediately before the receipt write.
Missing, malformed, stale, wrong-family, or V2-without-binding calls return a
fixed `catalog_binding_stale` or `catalog_schema_unsupported` refusal with
`execution: not_run` and `dry_run: true`. They do not touch managed bytes or
replace a receipt.

V1 callers must pass an explicit `model-catalog.v1` or `runtime-catalog.v1`
binding. V1 is not silently upgraded to V2, and V2 is never coerced to a V1
receipt. Successful installation writes an `INSTALLED_UNVERIFIED` receipt with
the exact binding; it does not establish runtime capability or `OPERATIONAL`
status. A separate bounded capability check remains required.

The lifecycle's fixture-only legacy install/maintenance helpers are explicitly
V1-only as well. A V2 plan is refused with `catalog_schema_unsupported` before
those helpers can construct a legacy manager or mutate fixture state.

The binding seam does not change the production catalog schema, download policy,
archive safety, root containment, rollback cleanup, or receipt store. This
contract is static/source-level coverage; it does not authorize a download,
installation, server/browser run, model load, GPU workload, or runtime smoke.
