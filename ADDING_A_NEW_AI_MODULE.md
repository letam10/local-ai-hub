# Adding an AI module in V7

1. Add a typed `module.json` manifest with fixed runtime/model IDs and opaque
   artifact inputs.
2. Add the model/runtime entry to the tracked example catalog with provider,
   revision, files, sizes/checksums where published, license/auth state and an
   honest disposition.
3. Add the feature owner under `src/ui/features/<id>/` and a namespace in the
   UI registry. Do not put new feature logic in `pages.js` or `app.js`.
4. Register a typed API route adapter. The browser may send IDs and bounded
   metadata only; source paths, commands, executables and URLs stay server-owned.
5. Add Model Manager/Runtime Manager discovery, plan, verify and synthetic
   install/repair/update/uninstall tests. Add a bounded runtime evidence receipt
   before claiming `OPERATIONAL`.
6. Update `architecture/module_ownership.yaml`, `dependency_rules.yaml`,
   `functional_parity.yaml` and the change-impact map.

Never commit weights/environments/runtime binaries. Never download a model at
startup. If the official source is gated or lacks a pinned digest, use
`AUTH_REQUIRED`, `LICENSE_REQUIRED`, or `MANUAL_IMPORT_ONLY`.
