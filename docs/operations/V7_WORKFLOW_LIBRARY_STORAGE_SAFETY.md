# V7 Workflow Library storage safety

WorkflowLibraryStore owns one server-managed metadata leaf:
Config/workflow_library.json. It is a declarative workflow library only. It
does not execute graphs, import a runtime, start a process, inspect media, or
resolve a client-selected path.

## Location authority

The production store uses the code-owned Config root. Bounded tests may inject
a temporary root, but the injected root is still an explicit storage boundary.
The store first performs lexical containment and no-follow lstat checks for
the root, every existing ancestor, the parent and the target leaf. Reparse
points, symlinks, junctions, wrong object types, missing parents, lstat
failures and resolved containment escapes become a fixed
recovery_required/manual-review result. No local path or exception text is
returned.

The first-use leaf may be absent only when its complete parent chain is
already present, regular and safe. The store never creates a missing parent
chain as a side effect of a read or write.

## Read, compare and replace

Every read is followed by a location and identity recheck. Writes use a
same-directory temporary file, bounded file and directory identity signatures,
flush/fsync, expected-byte comparison and an atomic os.replace. The root,
parent, target and temporary file are revalidated immediately before replace.
Target, parent, root or temporary identity drift—including a same-byte
replacement—stops the operation with a scrubbed recovery/conflict result.
Previously committed or outside bytes are not overwritten by recovery logic.
Only a task-created regular temporary file whose identity and safe parent are
still proven may be removed.

Optimistic expected_revision and expected_bytes checks remain active for
multiple store instances. Invalid UTF-8, duplicate keys, nonfinite numbers,
malformed schema, directory targets and read/write failures preserve the
existing bytes and require review. plan_migration remains a pure dry-run;
confirm_migration uses the same revision and storage guards as import.

## Public projection and boundary

List, get, export and mutation results retain the existing schema, revision,
fingerprint and opaque workflow projection. Recovery status uses fixed
status/reason/action text only. It never reflects absolute paths, URLs,
secrets, commands, temporary names, raw exceptions or arbitrary objects.

This is static/temp-fixture storage-safety evidence. It does not claim runtime,
media, GPU, model, provider, network, installation or graph execution
readiness.
