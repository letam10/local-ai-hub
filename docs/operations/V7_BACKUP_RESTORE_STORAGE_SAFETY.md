# V7 Config Backup and Restore Storage Safety

`BackupManager` is the server-owned mutation seam for local Config backups.
The contract covers only these fixed JSON leaves:

- `settings.json`
- `creative_workspace.json`
- `workflow_library.json`
- bounded Node Studio draft leaves

Models, Environments, runtime, Output, user media, and client-selected paths
are outside the boundary.

## Path authority

Production storage is `CONFIG_ROOT/backups`. Before any read, directory
creation, archive staging, restore target access, temporary-file creation, or
replacement, the manager validates the full existing ancestor chain with
no-follow `lstat` evidence. Reparse points, symlinks, wrong object types,
lexical/resolved escapes, missing ambiguous parents, identity drift, and
same-byte replacement fail closed.

Resolved containment is checked only after the original chain has passed the
no-follow checks. Every guarded object is revalidated immediately before
archive publication and each restore commit. Task-owned temporary files and
staging directories are removed only after their own identity and parent
containment remain proven.

## Closed archive and public contract

The versioned archive manifest is bounded and closed. Duplicate JSON keys,
nonfinite values, unknown fields, wrong types, invalid SHA-256/size metadata,
unknown members, traversal, absolute/UNC/drive paths, backslashes, directory
or symlink entries, duplicate ZIP members, oversized entries, and excessive
decompressed size are rejected.

Public inspection and restore plans expose only opaque backup/plan IDs, fixed
categories, bounded counts, statuses, and fixed safe codes. Draft archive
members use the fixed `drafts` category/count and never expose their archive
controlled names. Internally, `drafts/<safe-name>` maps back to the existing
root-level Config draft leaf; no arbitrary `Config/drafts` target is created.
Archive names, local paths, ZIP errors, exception text, URLs, secrets, and
client archive values are never reflected.

## Restore transaction

Restore is plan-first and requires explicit confirmation. The complete target
set is validated and staged before the first Config replacement. Every
successful replacement is recorded in the rollback ledger before its
post-replace guard. A later guard failure, write failure, archive race, or
reparse change therefore restores all identity-proven prior bytes or returns
manual review with `accepted=false` and `verified=false`.

The manager never skips a failed candidate and claims a partial restore. The
existing opaque API/context/routes and workflow/update/diagnostics boundaries
remain unchanged. This is a static storage-safety contract; it does not start
a server, probe a network, run a model, install a component, or promote a
runtime capability.
