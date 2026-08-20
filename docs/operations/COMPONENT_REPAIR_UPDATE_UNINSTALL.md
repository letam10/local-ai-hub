# Component repair, verification and reuse contract

Component state is derived from a server-owned versioned catalog context, the
fixed Hub roots and a bounded receipt.  V1 model/runtime callers retain their
legacy schema/revision binding.  Normalized V2 callers explicitly carry
`v7-production-catalog.v2`, the catalog version, catalog fingerprint and the
normalized source identity (or an explicit null); component revision/version is
never substituted for catalog version and source identity is never fabricated.
The catalog version and fingerprint may be shared by a normalized catalog, but
the source identity is bound independently for each model or runtime record.
The browser supplies only an opaque component or plan ID.  It cannot supply a
root, path, file list, source, command, executable or credential.

## Roots and leaves

The single resolver maps models to `models_root/<component_id>`, runtimes to
`runtime_root`, environments to `environments_root`, and
`external_managed` runtimes to the fixed Hub-owned `runtime_root/external`
boundary.  Unknown root classes, component IDs/types, caller roots, absolute
or escaping leaves, missing leaves and any symlink/junction/reparse ancestor
are refused with a fixed code.  Public results and receipts contain only
relative catalog leaves and location classes; they never return the resolved
filesystem path.

## Fast inspection and explicit verification

Startup and ordinary catalog snapshots use the fast inspector.  It checks only
the fixed catalog leaves with bounded presence, size and `mtime_ns` metadata,
then compares an existing receipt's component, catalog schema/revision/
fingerprint and opaque source binding.  It never hashes a file, reads a whole
file, downloads, starts a process, imports a model/runtime or claims
operational capability.  A complete fixed selection without a bound receipt is
`DISCOVERED`; a legacy component-install V2 receipt is readable as
`INSTALLED_UNVERIFIED`.

Verify Installation, Reuse Existing and receipt-only Repair are explicit
confirmation actions.  The deep verifier streams SHA-256 in bounded chunks for
every selected leaf, regardless of file size.  It compares size, `mtime_ns`
and the file identity before and after the read.  Any missing leaf, catalog
size/digest mismatch or race is a fixed unavailable/conflict result and does
not write a receipt or modify installation bytes.

`INSTALLED_VERIFIED` requires both an expected catalog size and expected
SHA-256 for every leaf.  A size-only or digest-less catalog is measured-only /
`INSTALLED_UNVERIFIED`; no reuse or catalog metadata can produce `OPERATIONAL`.
Runtime/model capability evidence remains a separate contract.

## Receipt V3 and compatibility

`component_install_receipts.json` uses the strict closed
`component-install-receipts.v3` schema.  It binds component ID/type,
catalog schema/revision/fingerprint, an opaque source identity, root/location
class, relative leaves, bounded timestamps, state and `operational: false`.
For V2 the schema/revision/fingerprint/source fields are copied only from the
explicit server-owned catalog context; a component's own revision is not used
as the catalog revision.
Every confirmed verify, reuse, repair, install or maintenance action rebuilds
the current server-owned binding and compares all four fields with the plan.
Missing or changed V2 context returns `stale_binding` before deep verification,
receipt writes or any executor path.
Verified leaves additionally carry observed/verified size, SHA-256 and the
`sha256` algorithm.  Duplicate keys, unknown fields, absolute paths, URLs,
commands, executables, secrets and oversized documents fail closed.  Writes
use a same-directory temporary file, UTF-8 JSON, flush/fsync and atomic
replacement; a failed write leaves the previous receipt intact and cleans its
temporary file.

V2 receipts remain readable for compatibility, but are always legacy/unverified
until an explicit deep verification produces a V3 binding.  Receipt-only
repair/reuse never copies, overwrites, deletes or relocates component bytes.
The legacy maintenance/update/uninstall behavior remains separate; existing
artifact and installer transport is not changed by this contract.

All checks are static or bounded local metadata checks.  No package/runtime
smoke, model load, provider, server, browser, GPU, download or installation
operation is implied by a plan or a receipt.
