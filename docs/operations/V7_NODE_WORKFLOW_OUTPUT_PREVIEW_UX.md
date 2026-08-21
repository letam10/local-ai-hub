# V7 Node Workflow Output Preview UX

## Scope

The Node Studio Inspector presents output that the server has already published
for the selected node. This package changes only the presentation layer. It
does not add an endpoint, alter artifact publication, resolve a local path, or
run a provider, worker, browser download, or media operation.

## Bounded artifact projection

The Inspector collects the selected node's existing public `output` values and
the matching run-level `provenance` entries. Collection is deterministic in
source order, deduplicates opaque `artifact_*` IDs, and is bounded to a fixed
number of inspected values and displayed artifacts. A malformed first value
cannot hide a later valid value. Only the existing opaque artifact ID and
server-owned `/api/artifacts/artifact_*` URL contract can produce a preview.

Names, media types, sizes and mask flags are bounded and type-checked. Paths,
URLs, secrets, commands, callables, object representations and unknown unsafe
values are omitted. No raw worker output or provenance object is inserted into
HTML. The existing `data-preview-artifact` contract remains the sole preview
transport, with native image/video/audio elements and metadata preload for
video/audio. Metadata-only and mask artifacts remain truthful fallback/metadata
previews rather than being treated as playable media.

## Truthful states and layout

Zero-artifact states distinguish completed-without-artifact, partial, failed,
unavailable and not-run/unknown snapshots using fixed copy. Node presence alone
does not imply execution or an artifact. Multiple artifacts render as a
bounded, responsive list with per-item name, type, size and an existing preview
button. The graph editor keeps one LiteGraph canvas and its existing palette,
Inspector, focus/collapse, draft, picker and run behavior. Graph preview cards
use the existing responsive breakpoints and wrap long metadata without
horizontal overflow.

The list is a view of already-public server state. It is not runtime evidence,
readiness evidence, a download authorization, or a claim that an operation ran.
