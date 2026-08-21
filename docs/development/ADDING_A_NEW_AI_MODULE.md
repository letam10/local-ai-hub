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

## V7 Phase 2 installation boundary

The module manifest remains declarative. Runtime/model installation is owned by
the server-side Component Installer and uses fixed catalog IDs, not commands,
URLs, Python callables or filesystem paths from the manifest or browser. A
module may expose an `install` plan and a dependency graph, but it must not
download at startup or claim `OPERATIONAL` from file presence alone. The
composition is:

`manifest → Runtime Manager → Model Manager → capability evidence → Module Manager`

Use the existing adapter and Job Manager execution path after installation;
do not add a parallel inference backend. Missing optional assets must remain
`NOT_INSTALLED`/`PARTIAL`/`UNAVAILABLE` with a concrete reason and next action.
