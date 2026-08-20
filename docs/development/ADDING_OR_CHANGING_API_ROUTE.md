# Adding or changing a V7 API route

This guide keeps the Phase 3 API boundary reviewable.

1. Identify the domain and the existing application service that owns the
   behavior. Do not put filesystem, subprocess, model-install, Job Manager,
   backup ZIP or project-database logic in a route adapter.
2. Update `architecture/api_routes.yaml` by running
   `python -B scripts/generate_api_route_inventory.py`. A route must have one
   method/path owner, a domain, service, body limit and status-code contract.
3. Implement or extend the cohesive adapter in
   `src/services/api/routes/<domain>.py`; use `ApiRequest`, `ApiContext` and
   `ApiResponse`. Browser input is limited to validated opaque IDs and typed
   fields—never raw paths, commands, URLs or executables.
4. Register the adapter explicitly in `src/services/api/router_registry.py`.
   Duplicate method/path registration is a hard error.
5. Add a contract test for status, response keys, schema or contract version,
   stale revision behavior and sanitized errors. Normalize timestamps, random
   IDs and machine-local paths in differential fixtures.
6. Run the targeted domain tests, `tests/architecture/test_v7_api_routes.py`,
   Python compilation, `scripts/ci_validate.py` and `git diff --check`.

Static delivery, artifact byte ranges and uploads remain transport-specialized
until their streaming regression suite is present. Do not bump a public
contract version merely because a handler moved.
