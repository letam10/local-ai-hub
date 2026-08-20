# V6 capability evidence and ComfyUI artifact contract

This source contract keeps two boundaries explicit:

- A completed Hub job records a local-only receipt with the tool, component,
  current path-free runtime fingerprint, bounded timestamp, and completed
  execution state. The API projects it as operational only when the receipt
  is fresh, the tool maps to the same component, and the runtime fingerprint
  still matches. Missing, stale, mismatched, legacy, or unbound receipts stay
  `partial`/`not_run`.
- ComfyUI image workers return `outputs` because they collect files from prompt
  history. The job publisher accepts that field alongside the older `output`
  and `files` aliases, then sends every candidate through the existing
  Hub-owned artifact store and job provenance boundary. The browser and
  jobs.json receive only opaque artifact metadata; worker paths stay inside
  the artifact store's private managed index and are not exposed.

This package does not execute a runtime, inspect machine-local Config, start
ComfyUI, download a model, or claim a functional smoke. A bounded generation
smoke remains required before FLUX or Qwen Image can become operational.
