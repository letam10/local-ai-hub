# V7 Node Canvas Input Accessibility

This package is a static input-safety contract for the pinned Hub Node Studio
canvas. It keeps the existing desktop mouse path as the default and opts an
individual `LGraphCanvas` into Pointer Events only when the canvas window
advertises `PointerEvent` support. The global LiteGraph pointer setting is not
changed, so another canvas cannot inherit the editor's input mode accidentally.

## Contract

- `studio.js` requests a capability-checked per-canvas method. A WebView
  without `PointerEvent` uses the existing mouse listeners.
- `litegraph.js` maps each semantic event to exactly one DOM event. Primary
  mouse, pen, and touch contacts are normalized to the existing `which` and
  button fields; non-primary or cross-pointer contacts are ignored.
- A primary pointer is captured with `setPointerCapture` after an accepted
  canvas-down and released with `releasePointerCapture` on pointer-up,
  `pointercancel`, or `lostpointercapture`. Move/up/cancel listeners
  use the same capture flag and callback identity, preventing duplicate
  listeners when a drag leaves the canvas; there is no duplicate listener
  registration for one semantic event.
- Cancellation clears node drag, resize, marquee, canvas-pan, widget, and
  connection-preview state. The Studio adapter restores the bounded
  pre-change graph snapshot when one exists. Cancellation does not call
  `afterChange`, write history, autosave, or start a job.
- Studio teardown calls `setCanvas(null)` before `setGraph(null)`. The
  `unbindEvents` path is idempotent, removes any document-level drag listeners, calls
  `releasePointerCapture` before clearing the active pointer, and clears all
  transient interaction state without persistence callbacks. A graph-null
  pointer-up follows the same cleanup path before returning.
- The connection-picker wrapper rebinds through the canvas's selected method,
  so pointer-mode socket drops are handled once and cancellation closes the
  picker without publishing a second edit.
- Malformed or unsupported event objects are ignored or reduced to a bounded
  no-op event. No event object, path, URL, command, exception, or secret is
  stringified into a public result.

## Scope and verification boundary

Only `src/ui/features/node_studio/studio.js` and the pinned
`src/ui/vendor/litegraph.js` are changed. The focused Python test is a pure
source-contract regression; `node --check` is syntax validation only. Existing
selection, keyboard, minimap, typed picker, undo/redo, draft/autosave, and
Output Preview projections remain covered by their adjacent static suites.

This package does not claim browser/device support or runtime authoring
readiness. There is no runtime/browser execution in these gates. No browser,
server, provider, model, network, or media operation is run here; any
execution projection remains `execution=not_run` until a separate bounded
runtime authorization and device check exists.
