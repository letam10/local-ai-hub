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
and private. The server keeps execution plans in a private map and exposes a
separate lookup projection, so the existing read-only plan lookup cannot
return the private record, typed binding, fingerprint payload or staged
candidate.

Maintenance uninstall removes only the selected component record and writes
the same receipt envelope that was already present. In particular, an
uninstall against a V2-bound component preserves a
`component-install-receipts.v3` envelope and all unrelated record provenance;
it never downgrades the store to the legacy V2 envelope. A missing receipt is
not materialized as a legacy store. Malformed or unsupported receipt state
refuses before deletion. Known leaves are moved into a private, reversible
task-owned quarantine, the current binding is checked again after the move,
and only then is the receipt envelope changed. The receipt read captures a
bounded regular-file identity and metadata signature; immediately before and
inside the atomic replacement, the path must still be non-reparse with the
same identity and raw bytes. Same-byte replacement with a symlink/reparse or
any other identity drift moves the leaves back and leaves the receipt state
unchanged.

Managed model paths are resolved through the server-owned data-root to
`Models/<component>` chain. Every existing ancestor is checked for ordinary
directory identity and reparse state before inspection, directory creation,
activation, rollback, or restoration, and the chain is checked again at each
rename boundary. An absent component leaf is therefore not permission to
follow a replaced `Models` parent or an external junction. During uninstall,
each catalog leaf carries a bounded lstat identity, size, and streaming hash
attestation; a same-size or same-byte path replacement is a fixed
`uninstall_target_changed`/manual-review refusal and is never moved as the
managed component.
