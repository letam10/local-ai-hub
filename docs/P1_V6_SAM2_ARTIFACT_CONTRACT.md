# SAM2 opaque artifact contract

SAM2 is a direct-worker integration, not an independent GUI or a public local
filesystem API. Its configured runtime remains `partial` with
`execution=not_run` until a separately authorised bounded functional smoke
records current matching evidence.

## Input boundary

The adapter accepts exactly one Hub artifact identifier in
`source_artifact_id` (or the compatibility alias `asset_id`). It resolves the
identifier server-side, checks its declared media type, and rejects all raw
input paths, output paths, commands, executable/runtime/model overrides and
other filesystem-shaped worker fields before a worker is launched.

The composed Grounding DINO-to-SAM2 path preserves the opaque source artifact
identifier; it never re-injects a workstation path into SAM2.

Immediately before image decoding, model import, GPU selection, output
creation or subprocess work, the worker independently requires all of the
following:

- the configured SAM2 runtime and checkpoint are existing non-reparse leaves
  below the Hub-owned `runtime` tree;
- the source is an existing non-reparse artifact leaf below one of the fixed
  Hub roots: `Temp/uploads`, `Output`, or `Archive`;
- output is a new task-owned child below `Output/SAM2`.

Any lexical escape, missing leaf, junction/symlink/reparse ancestor, or output
creation failure returns a finite error and does not load the model or create
an output directory.

## Output boundary

The worker returns generated mask/overlay or tracking preview/mask paths only
in the private `outputs` list. It does not emit `mask`, `preview`, `video`,
`masks`, raw input paths, exception text, device details, or local paths as
public metadata.

Job Manager is the sole publication boundary. It validates each Hub-owned
candidate, records the terminal job provenance, and replaces the private list
with opaque artifact records before the job result is persisted or exposed.
Cancelled, unavailable, failed, or metadata-only results do not publish an
artifact.

This source contract creates no model, environment, runtime, download, smoke,
or capability evidence. A successful static contract is not a SAM2
operational claim.
