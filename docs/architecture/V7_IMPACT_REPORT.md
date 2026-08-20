# V7 foundation impact report

## Added

- `src/platform/` path and filesystem boundaries.
- Strict module manifest registry and ten built-in data manifests.
- Model catalog/manager and runtime catalog/manager foundations.
- Offline model-free bootstrap entrypoints.
- Ownership/dependency maps, baseline, migration, impact and developer docs.
- Architecture guards and clean-clone/fake-install fixtures.
- Compatibility markers for `Hub/`, `MCP/`, `Adapters/` and `Services/`.

## Modified

- Existing shared path registry now forwards to platform paths while preserving
  `ROOT`, `CONFIG_ROOT`, `MODEL_ROOT` and other V5/V6 aliases.
- Core product version/examples now agree with the V6 release version source.
- App bootstrap exports the model-free foundation without changing the legacy
  `bootstrap()` contract.
- README documents the V7 foundation and clean-clone path.

## Moved/deleted

None. Historical milestone docs, V5/V6 branches, legacy directories, machine
data and user files remain in place.

## New contracts

`module-manifest.v1`, `model-catalog.v1`, `runtime-catalog.v1`,
`core-bootstrap.v1`, `model-manager.v1`, `runtime-manager.v1` and
`module-composition.v1` are additive. Existing `job.v2`, `node-run.v2`, opaque
artifact, project, workflow, settings and backup contracts are unchanged.

## Risks and future extraction

`src/services/api/core.py`, `src/ui/app.js`, `src/ui/pages.js`, Job Manager and
Artifact Store remain intentional monolith hotspots. Future route/UI extraction,
real installation workflows, receipts, download resumption and runtime smoke
packages require separate reviewed packages. No real model download or AI
inference is part of this foundation.
