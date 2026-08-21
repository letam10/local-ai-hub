# V7 update execution binding

V7 update and maintenance activation is a server-owned, plan-first mutation
boundary. A plan is coordination metadata only; it never makes a catalog
candidate authoritative by itself.

Before staging, before activation or rollback, and immediately before a
receipt write, the executor rebuilds and compares the current typed
`CatalogBindingContext`. The comparison covers the catalog schema, catalog
revision, catalog fingerprint and normalized component source identity (which
may be explicitly null for V2), together with the component type/id, catalog
record revision, install strategy, supported candidate revision and an opaque
fingerprint of the server-owned update candidate. A missing, malformed, wrong
family or changed value returns one fixed path-free refusal:
`catalog_binding_unavailable`, `catalog_schema_unsupported`, or
`catalog_binding_stale`. It performs no downloader, staging, copy, rename,
delete or receipt operation.

V2 updates require the current server-owned
`v7-production-catalog.v2` context, with `catalog_version` as the typed
catalog revision. The full typed binding is supplied to the existing receipt
writer, so a V2 update cannot produce a V1-looking receipt with only a V2
fingerprint. Legacy model/runtime callers retain explicit V1 bindings; a V2
record is never synthesized from a legacy record's revision or fingerprint.

Rollback uses the same current binding and the exact in-memory update plan
when available. Binding drift aborts before the active/rollback root swap.
If a late binding or receipt failure occurs after an activation swap, the
executor restores the pre-update roots and leaves no new public receipt.

Successful update/rollback results remain `INSTALLED_UNVERIFIED` and
`operational: false`; this contract performs no process, model, runtime or
network work and does not promote capability readiness. Public plan data
contains only bounded identifiers, statuses and fingerprints. Candidate
paths, URLs, commands, credentials and source payloads remain server-owned
and private.
