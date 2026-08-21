# V7 Node Studio draft privacy and storage safety

This contract covers the crash-recovery draft seam used by Node Studio. It is
an editor persistence boundary, not an execution or readiness signal. A draft
operation never imports a node module, starts a provider, runs a subprocess, or
promotes a capability; refusal projections remain `execution=not_run` and
`dry_run=true`.

## Managed location

Production drafts are fixed server-owned files under `Config`:

```text
Config/node_studio_draft_<validated-scope>.json
```

The route accepts a scope identifier, never a path or storage root. A scope is
lowercase ASCII and bounded to 32 characters, beginning with a letter and then
using only letters, digits, `_`, and `-`. Empty, uppercase, whitespace,
drive/UNC/POSIX, traversal, encoded-separator, and overlong values are refused;
they are never sanitized into another filename. This makes the opaque
`draft_<scope>.json` identifier safe to return without returning a path.

Before reading, creating, replacing, or deleting a draft, the state boundary
checks the fixed Config root, its existing ancestors, the parent, the target,
and task-owned temporary files with `lstat`/no-follow checks. Reparse points,
symlinks, directories in a file position, lexical escapes, missing parent
chains, and identity or byte drift are fail-closed. A first use may use an
already-existing safe Config parent; the draft operation does not create an
unsafe parent chain.

## Envelope and graph

The persisted envelope is closed and bounded:

```json
{
  "draft_schema_version": 1,
  "owner": "node_studio",
  "draft_id": "draft_image.json",
  "scope": "image",
  "graph": {"nodes": [], "edges": []}
}
```

The file is bounded UTF-8 JSON with duplicate-key and nonfinite-number
rejection and a deterministic compact representation. Unknown envelope fields,
invalid UTF-8, malformed JSON, oversized values, unsafe graph keys/values, and
path/URL/secret/command-shaped material are refused without echoing the input.
Editable graphs use the existing `validate_graph(..., require_runnable=False)`
semantics. Forward-compatible unknown node-type records may remain editable;
structural, topology, property, and unsafe-value failures do not persist.

Persistence writes a same-directory task temporary, flushes and fsyncs it, and
atomically replaces the target only after the root/parent/target and temporary
identities and expected bytes still match. A post-replace check can restore the
prior bytes only while the new leaf and managed chain remain identity-attested.
If that proof is lost, the result is manual review and no public success claim.

## Public result boundary

`GET` returns only a validated opaque draft projection containing `draft_id`,
the safe scope, and the validated editable graph. Legacy `draft_path` fields,
absolute paths, URL values, exception text, secrets, commands, and raw hostile
objects are never projected. `POST` returns only a bounded accepted/status/
`draft_id` result; refusal carries a fixed reason and the truthful
`not_run`/`dry_run` markers. `DELETE` reports missing drafts as not found and
propagates manual-review/conflict failures instead of claiming completion.

Clear is identity-attested: only a regular, non-reparse leaf with the exact
server envelope, matching scope, stable bytes, and stable file identity can be
removed. Foreign, malformed, replaced, directory, or reparse-backed drafts are
preserved for manual review. Temporary cleanup is performed only for a
task-created file whose own identity and safe parent chain are still proven.

The existing route IDs and paths remain unchanged. These checks are static
storage and projection guarantees; they do not claim a working Node Studio
runtime, provider, model, GPU, or browser execution path.
