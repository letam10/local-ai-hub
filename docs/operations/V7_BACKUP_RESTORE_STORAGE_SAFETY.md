# V7 Config backup and restore storage safety

`BackupManager` is the server-owned mutation seam for the local Config backup
surface. The contract covers only the fixed Config metadata members:

- `settings.json`
- `creative_workspace.json`
- `workflow_library.json`
- bounded Node Studio draft members

Models, Environments, runtime, Output, user media, and arbitrary client paths
are outside this boundary.

## Path authority

The production backup directory is `CONFIG_ROOT/backups`. Before any read,
directory creation, archive staging, or replacement, the manager validates the
Config root, backup root, archive, target, parent, and temporary objects with
no-follow `lstat` evidence. Every existing original ancestor must be an
ordinary directory or regular file as appropriate, and resolved containment is
checked only after that no-reparse validation. A missing first-use leaf is
allowed only beneath a validated server-owned parent.

Each guarded object carries bounded identity and metadata evidence. The
evidence is rechecked immediately before archive publication and every restore
commit. Same-byte replacement, reparse appearance, parent replacement,
identity drift, or ambiguous containment fails closed. Task temporary files and
staging directories are removed only after their own safe identity and parent
checks succeed.

## Closed archive contract

The archive manifest remains the existing versioned backup contract, but its
shape is closed and bounded. Duplicate JSON keys, nonfinite values, unknown
fields, wrong types, invalid SHA-256/size metadata, unknown members, traversal,
absolute/UNC/drive paths, backslashes, directories, symlink/reparse entries,
duplicate ZIP members, oversized entries, and decompression over the fixed
bound are rejected.

Public inspection and plan projections expose only opaque backup/plan IDs,
fixed categories, bounded counts, statuses, and fixed safe codes. Draft
members use the fixed `drafts` category/count; their archive-controlled names
are never public. Internally, the archive namespace `drafts/<safe-name>` maps
back to the existing root-level Config draft leaf, and never creates an
arbitrary `Config/drafts` restore path. Archive member names, local paths,
ZIP/library errors, exception text, URLs, secrets, and client-supplied archive
values are not reflected.

## Restore transaction

Restore is plan-first and requires explicit confirmation. The complete target
set is revalidated and staged before the first Config replacement. A failure,
identity race, reparse change, or write error rolls back already-applied
members using their identity-bound prior bytes. Every successful replacement
is recorded before its post-commit guard, so a guard failure after a replace
still enters rollback. If rollback cannot be proven safe, the result is
`recovery_required`/manual review with
`accepted=false` and `verified=false`; the manager never reports a partial
restore as accepted.

Existing opaque route/status semantics, stale-plan conflict behavior, same-
directory fsync and atomic replacement, and the read-only API transport remain
unchanged. This is a static storage-safety contract; it does not start a
server, probe a network, run a model, install a component, or promote any
runtime capability.
