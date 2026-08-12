# V5 Unified Workspace & Workflow Library

V5-C defines the product journey from readiness to an intentionally bounded
workflow:

Dashboard -> Project/Workspace -> Capability -> Workflow/Nodes -> Job -> Artifact/Preview

This document describes the current product contract. It does not claim that a
provider, model, image/video backend, or server-owned bridge is operational
without a separately authorized bounded smoke. The Dashboard may display a
server-owned, read-only storage projection; that projection is evidence, not a
write or runtime operation.

## Product contract

The shell has one navigation model and one workspace context. Dashboard shows
readiness, reason, and next action before a user enters a capability. It also
shows fixed C: and D: volume cards with total/free/used figures when readable.
Missing or unreadable volumes remain unavailable/unknown and show no fabricated
figures. A low-space card includes the server-owned next action. Projects and
recipes keep the user-facing context; Hub Nodes edits a declarative graph;
Jobs owns progress and cancellation; artifact preview displays only
server-owned opaque artifact metadata.

Every readiness card uses a truthful state:

- operational: a bounded functional smoke exists for this exact provider and version.
- partial: a typed contract exists, but an adapter or smoke is still missing.
- unavailable: the requested capability cannot be used under the current evidence.
- planned: the capability is described but not wired.
- not_run: no runtime operation was authorized.

Static green tests never elevate one of these states to operational.

## Unified UI state

src/ui/app.js owns a detached view state containing health, components, jobs,
storage, projects, active workspace tabs, and workflowLibrary. Bootstrap carries
only the sanitized C:/D: storage projection from a separate cached metadata-only
snapshot; it never scans managed trees. The browser never chooses a volume or
performs a filesystem scan. The latter is initialized to partial with a reason
and next action. The full legacy/areas summary remains behind /api/storage; a
server-owned bootstrap may replace its projection with validated volume metadata,
and the UI never accepts a client mapping or invents an endpoint.

Navigation keeps every existing route and exposes named controls and active
route semantics. The sidebar has separate desktop collapse and mobile drawer
state, a versioned failure-safe preference, and the shared 980px breakpoint.
Resize and route changes synchronize ARIA state without a focus trap. Escape,
Delete, undo, redo, palette search, inspector, minimap, and keyboard canvas
shortcuts remain owned by the LiteGraph adapter.

## Workflow Library v1

src/shared/schemas/workflow_library.py publishes the closed
workflow-library.v1 and workflow-entry.v1 contracts. A library contains:

- a bounded library revision;
- opaque workflow IDs, title, scope, tags, status, source, and audit times;
- a declarative Hub Nodes graph with bounded node/edge/group counts;
- deterministic canonical ordering and a SHA-256 content fingerprint.

Unknown fields are rejected. IDs, graph entities, strings, arrays, nesting,
non-finite numbers, duplicate JSON keys, invalid UTF-8, paths, traversal,
secrets, credentials, arbitrary URLs, commands, media/blob payloads, and model
files are rejected without reflecting the unsafe value in an error.

The published JSON Schema is closed and keeps required and properties in parity.
Canonical export sorts object keys, workflow IDs, node IDs, edge IDs, and group
values. Semantic fingerprints exclude revision and audit timestamps so a
reorder or a save revision does not masquerade as a content change.

## Local-first persistence and conflicts

src/services/workflow_library/ is a thread-safe metadata store. Its default
state is under the ignored local configuration root; tests inject a temporary
root. Writes use a same-directory temporary file, flush/fsync, and atomic
replace. A failed write leaves the previous state intact. Mutations targeting
the same file share a path lock and compare the bytes read before replacement;
an interleaved writer therefore returns a conflict and the newer bytes remain
untouched.

Every save/delete/import can carry expected_revision. A mismatch returns a
fixed conflict code and a reload/review action; it never silently overwrites a
newer user change. A malformed local file returns recovery_required and is not
replaced automatically. Export/import always returns detached values.

The tracked Config/workflow_library.example.json documents limits and the
local-first policy only. User workflow state, cache, outputs, models, and media
are never tracked.

