# Kiến trúc và bản đồ thư mục

Local AI Hub là bộ điều phối chạy cục bộ trên Windows. Mã nguồn chính nằm dưới
`src/`; runtime nặng, model, cache, output và dữ liệu cá nhân nằm ngoài Git.
Các thư mục tương thích cũ `Hub/`, `MCP/` và `Adapters/` chỉ là shim, không phải
source canonical.

## Ownership theo tầng

- `src/app/`: desktop shell, WebView, bootstrap và vòng đời cửa sổ.
- `src/ui/`: navigation, workspace, Node Studio LiteGraph, queue/job panel và
  design-system CSS. UI chỉ gọi API loopback, không chạy shell command.
- `src/services/api/`: route loopback, upload/artifact, validate, submit graph,
  dashboard và public response contract.
- `src/services/node_studio/`: registry node typed, schema validator, DAG
  engine và trạng thái run/provenance trong bộ nhớ.
- `src/services/job_manager/`: queue, progress, cancel/resume và GPU slot;
  chỉ quản lý tiến trình do Hub tạo.
- `src/modules/`: adapter cho ComfyUI, FFmpeg, SAM2, AnimeSR, Whisper và các
  backend local đã cài; không vendor repository upstream và không tự tải model.
- `src/shared/`: kiểu dữ liệu và tiện ích dùng chung.
- `scripts/`: launcher, audit và kiểm tra bounded; `tests/`: unit/smoke tests.
- `workflows/`: graph JSON mẫu được theo dõi trong Git, không chứa model,
  output, đường dẫn máy hay secret.
- `Config/`: chỉ cấu hình mẫu `*.example.json`; cấu hình máy local bị ignore.

Các thư mục `runtime/`, `Models/`, `Environments/`, `Cache/`, `Output/`,
`Temp/`, `Logs/` và `Reports/` chứa cài đặt hoặc dữ liệu runtime local, không
được commit. Không tự ý di chuyển hay xoá các cài đặt AI hiện có.

## Luồng Node Image Studio

```text
WebView UI → /api/node-studio/registry + /availability
          → validate graph → job.v2 queue → node-run.v2 DAG engine
          → adapter (ComfyUI / FFmpeg) → artifact ID + provenance → UI preview
```

Luồng ảnh chuẩn là `Prompt/Image artifact → Generate → Image Edit (tuỳ chọn) →
Upscale → Preview → Save/Export`. Socket được kiểm tra theo kiểu; validator
từ chối input bắt buộc thiếu, nối sai kiểu, cạnh trùng và cycle. Registry dùng
`node-studio.v2`; run snapshot công bố `contract_version=node-run.v2`, trạng
thái từng node, `next_action` và provenance an toàn; public job dùng
`contract_version=job.v2` và không trả raw input hoặc workstation path.

`operational` chỉ dành cho adapter đã có bounded smoke. `partial` hoặc
`unavailable` luôn có `reason` và `action`, được hiển thị ở palette, Inspector,
queue và lỗi job; không được giả nhận backend là hoạt động.

## Video Creative Workflow

Milestone 2 dùng cùng DAG/job contract cho luồng `Video artifact + prompt tuỳ
chọn → Video Transform → Video Upscale → Frame Interpolation → Encode →
Preview → Save → Export`. `video_transform` chỉ nhận operation trong allowlist
FFmpeg; `video_upscale` có fallback `ffmpeg_scale` và metadata
`ai_upscaler=false`. Node `video_generate` được hiển thị để giữ contract prompt-
to-video nhưng trả `unavailable` có hướng dẫn vì chưa có adapter generation
local đã smoke. Chi tiết template và ma trận backend nằm ở
[Video Creative Workflow Milestone 2](VIDEO_CREATIVE_WORKFLOW_MILESTONE_2.md).

## API và kiểm thử

Các route Node Studio chính:

- `GET /api/node-studio/registry?scope=image`
- `GET /api/node-studio/availability?scope=image`
- `GET /api/node-studio/presets`
- `POST /api/node-studio/validate` và `POST /api/node-studio/run`
- `GET /api/node-studio/runs/{job_id}`

Kiểm tra mục tiêu trong lúc phát triển:

```powershell
& .\Environments\hub\Scripts\python.exe -m unittest -q `
  tests/test_v4_node_studio.py tests/test_unified_ui.py
