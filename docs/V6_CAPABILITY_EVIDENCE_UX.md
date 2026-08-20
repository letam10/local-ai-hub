# V6 Capability Evidence UX

The Dashboard, Settings and Media/Node Studio share one server-snapshot view
for the fixed media operations `video_grade`, `logo_overlay` and `encode`.
The UI shows outcome, execution, cleanup, source-overwrite tri-state, reason and
next action before a user enters Media. Settings provides the detailed view;
Media keeps generic actions visibly partial and disables the explanatory form
submit control when the snapshot does not authorize that generic action.

Node Studio reads the existing registry `operation_scope` projection. Only the
three named operation nodes can receive an operational label, and only when the
projection contains completed exact evidence. Unrelated nodes remain partial or
unavailable from their own registry status. The existing single LiteGraph canvas,
opaque artifact URLs, native media metadata preload and bounded Range transport
are unchanged.

The projection is fail-closed: missing or malformed nested evidence becomes
`unavailable` / `not_run`, unknown values are not stringified, and presentation
contains no local paths, secrets, commands or callable data. Fast refresh still
uses the existing health/jobs/capabilities calls; it does not fetch productization
again or infer execution from a partial refresh.

This is a presentation of server-owned static/runtime evidence, not a new runtime
capability. Rendering the evidence starts no worker, FFmpeg process, provider,
GPU task or media operation. A completed status means only that the server
published its bounded evidence record for the exact approved operations; generic
Media execution and all unrelated operations remain outside this UI contract.
