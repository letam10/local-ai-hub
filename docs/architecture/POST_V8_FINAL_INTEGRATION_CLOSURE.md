# Post-V8 final cross-milestone integration closure

This document records the source-led audit and correction plan for PR #113.
It is intentionally about contracts and projections.  It does not authorize a
provider, plugin, model, GPU, external application, update, restore, or remote
worker to execute.

## Source audit matrix

| Subsystem | Producer | Consumer | Schema / IDs | State and resource semantics | Persistence / execution authority | Audited mismatch | Correction |
|---|---|---|---|---|---|---|---|
| M1 Capability Graph V2 | `capability_graph` | Workflow Runtime, adapters | `capability_id`, evidence fingerprint | Operational evidence is separate from a plan | graph service / none | None found | Preserve bounded evidence checks |
| M1 Lifecycle + Model Manager | lifecycle/model services | M2/M3 preflight | capability/model IDs | Plans are not execution | existing V8 owners / owner-gated | None found | Retain `not_run` wording |
| M1 Resource Scheduler | `resource_scheduler` | Workflow Runtime, Durable Job Engine, adapters | `resource-scheduler.v3`, job/reservation/lease IDs | Logical requirement resolves to public `resource_profile` | scheduler / no worker | Runtime read a private `profile` shape | Publish bounded classification and consume it |
| M1 Durable Job Engine | durable engine/store | Workflow Runtime, Projects | durable `jobv2_*` ID | admission/retry is not worker execution | SQLite / registered owner only | None found | Regression keeps CAS/restart/lease invariants |
| M1 Feature Discovery | router registry | UI/diagnostics | route ID + method + path | feature state names a contract dimension | Router / no execution | Product search wording was stale | Describe bounded server metadata accurately |
| M2 Workflow Runtime | typed Node Studio graph | durable admission | workflow/project/artifact IDs | runner requirements are plan-only | existing graph owner / owner required | slots mismatched actual FFmpeg/GPU runners | One runner contract maps capability + logical requirement + owner class |
| M2 Project Workspace | workflow library + durable lookup | project manager | opaque workflow/job IDs | metadata-only attachment | V5/V7 stores / no execution | None in façade; source checks existence | Regression retains authoritative lookup |
| M2 Artifact/Media | artifact/project stores | UI, workflow preflight | opaque artifact IDs | bounded metadata / plan-only media | existing stores / owner required | None found | No byte/path projection |
| M3 Provider Adapter Registry | static adapter specs | adapter preflight | adapter/capability/requirement IDs | availability is not provider execution | registry / `UNBOUND` | adapters declared orphan concrete profile IDs | adapters declare only canonical logical requirements |
| M4 Product Experience | product experience service | app.js → `renderPage` | category → route | navigation/search only | API / no execution | live categories returned non-routable routes; exact-8 truncation lied | one category route map, correct truncation |
| M5 Platform Hardening | hardening boundary + native launcher | Diagnostics + API child | area IDs + payload/build identity | contract readiness differs from runtime state; API must import selected payload | existing owners / not run | `completed` could be read as executed; embedded runtime could import an old payload before the new one | explicit state dimensions; fixed bootstrap inserts the selected payload root before API module resolution |
| M6 Extensibility | static SDK/remote-worker contract | Diagnostics | area/protocol IDs | declarative contract differs from plugin/worker runtime | no owner / `NOT_CONFIGURED` | UI could appear green for a contract | explicit dimensions and neutral wording |

## Canonical resource taxonomy

| Logical requirement | Scheduler resolution | Evidence rule | Execution implication |
|---|---|---|---|
| `cpu.light` | `cpu_light` | scheduler declaration | plan only |
| `media.ffmpeg` | `ffmpeg_probe` | scheduler declaration | plan only |
| `gpu.vision` | `vision_gpu_2gb` | scheduler declaration | plan only |
| `gpu.speech` | `whisper_gpu_2gb` | scheduler declaration | plan only |
| `gpu.video` | `video_gpu_4gb` | scheduler declaration | plan only |
| `gpu.voice` | unavailable until published | no fabricated profile | unavailable/not run |
| `gpu.image_generation` | unavailable until model VRAM evidence + profile | model-catalog evidence required | unavailable/not run |

The scheduler is the sole resolver from logical requirement to concrete
profile.  A provider or runner cannot select its own hardware profile.

## State dimensions

| Field | Meaning | It does not mean |
|---|---|---|
| `contract_state=READY` | Source/API contract is present and tested | a provider, updater, plugin, or worker ran |
| `availability_state` | Declared dependency/profile availability | permission to dispatch |
| `runtime_state` | Runtime implementation/owner state | a successful operation |
| `execution_state` / `execution=not_run` | Whether this projection performed work | a generic health verdict |
| `connection_state=NOT_CONFIGURED` | No remote endpoint/credential/owner is configured | an error that may be bypassed |
| `feature_state` | Feature Discovery contract class | runtime operational status |

## Change plan and acceptance evidence

| Priority | Change | Required evidence |
|---|---|---|
| P0 | Publish scheduler job resource classification and use it for Workflow heavy count | PREPARING/RUNNING/PAUSED/CANCELLING count; release/expiry returns zero |
| P0 | Replace adapter profile strings with logical requirements and resolver | all 13 adapters recognized; image generation has no false 2 GiB fit |
| P1 | Align runner capability/resource declarations and make multi-profile workflow unavailable | no slot inference; explicit coordinator blocker |
| P1 | Make GPU exclusivity candidate-GPU-specific | GPU0 exclusive does not block compatible GPU1; global heavy limit remains separate |
| P0 | Use canonical search category routes and correct truncation | every category route is in frontend routes; 7/8/9 result semantics |
| P1 | Keep onboarding session-only wording truthful | `persistent_state=none`, no localStorage claim |
| P1 | Prove project attachment uses authoritative workflow/job lookups | phantom references rejected; valid references remain opaque/idempotent |
| P1 | Normalize M5/M6 contract versus runtime projections | diagnostics says contract ready, not operational/executed |
| Final | Run source, API/UI, candidate and CI gates on exact final head | test logs, exact GitHub run, isolated candidate evidence |

## Exact payload startup invariant

An embedded runtime can have an older application directory before
`PYTHONPATH`. Therefore a child API is never launched with only
`-m src.services.api.api_server`. The desktop path and staged-updater preflight
both use the same fixed bootstrap: insert the selected, verified payload root
at `sys.path[0]`, then run the allowlisted API module. The child remains bound
to the normal build/payload identity handshake; this is not a generic command
or an execution owner for model/provider work.

## Deliberate limits

Provider execution remains unbound, plugin dynamic execution remains
not implemented, remote workers remain not configured, and production
update/restore and GPU/model acceptance are outside this source-only closure.