## Migration from legacy localStorage

Node Studio may still have an existing local draft for recovery. Migration is
user-mediated:

1. Read a bounded detached candidate list supplied by the user action.
2. Build workflow-library-migration.v1 with dry_run: true.
3. Show every accepted fingerprint and every fixed rejection reason.
4. Require explicit confirmation with the current library revision.
5. Import only the reviewed entries; never overwrite or delete localStorage
   automatically.

Malformed candidates produce partial or recovery_required, and the source draft
remains untouched. This package does not inspect a browser profile or
filesystem to discover candidates.

## Hub Nodes and existing workspaces

LiteGraph remains the one canvas editor. Palette/search, typed socket markers,
validation, dirty/downstream propagation, cache/progress/error presentation,
inspector, minimap, preview, A/B compare, and keyboard shortcuts are preserved.
Changing a node, dragging a node, applying a preset, or saving a draft never
starts a graph, worker, GPU, model, FFmpeg, or video operation. Run Graph
remains the explicit execution action and retains existing backend guards.

The stable Node layout CSS contract is intentionally data-attribute driven for
the later LAH 2 lane. On `.graph-editor`, LAH 2 may set
`data-node-palette="collapsed"`, `data-node-inspector="collapsed"`,
`data-node-canvas-focus="true"`, `data-node-preview="expanded"`, or the
`data-node-palette-width` / `data-node-inspector-width` values `narrow` or
`wide`. It may also set the documented `--node-palette-width`,
`--node-inspector-width`, and preview-height variables. With attributes absent,
the current responsive fallback remains active.

The Node Studio adapter keeps exactly one LiteGraph editor and uses its
supported canvas callbacks for plain-click selection, Ctrl/Shift additive
selection, empty-canvas marquee, multi-node movement, middle-drag panning,
arrow-key movement, safe deletion, and undo/persist dirty tracking. A dangling
typed socket may open an accessible, searchable picker at the drop position.
The picker lists only directional compatible ports, reports occupied
non-multi inputs, auto-connects a single unambiguous candidate, and requires an
explicit choice when there is more than one. Escape, outside click, and
incompatible links cancel or reject without adding a node. No vendor LiteGraph
file is modified.

The in-Node-Studio Node Guide is a compact drawer/details panel backed by
versioned localStorage UI state only. It explains typed sockets, selection and
keyboard behavior, truthful validation/dirty/cache/progress/error states,
opaque-artifact preview and recovery, and save/import/export. Template buttons
reuse the existing server-owned preset route; they do not create another graph
engine or registry.

Video vocabulary is deliberately closed and status-aware. video_grade accepts
bounded brightness, contrast, saturation, gamma, denoise, and sharpen controls;
logo_overlay accepts only a typed IMAGE artifact plus fixed position/opacity;
and audio_loudness accepts typed AUDIO plus bounded loudness/gain controls.
text_overlay remains unavailable until a server-owned font and escaping
contract exists, with Subtitle Burn as the documented fallback. Registry
opening reads a cached server-owned encoder snapshot with not_run/unavailable
truth when no explicit probe has populated it; it never invokes FFmpeg.
Encode, subtitle burn, RIFE/interpolation, AnimeSR, FFmpeg transforms, and video
generation remain partial/unavailable unless their separate bounded runtime
evidence exists.

Image AI Quick, Hub Nodes, and ComfyUI Advanced remain distinct modes. No
second ComfyUI frontend is introduced. The UI adapter in
src/ui/workflow_library.js uses only an explicitly supplied typed
pywebview.api.workflow_library bridge. Until V5-D supplies that bridge, save
and migration actions remain partial with a reason/action; no guessed fetch
route is called.

## Readiness and Module Plan journey

Dashboard is the concise entry point: it shows the current server-owned
readiness state, module attention, C:/D: storage projection, and a direct
`Readiness & Module Plan` route into Settings. Settings is the detailed view.
Its module rows come from the bootstrap product surface and show only the
projected id/provider/component/version/status/reason/next action fields.
Allowlisted resource-fit and C:/D: constraints remain dry-run evidence; unknown
objects, paths, secrets, manifests, and commands are not rendered.

