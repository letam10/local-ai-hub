# V7 Project Workspace storage safety

`CreativeProjectManager` owns the server-managed workspace metadata leaf
`Config/creative_workspace.json`. It stores projects, recipes, compare boards,
opaque artifact references and bounded creative metadata. It is a persistence
boundary only: it does not execute a graph, resolve a client path, start a
process, inspect a model, or claim runtime/media readiness.

## Fixed storage authority

Production instances use the code-owned Config root and the fixed workspace
leaf. A caller cannot select a production root. The constructor path is a
temporary, task-owned fixture seam for tests; it is still checked as a fixed
lexical boundary and never becomes a client storage API.

Before load, list, get, export, import, mutation, draft creation or draft
clear, the manager uses lexical containment plus no-follow `lstat` checks for
the root, every existing ancestor, the parent and the target leaf. Every
checked directory must be a regular directory without a symlink, junction or
other reparse attribute. The workspace leaf may be absent only for first use
when its complete parent chain already exists and is safe. The manager never
creates a missing parent chain as a side effect.

## Read and write contract

Workspace JSON is bounded, UTF-8, duplicate-key-free and rejects nonfinite
numbers before normalization. Oversized, malformed, non-object, directory,
reparse, lstat, read or schema-invalid state becomes a fixed
`recovery_required` result. The existing bytes are preserved and the public
recovery projection contains only fixed safe messages; it does not echo paths,
URLs, secrets, commands, exception text or arbitrary objects.

Mutations retain the existing schema, optimistic revision/expected-byte
behavior and opaque artifact references. They serialize to a same-directory
temporary file, flush and fsync it, then revalidate the root, parent, target
and temporary identity immediately before an atomic `os.replace`. Identity or
content drift, including same-byte replacement, target/parent reparse, a
directory escape, or a read/write/replace failure returns a bounded
`manual_review`, `conflict` or `write_failed` refusal. No recovery path
overwrites prior or outside bytes. Cleanup is limited to a task-created
temporary regular file whose identity and safe parent are still proven.

## Autosave drafts

Autosave derives a collision-free server-owned draft name only from a valid
opaque project ID. Invalid or path-like IDs are rejected before persistence.
The result contains an opaque draft identifier, never an absolute `draft_path`.
Existing drafts must have the matching project ID, schema and graph shape
before replacement or removal. A foreign, malformed, replaced, reparse,
directory or identity-drifted draft returns `accepted=false` with a bounded
manual-review result and is not deleted. Persistence failures likewise return
`accepted=false` without echoing the temporary filename or exception.

## Compatibility and limits

Creative workspace CRUD, import/export, recipe, asset, collection, compare,
revision and recovery result shapes remain unchanged except for the safer
bounded draft result. The existing Node Studio draft store and API/context
layers remain separate and are not broadened by this package. This package
uses only static/temp-fixture tests; it makes no server, browser, network,
subprocess, runtime, GPU, model, provider, download, install or media
execution claim.