```

Ở cuối milestone mới chạy một lượt full bounded suite, HTTP/UI smoke và image
workflow smoke nếu backend local đã sẵn sàng. Không benchmark, không chạy
  inference lặp, không tải model và không thay đổi CUDA/NVIDIA driver.

## Milestone 3 — Unified Creative UX

Milestone 3 giữ nguyên các ownership trên và bổ sung một lớp UX/productivity chung:

- `src/ui/pages.js` là composition layer cho workspace header, `workspace-state`,
  workflow rail, Jobs history và artifact preview/save/export. Page không gọi worker
  trực tiếp.
- `src/ui/app.js` là state/event layer: bootstrap snapshot, navigation drawer, route
  data, API error/loading banner, preview modal và các thao tác cancel/retry an toàn.
- `src/ui/node_studio.js` là editor layer: template discovery, Recent, rename/duplicate,
  local autosave/recovery, import validation, export JSON, unsaved warning và typed
  canvas. `localStorage` là nơi duy nhất dành cho workflow cá nhân.
- `src/services/api/core.py` công bố `reason` và `action` cùng `tool_status`; route
  không nâng `partial` thành `operational` nếu thiếu bounded smoke.
- `src/services/node_studio/engine.py` công bố provenance theo artifact/node trong
  `node-run.v2`; `src/services/api/jobs.py` publicize metadata và loại bỏ raw path.

### Workflow lifecycle

```text
Preset / Recent local
        → validate graph (node-studio.v2)
        → edit + autosave local + unsaved fingerprint
        → Run Graph (job.v2)
        → queue/progress/cancel/retry
        → node-run.v2 provenance + artifact preview/save/export
```

Recent index dùng key `local-ai-hub-workflows-v1:index:{scope}` và tối đa 12 mục.
Graph key có scope/ID opaque; không ghi workstation path, secret, model weight hay
output media vào Git. `Lưu local` cập nhật fingerprint xác nhận; autosave chỉ phục hồi
bản nháp và không thay đổi trạng thái backend.

### Creative limitations

Image Generate/Edit, AnimeSR và các tool direct vẫn có thể là `partial`; video
generation giữ `unavailable` khi chưa có adapter smoke. Trong thời gian resource
override, không chạy FFmpeg/NVENC, video generation/transform/upscale/interpolation/
encode, AnimeSR/RIFE hoặc ComfyUI video. Functional video evidence được ghi
`deferred due GPU/resource contention` và không được dùng để tuyên bố operational.

Chi tiết UX, contract và lệnh kiểm tra nằm ở
[MILESTONE_3_UNIFIED_CREATIVE_UX.md](MILESTONE_3_UNIFIED_CREATIVE_UX.md).

## Milestone 4A — Creative Project ownership

Milestone 4A là lớp metadata/local workspace, không phải pipeline AI mới. Nó
không sở hữu file media, không scan filesystem tùy ý và không tự chạy workflow:

```text
Artifact Store (file + opaque artifact ID)
        ↑ public, path-safe record
Project Manager (project / asset metadata / recipe / compare)
        ↑ validated JSON contract
