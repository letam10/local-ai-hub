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
- `src/services/image_mask_studio/`: session Image & Mask khai báo, bounded,
  recovery-safe; chỉ tham chiếu Artifact Store bằng opaque ID.
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

Post-V8 M2 bắt đầu bằng lớp `workflow-runtime.v2` preflight và các projection
`project-workspace.v2`, `artifact-library.v2`, `media-pipeline.v2`. Các lớp này
không thay thế owner persistence V7/V8, không reserve GPU/job, không chạy media
và chỉ dùng opaque artifact ID. Chi tiết hợp đồng xem
[Post-V8 Workflow & Data M2](architecture/POST_V8_WORKFLOW_DATA_M2.md).

Post-V8 M3 thêm `provider-adapters.v2` và `external-integrations.v2`: catalog
finite cho các first-party adapter với typed preflight, dependency và declared
resource profile; cùng projection AIRI/ứng dụng ngoài không có path, endpoint
hay credential. M3 không import adapter legacy, không khởi chạy worker/provider
hoặc external app, không embed WebView ngoài và không biến dependency đủ thành
quyền thực thi. Chi tiết xem
[Post-V8 Provider Adapters & External Integrations M3](architecture/POST_V8_PROVIDER_ADAPTERS_M3.md).

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
- `src/ui/features/node_studio/studio.js` là editor layer: template discovery, Recent, rename/duplicate,
  với `src/ui/node_studio.js` chỉ là compatibility facade cho các extension cũ.
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
| `src/ui/features/node_studio/studio.js` | Clone/fill editable graph from Recipe; load tracked preset | Auto-run graph hoặc nâng availability |

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

## Milestone 6A — Image & Mask Studio ownership

Image & Mask Studio là một editor **khai báo** nằm trên Artifact Store và
Creative Project. Nó không phải image renderer, adapter inference hay job
runner. Bất kỳ pixel mới nào phải được một công cụ đã được ủy quyền tạo và đăng
ký trước qua Artifact Store; Studio chỉ nhận public record/opaque ID sau đó.

```text
Artifact Store (file + immutable opaque artifact ID)
        ↑ public image record / safe URL
ImageMaskStudioManager (session + layer/mask vector + history/snapshot)
        ↑ validated loopback JSON, optimistic revision
API / UI Image AI → Project Manager attachment + provenance metadata
```

### Phân chia module và dữ liệu local

| Module/tệp | Sở hữu | Không sở hữu |
| --- | --- | --- |
| `src/services/image_mask_studio/schemas.py` | Opaque Studio/layer/snapshot/preset ID, allowlist layer/mask/adjustment, giới hạn hình học và contract | Raw path, data URL, pixel buffer, executable hoặc backend command |
| `src/services/image_mask_studio/manager.py` | Session declarative, autosave, history, snapshot, preset, safe recovery và pending project-link intent | File artifact, copy/xóa media, rasterize mask hoặc inference |
| `src/services/image_mask_studio/config.py` | Policy cục bộ nhỏ và các giới hạn bounded; cờ SAM2 chỉ hạ/nâng giữa `unavailable` và `partial` | Path model, URL backend, lệnh, tên model, secret hoặc cờ `operational` giả |
| `src/services/artifact_store.py` | Artifact image canonical và public projection | State/session/layer/brush của Studio |
| `src/services/project_manager/manager.py` | Gắn reference Studio idempotent về media/reference, provenance revision theo Project và shared-artifact lineage bounded | Pixel/canvas/state đầy đủ của Studio |
| `src/services/api/api_server.py` | HTTP mapping, 400/404/409 path-safe response, liên kết hai bước Studio → Project | UI state hoặc filesystem/browser access trực tiếp |
| `src/ui/image_mask_studio.js` | Pointer canvas → brush vector tọa độ chuẩn hóa, Escape hủy nét nháp | Rasterize/upload pixel hoặc gọi shell |
| `src/ui/pages.js`, `src/ui/app.js`, `src/ui/api.js` | UI, accessibility state, form/event và loopback client | Tin tưởng raw path/manifest tự do hoặc tự submit job/model |

Ba tệp local không bị lẫn ownership:

| Tệp | Nội dung | Git |
| --- | --- | --- |
| `Config/image_mask_studio.example.json` | Template policy `image-mask-studio-config.v1`, `schema_version: 1` | Track |
| `Config/image_mask_studio.json` | Policy máy cục bộ tùy chọn: limits và `sam2_assist.configured` | Ignore |
| `Config/image_mask_studio_state.json` | State runtime `image-mask-studio-state.v1`: session, preset, recent index, history và snapshot | Ignore |

State được ghi qua sibling `*.tmp` rồi replace nguyên tử. File state hỏng hoặc
sai top-level contract trả `recovery_required` và chặn mutation, không tự ghi
đè. Record session/preset/history/snapshot vượt contract hoặc giới hạn đưa Studio
vào `recovered_partial` **chỉ đọc** để không làm mất phần state chưa thể phục hồi;
response nêu action recovery. Policy sai chỉ fallback về default bảo thủ, không
được dùng để biến backend chưa smoke thành operational.

### Contract công khai và lineage

| Contract | Mục đích | Reference được phép |
| --- | --- | --- |
| `image-mask-studio.v1` | Session public, revision, layer stack, history/snapshot summary và provenance | `studio_`, `layer_`, `snapshot_`, `project_`, `artifact_` opaque ID |
| `image-mask-studio-state.v1` | State local persisted cho session/preset/recent | Chỉ JSON declarative bounded; không có pixels/path/callable |
| `image-mask-export.v1` | Xuất/import mask layer đã validate | Source/mask artifact opaque ID, operation và Studio provenance |
| `image-mask-preset.v1` | Layer summary có thể áp dụng lại | Layer metadata/mask operation/adjustment JSON safe |
| `creative-asset.v1` | Project-side lineage/provenance sau khi gắn | Artifact reference, parent immutable và `image_mask_studio_links` bounded, không chứa Studio state |

Layer được allowlist là `source`, `mask`, `adjustment`, `generated`. Mask chỉ
nhận `brush`, `invert`, `feather`, `grow`, `shrink`; brush là `add` hoặc
`subtract`, với tọa độ 0–1 và số điểm bounded. Adjustment chỉ nhận
`brightness`, `contrast`, `saturation`, `exposure`, `temperature`, `crop` cùng
JSON an toàn. Undo/redo, snapshot và preset lưu **mô tả** layer, không nhân bản
media. Generated parent phải thuộc stack acyclic; Hub không gỡ parent khi child
còn tham chiếu. Xóa layer chỉ bỏ reference Studio, không gọi Artifact Store để
xóa file.

Project link dùng một intent durable có token/revision trước, khóa mutation của
phiên cho đến khi completion xác nhận đúng token/revision, sau đó Project Manager
xác minh mọi artifact là opaque image artifact thuộc session và thêm reference
idempotent về media/reference. Mỗi Project giữ record Studio ID/revision, source,
tập artifact và chỉ mask artifact nằm trong tập đã chọn, kể cả source-only link.
Artifact dùng chung không bị ghi đè: parent đầu tiên là immutable; link Studio
cùng source được append bounded, còn claim source khác bị từ chối. Project không
nhận canvas data URL, raw path hoặc manifest tùy ý. Nếu bước Project lỗi, intent
được giữ cho một lần retry/recovery có chủ đích.

### HTTP surface M6A

| Route | Chức năng |
| --- | --- |
| `GET /api/image-mask-studio/overview`, `/preflight` | Session/preset/recovery và trạng thái capability có reason/action |
| `GET /api/image-mask-studio/sessions/{id}` | Chi tiết layer stack của một opaque Studio ID |
| `POST /api/image-mask-studio/sessions`, `PUT /sessions/{id}` | Tạo từ image artifact Hub; đổi title/project với `base_revision` tùy chọn |
| `POST /sessions/{id}/layers`, `PUT /layers/{layer}` | Thêm/cập nhật layer metadata đã validate |
| `POST /layers/{layer}/operations`, `/move`, `/remove` | Thao tác mask, đổi thứ tự hoặc gỡ reference không phá hủy |
| `POST /sessions/{id}/undo`, `/redo`, `/save` | History bounded và explicit local save/snapshot |
| `GET /sessions/{id}/compare`, `POST /snapshots/{snapshot}/restore` | So sánh/khôi phục metadata snapshot |
| `GET /layers/{layer}/export`, `POST /masks/import` | Mask manifest safe export/import |
| `POST /presets`, `/presets/{preset}/apply`, `/link-project` | Preset declarative và workflow gắn Project hai bước |

