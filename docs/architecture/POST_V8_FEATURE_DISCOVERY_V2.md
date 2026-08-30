# API Feature Discovery V2

## Purpose

`GET /api/features/v2` gives Local AI Hub clients one explicit, finite catalog
of Post-V8 API surfaces. It prevents a frontend from inferring feature support
from a version string, a route naming convention, a model file, or a visible UI
card.

The catalog describes protocol availability only. It does not probe hardware,
start a worker, load a model, access an external provider, or claim that a
feature is operational.

## Contract

Every feature record has a stable `feature_id`, `api_version`,
`feature_state`, route list, `execution=not_run` and `dry_run=true`.

| Feature state | Meaning |
| --- | --- |
| `READ_ONLY` | The API publishes bounded server-owned evidence only. |
| `PLAN_ONLY` | The API can create a separately confirmed server-owned plan, not execute it. |
| `OWNER_REQUIRED` | Durable metadata exists, but actual execution requires a registered server-owned owner and exact reservation claim. |

The initial catalog contains Capability Graph V2, Component Lifecycle Engine
V2, Model Manager V2, Resource Scheduler V2 and Durable Job Engine V2.

## API and safety

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/features/v2` | Return the detached finite feature catalog. |
| `GET` | `/api/features/v2/{feature_id}` | Return one known feature or a non-reflecting 404. |

The response contains no filesystem paths, credentials, raw settings, worker
commands, artifact bytes, model identity, or provider request. Runtime health
continues to come from the particular capability/preflight endpoint, not this
catalog.

## Tests

`tests/test_post_v8_feature_discovery_v2.py` verifies feature state vocabulary,
path-free detached projection, unsafe/unknown detail rejection, default API
composition and generated route inventory ownership.