API loopback → UI state/event layer → Quick hoặc Hub Nodes editable state
```

### Phân chia module

| Module | Sở hữu | Không sở hữu |
| --- | --- | --- |
| `src/services/artifact_store.py` | Registry và public opaque record của artifact | Project title, recipe, collection hoặc raw M4A metadata |
| `src/services/project_manager/schemas.py` | ID opaque, giới hạn, validation, safe text/JSON và contract version | Đọc file artifact hoặc thực thi backend |
| `src/services/project_manager/manager.py` | CRUD state, safe recovery, lineage metadata, Project/Recipe Pack/Compare serialization | Copy/move/delete media, model, output, secret hoặc environment |
| `src/services/api/api_server.py` | Mapping HTTP status, JSON bounded và public M4A routes | Logic UI hoặc raw path disclosure |
| `src/ui/api.js` | Thin loopback client | Shell/direct filesystem access |
| `src/ui/pages.js` | Accessible states, contact sheet, Gallery/Compare/Recipe controls | Mutable backend state |
| `src/ui/app.js` | Route data, events, browser download JSON và handoff editable UI | Job submission tự động từ recipe/template |
| `src/ui/node_studio.js` | Clone/fill editable graph from Recipe; load tracked preset | Auto-run graph hoặc nâng availability |

State thực tế là `Config/creative_workspace.json` và bị Git ignore. Chỉ
`Config/creative_workspace.example.json` được track làm mẫu schema rỗng. State
ghi qua temporary sibling `*.tmp` rồi replace nguyên tử. Nếu top-level state
không đọc được hoặc không phải JSON object, manager trả `recovery_required` và
chặn mutation để không ghi đè dữ liệu. Nếu một số record con hỏng, manager giữ
record hợp lệ và trả `partial_recovery` cùng next action.

### Contract dữ liệu công khai

| Contract | Mục đích | Reference được phép |
| --- | --- | --- |
| `creative-workspace.v1` | Local maps cho project/recipe/metadata/collection/board | opaque project, recipe, collection, compare, artifact ID |
| `creative-project.v1` | Project public/metadata | opaque artifact ID, recipe ID, preset ID |
| `creative-asset.v1` | Artifact Store record được decorate tag/favorite/lineage/provenance | opaque artifact parent/child và recipe ID |
| `creative-recipe.v1` | Prompt template, variables, blocks, seed/model/settings/version | preset ID; JSON safe only |
| `creative-recipe-pack.v1` | Chia sẻ recipe đã validate | recipe record |
| `creative-project-export.v1` | Export project/recipe refs/metadata/compare | opaque/relative metadata only |
| `creative-compare.v1` | Tối đa 8 artifact cùng project và selection/diff | opaque artifact ID |

`safe_json` từ chối key `path`, `*_path`, `secret`, `token`, credential/password
và string giống Windows drive/UNC path. Public API không gửi raw filesystem
path, input, resume data hoặc callable runtime state. URL artifact, nếu có, là
endpoint Hub đã publicize, không phải machine path.

### API loopback M4A

| Route | Chức năng |
| --- | --- |
| `GET /api/creative/overview` | Snapshot Project, Asset, Recipe, Collection, Gallery và recovery |
| `GET/POST /api/projects`, `GET/PUT /api/projects/{id}` | List/create/read/update project |
| `POST /api/projects/{id}/archive`, `/restore` | Archive/restore metadata, không xóa artifact |
| `POST /api/projects/{id}/assets` | Thêm reference tới artifact Hub hiện có |
| `GET/POST /api/projects/{id}/compare` | Read/update Compare Board |
| `GET /api/projects/{id}/export`, `POST /api/projects/import` | Project Manifest với conflict policy |
| `GET /api/assets`, `PUT /api/assets/{opaque-id}` | Filter/search + tag/favorite/lineage/recipe metadata |
| `GET/POST /api/collections`, `PUT /api/collections/{id}` | Collection chứa opaque asset reference |
| `GET/POST /api/recipes`, `PUT /api/recipes/{id}` | Recipe lifecycle, version tăng khi update |
| `POST /api/recipes/{id}/apply` | Render variable, trả Quick/Node editable application; không tạo job |
| `GET /api/recipes/export-pack`, `POST /api/recipes/import-pack` | Recipe Pack safe import/export |
| `GET /api/workflow-gallery` | Tracked template + capability preflight/reason/action |

Mutation JSON bị giới hạn 2 MB bởi handler chung. Invalid ID/schema trả `400`,
unknown public record trả `404`; route không nhận raw path hoặc command.

### Extension point và limitation

- Adapter/job mới phải đăng ký output qua Artifact Store trước khi user có thể
  gắn asset vào Project. Project Manager không nhận `Path` hoặc upload bytes.
- Extension chỉ có thể công bố metadata/provenance JSON nhỏ nếu pass `safe_json`;
  không đưa API key, environment state, model weight, callable hoặc raw path vào
  manifest.
- Workflow Gallery chỉ đọc JSON track trong `workflows/`; `partial` hoặc
  `unavailable` từ node registry luôn thắng card state và xuất reason/action.
- Recipe application chỉ clone/fill Quick form hoặc graph đang chỉnh. Nó không
  bypass confirmation, không enqueue job và không làm backend partial thành
  operational.
- Compare Board là presentation/selection metadata, không decode/encode,
  transform, upscale hoặc generate media.
- Trong resource override, evidence video/GPU vẫn `deferred due GPU/resource
  contention`; không chạy FFmpeg/NVENC, AnimeSR/RIFE, ComfyUI video hoặc can
  thiệp process video của task khác.

Xem [MILESTONE_4A_CREATIVE_PROJECTS.md](MILESTONE_4A_CREATIVE_PROJECTS.md) để
biết flow UX, import/export/recovery và ma trận kiểm thử bounded.

## M4A Reliability & Large Media ownership

Product release version nằm duy nhất ở `src/shared/version.py` (`4.0.0`). Nó
được projection vào `/health`, HTTP server header và tracked component example;
không đổi schema/contract version riêng của job, graph hoặc creative records.

| Module | Sở hữu hardening | Không sở hữu |
| --- | --- | --- |
| `src/app/desktop_lifecycle.py` | Decision gate close, bounded cancel wait, cleanup authorization | Kill force worker hoặc lifecycle API external |
| `src/app/tray.py` | Windows Restore/Exit notification area sau khi đăng ký thành công | Hide không có restore surface, dependency tray mới |
| `src/app/main.py` | Own-vs-external API boundary, pywebview bridge, admission recheck, late process registration | Job runner/model runtime implementation |
| `src/services/api/jobs.py` | Coalesced durable public jobs, interrupted recovery, hot/cold bounded history | Callable retry runner, raw input/path/resume payload |
| `src/services/job_manager/manager.py` | Context cancel and bounded terminal wait | Force shutdown khi timeout |
| `src/services/artifact_store.py` | Stream upload staging, SHA-256, atomic register, owned token cleanup | Client filesystem path hoặc arbitrary destination |
| `src/services/api/api_server.py` | Artifact range/HEAD stream, Content-Length upload gate, graceful flush | Static UI stream change hoặc GPU/media execution |
| `src/services/node_studio/engine.py` | 256-entry LRU reference cache | Artifact file deletion on eviction |
| `src/services/node_studio/state.py` | All active + 100 terminal path-safe graph snapshots | Persistent graph/media cache |

### Reliability contracts

`active = queued | starting | running | cancelling`. Desktop native closing is
vetoed when `active > 0`; only a zero-active normal close or a completed
cancel-and-wait marks cleanup authorized. For an owned API, `POST
/api/lifecycle/prepare-close` acquires the submission gate, rejects new work
and rechecks durable active jobs atomically before cleanup. If the recheck sees
work, admission reopens and the three-choice decision remains visible.

The API's own startup reconciliation maps stale active durable records to
terminal `interrupted` with a recreation action. Only current-session
`failed`, `cancelled` and `unavailable` jobs may project `resumable=true`.
An externally managed API is never terminated, idled or globally cancelled by
this desktop; its active jobs still veto close so the user can return or keep
the visible Hub in the tray.

Artifact `GET` supports one RFC-style range and streams at 1 MiB; multi-range
or invalid requests return `416` with `Content-Range: bytes */<size>`. Artifact
IDs remain opaque and all records resolve within owned roots. `POST /api/uploads`
requires content length, validates configured 8 GiB default/safety margin,
streams at 4 MiB, hashes and atomic-renames inside `Temp/uploads/`; failed token
parts are removed without touching other data.

`Config/jobs.json` and `Archive/Jobs/` are ignored runtime state. The hot file
keeps every active record and 500 newest terminal record; archival JSONL rotates
at 16 MiB and retains at most 30 files. Progress writes are coalesced around
500 ms; terminal/create transitions and graceful API shutdown flush immediately.

ComfyUI Advanced is a partial bridge pending the manual Windows WebView
acceptance documented in
[MILESTONE_4A_RELIABILITY_HARDENING.md](MILESTONE_4A_RELIABILITY_HARDENING.md).
The current resource override defers that evidence; no iframe/runtime presence
is treated as an operational claim.

`tests/windows_lifecycle_smoke.py --run` is an opt-in real Windows acceptance
fixture. It uses a `pythonw` child, a test-owned loopback server and CPU-only
dummy processes to prove cold start, a second external-API desktop instance,
zero-active cleanup, background/restore, cooperative cancellation and native
tray re-registration. It never starts the production API, probes GPU hardware
or executes media/model work.