Unknown opaque record trả `404`; payload/ID/schema không hợp lệ trả `400`.
Mutation với `base_revision` cũ trả `409` kèm revision hiện tại và action tải lại
bản nháp server trước khi ghi tiếp. API công khai không projection raw filesystem path,
pixels, input bytes, private state path, secret hay callable runtime.

### Preflight và giới hạn trung thực

`local_non_destructive_layers` là `operational` vì không gọi model và chỉ ghi
metadata/vector bounded. `sam2_assisted_mask` là `unavailable` theo mặc định;
nếu policy local đặt `sam2_assist.configured: true`, nó chỉ lên `partial` và vẫn
cần smoke runtime được ủy quyền. `image_inpaint` và `image_outpaint` là
`unavailable` cho đến khi có adapter an toàn cùng smoke riêng. Mọi card phải giữ
`status`, `reason`, `action`; không dùng sự tồn tại của cài đặt SAM2 hay Qwen
image-to-image làm bằng chứng thay thế.

Trong resource-safety override, M6A không khởi chạy/kiểm tra SAM2, inpaint,
outpaint, ComfyUI, FFmpeg hay workload GPU/video. Evidence runtime đó là
`deferred due GPU/resource contention`, không phải tính năng hoạt động.

### Extension point và kiểm thử

- Một renderer/mask engine tương lai phải có adapter và bounded functional smoke
  riêng. Nó đăng ký output qua Artifact Store trước, rồi mới tạo `generated`
  layer/provenance bằng opaque artifact ID; không được ghi đè source hay để UI
  gửi path/command.
- Import mask chỉ nhận contract/layer allowlist đã validate; export không phải là
  ảnh PNG. Muốn trao đổi pixel, dùng Artifact Store contract riêng.
- Test mục tiêu là `tests/test_milestone6_image_mask_studio.py`: contract
  manager/schema, safe recovery, revision conflict, mask import/export, preset,
  project lineage, loopback API và canvas/UI bounded. Chúng dùng fixture nhỏ,
  không chứng minh hoặc khởi chạy inference/GPU/video.

Chi tiết UX và hướng dẫn vận hành nằm ở
[MILESTONE_6A_IMAGE_MASK_STUDIO.md](MILESTONE_6A_IMAGE_MASK_STUDIO.md).

## M4A Reliability & Large Media ownership

Product release version nằm duy nhất ở `src/shared/version.py` (`5.0.0`). Nó
được projection vào `/health`, HTTP server header và tracked component example;
không đổi schema/contract version riêng của job, graph hoặc creative records.

V5-C bổ sung Workflow Library local-first trong services/workflow_library/.
Library chỉ lưu declarative graph metadata, dùng workflow-library.v1, atomic
replace và optimistic library revision. Import/export đóng, canonical và
redacted; migration từ localStorage luôn dry-run/user-mediated. UI không gọi
route đoán trước: bridge server-owned chỉ được wire trong V5-D, còn thiếu
bridge thì hiển thị partial/reason/next action.

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
## V5-D End-to-End productization boundary

V5-D is a composition layer, not a replacement engine.  The API bootstrap
snapshot joins the existing capability registry/Module Manager preflight,
durable public job records, Workflow Library revision state, and Artifact Store
health through `src/services/api/v5_productization.py`.  Its output is
`v5-product-surface.v1`, always `execution: not_run` and `dry_run: true`.

The Dashboard consumes that server-owned snapshot for readiness, module
reason/next-action, recovery counts, and GPU/storage warnings.  Workflow
Library routes use the existing validator and atomic optimistic-revision store:
list/read are read-only, save/import/delete require typed revisions, migration
has separate dry-run and user-confirmed endpoints, and conflict/recovery
responses remain visible instead of overwriting local state.

No client callable, command, manifest, raw path, secret, model/media blob, or
execution descriptor is reflected into these public projections.  Runtime,
provider, GPU, video, download, and install claims remain deferred unless a
separate bounded evidence gate exists.