The detailed view is explicitly a server snapshot. Fast refresh may replace
the capability snapshot, but it does not fetch or recompute productization and
never upgrades a partial, unavailable, not_published, or not_run state into an
operational or executing claim. Install, repair, and uninstall are explanatory
states only. Productization and resource planning remain `execution: not_run`
with `dry_run: true` until a separately authorized runtime boundary exists.

## Job recovery and artifact journey

Dashboard also exposes a concise Job Recovery card with server-published active,
attention, interrupted, and recoverable counts. Its focused action navigates to
the existing Jobs route and applies the attention filter; it adds no endpoint or
client-side discovery.

Jobs treats `productization.jobs.records` and `counts` as the canonical recovery
snapshot. Records are bounded, escaped, deterministically deduplicated, and
sorted. Only a matching hot-job record may add published lifecycle timestamps,
progress detail, opaque artifact metadata, or provenance. Durable records are
shown as Durable and do not receive a Resume control unless the server publishes
`resumable: true`; the current production-shaped durable snapshot therefore
remains unavailable with no fabricated preview or media.

Existing legacy cancel/resume requests are followed by the existing fast
snapshot refresh and expose role=status feedback. Artifact cards use only the
opaque artifact ID/URL transport. If no artifact detail is published, Jobs says
`Preview unavailable in this snapshot`; no path, secret, command, manifest, or
unknown object is rendered. This is static/fixture acceptance evidence only and
does not promote recovery, media, GPU, provider, or execution capability.

## V5-D integration boundary

The future integration lane must provide server-owned methods equivalent to:

- list() -> validated library projection, revision, recovery state;
- save(entry, expected_revision) -> detached accepted/conflict result;
- remove(id, expected_revision) -> detached accepted/conflict result;
- plan_migration(entries) and confirm_migration(entries, expected_revision).

The boundary must revalidate every input, redact paths/secrets/commands/media,
and project only allowlisted fields. It must not accept arbitrary client
manifests, start workloads, or expose raw storage paths. This package does not
edit shared API routing.

## Versioning and deferrals

The public product version is V5 (5.0.0). Historical milestone documents
remain unchanged. workflow-library.v1 is independent from product version and
requires an explicit migration plan for future breaking changes.

Global artifact preview accepts only the existing opaque `/api/artifacts/...`
URL and lets native image/video/audio elements use the existing Range transport.
Video and audio use `preload="metadata"`; the browser does not fetch or buffer
the whole server artifact. Mask raster artifacts render as images; non-raster
mask artifacts show a truthful fallback. Metadata and provenance are limited to
allowlisted fields and rendered as escaped text.

Deferred by design: GPU/video/FFmpeg/SAM2/model/provider/server/UI runtime
smokes, filesystem mutation, downloads, dependency installation, benchmarks,
and any automatic GitHub or main-branch operation. Static UI and projection
tests therefore retain `execution: not_run` / `dry_run: true` where applicable.

## UXNW-V5-ACCEPT-UI-001 acceptance evidence

The task-owned `tests/v5_acceptance_ui_fixture.py` serves only deterministic
loopback data; `tests/test_v5_acceptance_ui.py` covers truthful C:/D: payloads,
opaque artifact HEAD/GET/range responses, and the native non-buffering preview
contract. The authorized browser smoke exercised the Dashboard and one real
LiteGraph canvas at 1280x720, 1920x1080, and 2560x1440: storage values and
low-space/unavailable actions remained reachable, horizontal overflow stayed
absent, and the dark perimeter remained border-free. It also exercised node
selection, marquee/multi-move, middle-pan, keyboard movement/deletion/clear,
typed connection auto-connect/picker/rejection/cancel, panel state, Node Guide,
template preview, local save, and export controls. Image, video, audio, mask,
and unknown artifact previews retained opaque URLs; video/audio used metadata
preload and the fixture observed bounded Range requests. This is fixture-only
acceptance evidence; no Hub backend, provider, model, GPU, FFmpeg, or media
execution is claimed operational.
