# V7 logical source tree

```text
APP                 src/app/ (desktop shell and lifecycle)
UI                  src/ui/ (legacy composition; new features under features/)
API                 src/services/api/ (loopback transport compatibility)
SERVICES            src/services/ (jobs, artifacts, domains, managers)
MODULES             src/modules/ (allowlisted manifests, adapters, workers)
PLATFORM            src/platform/ (paths, containment and machine boundaries)
SHARED              src/shared/ (schemas, validation, compatibility facades)
CONFIG              Config/*.example.json + ignored local Config/*.json
WORKFLOWS           workflows/ and workflow_packages/
SCRIPTS             scripts/ (bootstrap, validation, launch/repair tooling)
TESTS               tests/ + tests/architecture/
DOCS                docs/architecture, docs/development, docs/operations
DISTRIBUTION        distribution/ (installer and release metadata)
MACHINE-LOCAL DATA  Models, Environments, runtime, Output, Cache, Temp, Logs,
                    Reports, Projects and Backups
```

The source tree remains compatible with the V6 layout. V7 adds platform and
manager foundations; it does not bulk-move legacy files.
