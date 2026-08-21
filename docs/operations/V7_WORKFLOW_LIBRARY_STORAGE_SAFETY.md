# V7 Workflow Library Storage Safety

The Workflow Library is server-owned declarative metadata. Its production
target is the fixed `Config/workflow_library.json` leaf below the configured
Hub data root. A browser payload never supplies this storage path, a recovery
path, a command, a callable, or a replacement file.

## Location and identity boundary

Every read, list, detail, export, save, delete, import, and confirmed migration
validates the target parent and every existing ancestor with `lstat` and the
platform reparse check. Lexical traversal, resolved containment escape,
symlink/junction/reparse roots, non-directory parents, directories in place of
the leaf, and unknown `lstat` state return a fixed `recovery_required`
projection. The local path and the underlying exception are never returned.

The normal first-use case permits an absent library leaf only when its complete
parent chain is already a regular, non-reparse directory chain. The service
does not create a missing or ambiguous parent as a side effect of a browser
request. Test-only temporary roots remain supported through the existing
injected `WorkflowLibraryStore(path)` seam and receive the same no-follow
checks.

The store records bounded target, parent, and temporary-file identities. The
target identity and bytes are compared before replacement; parent identity is
rechecked after temporary creation; and the temporary file is rechecked before
replacement. Same-byte replacement is still a conflict when file identity
changes. A reparse appearance, identity drift, directory target, or failed
preflight preserves the prior and outside sentinel bytes. Temporary cleanup is
attempted only when the task-created file and its safe parent still have the
expected identity.

Writes retain the existing workflow-library contract: canonical JSON,
same-directory temporary storage, flush/fsync, atomic `os.replace`, and
optimistic expected-byte revision conflict handling. No arbitrary scan,
overwrite recovery, delete recovery, subprocess, import, socket, network,
runtime, model, Output, or Artifact Store action is introduced.

## Data and runtime boundary

`src/shared/schemas/workflow_library.py` remains the closed declarative schema
and continues to reject unsafe graph fields, paths, URLs, secrets, commands,
callables, duplicate JSON keys, nonfinite numbers, oversized input, and invalid
UTF-8. `plan_migration` remains a pure dry-run function; only explicit
confirmation with the current library revision can write validated metadata.

This contract says nothing about graph execution or runtime readiness. Saving a
valid workflow is metadata persistence only; it does not start a node, worker,
provider, model, media operation, or external process.
