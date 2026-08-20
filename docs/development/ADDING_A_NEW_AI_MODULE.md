# Adding a new AI module

1. Create `src/modules/<id>/` and a strict `module.json` manifest.
2. Define runtime and model requirements using opaque IDs and relative leaves.
3. Implement an adapter that accepts typed opaque artifact IDs only.
4. Implement a bounded worker with canonical containment and reparse checks.
5. Register the manifest through the allowlisted data registry; do not add
   arbitrary dynamic imports.
6. Connect the existing Job Manager and Artifact Store output contract.
7. Add focused unit/contract tests, including missing runtime/model and unsafe
   path/reparse cases.
8. Add a UI feature under `src/ui/features/<id>/` only when needed; the UI sees
   IDs, states, reasons and next actions, never workstation paths.
9. Add/update tracked example configuration and architecture ownership metadata.
10. Run syntax, focused tests, architecture guards, diff, secret and large-file
    checks. Only a bounded functional smoke can change `PARTIAL` to
    `OPERATIONAL`.
