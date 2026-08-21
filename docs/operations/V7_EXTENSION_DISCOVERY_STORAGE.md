# V7 Extension Discovery Storage Safety

Extension discovery is a static metadata operation. It reads only the
repository-managed `extensions/` tree and the fixed `Config` extension
configuration leaves. It never imports extension code, launches an entrypoint,
starts a process, performs a network request, installs a dependency, or claims
runtime readiness. Capability and Module Manager projections remain
`execution: "not_run"` and `dry_run: true`.

## Managed-location boundary

Production discovery uses the repository root and its fixed `extensions/`
child. Configuration uses the same repository root and the fixed
`Config/extensions.local.json` followed by the tracked example fallback. A
test may provide an explicit task-owned fixture root; a caller cannot select an
arbitrary production directory.

Before a read or write, the source validates lexical containment and a
no-follow `lstat` chain for the root, every existing ancestor, the parent, and
the target. Reparse points, symlinks, directory/file type mismatches, escapes,
missing required parents, and identity changes are unavailable outcomes.
After a bounded read, the chain and file identity/stat evidence are checked
again and the small file is read a second time. Same-size replacements and
same-byte replacement races therefore fail closed rather than becoming a
trusted descriptor.

Directory enumeration is bounded. It uses `os.scandir` with a finite entry
limit, accepts only regular files and directories, and rejects reparse-backed
entries, unsafe descriptor depth, nested escapes, and oversized directories.
JSON is strict UTF-8: duplicate keys, nonfinite constants, malformed JSON,
invalid UTF-8, and oversized files are rejected. Public issue messages use
fixed codes and text; local paths, URLs supplied by invalid input, secrets,
commands, callables, exception text, and arbitrary objects are not copied into
issues or storage-failure results. Valid manifest/card metadata retains the
existing schema contract for the existing static preflight callers.

## Scaffold publication

The generator validates the same fixed root and creates a bounded staging
directory under the managed extensions directory. It writes the three static
files with exclusive creation and flushes each file. The staging directory is
renamed into the new extension identifier only after all files are complete,
the destination is still absent, and the parent/staging identities remain
valid. Existing extensions are never overwritten.

On any write, identity, reparse, or publication failure, only the task-created
staging directory (or a destination still proven to have the staging identity)
is eligible for cleanup. Ambiguous or changed objects are preserved for manual
review. A failed scaffold does not return a successful result or leave a
partial public extension tree.

The result shape remains the existing bounded `{status, extension_id, files}`
success contract, with relative fixed file names only. Discovery and config
fallback shapes remain compatible with the extension manifest and capability
planner schemas. This contract does not change API routes, UI payloads,
shared schemas, Module Manager, capability gateway, Project Workspace, Backup,
Diagnostics, Workflow, Update, artifact, producer, or reservation code.
