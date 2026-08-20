# Local AI Hub — Unified Workspace V5/V6 with V7 architecture foundation

V5 and V6 remain preserved product lines. The V7 foundation is an incremental
architecture layer: source checkout and machine data roots are resolved through
`src/platform/paths.py`, AI modules use validated `module.json` manifests, and
Model/Runtime Managers inspect and plan without downloading or running models.
For a clean clone, use `.\scripts\bootstrap_core.ps1`; it initializes only missing
local configuration from examples and opens the core without requiring AI
weights. See `docs/architecture/ARCHITECTURE.md` and
`docs/architecture/V7_MIGRATION_PLAN.md`.

## V7 Phase 2 — clean clone and managed components

Fresh Windows checkout:

```powershell
git clone <repository-url>
Set-Location .\local-ai-hub
.\scripts\bootstrap_core.ps1
```

Core bootstrap resolves the managed `Environments\core` Python first, then
reuses the legacy `Environments\hub`, then a supported Python 3.10+
development interpreter. `LOCALAIHUB_DATA_ROOT` may point machine-local data
outside the source checkout; the owner installation remains in legacy
single-root mode and is discovered without copying or moving its data.

Open `Components / AI Setup` to inspect module, runtime and model states. AI
installation is never automatic: the server creates an opaque inspect → plan
→ confirmation flow, with trusted catalog sources, bounded staging, checksum
and archive validation, native opaque model import, and atomic receipts.
Missing AI remains `NOT_INSTALLED`, `PARTIAL` or `INSTALLED_UNVERIFIED` until
separate bounded evidence proves `OPERATIONAL`. Core startup does not scan
drives, download models, launch AI runtimes, or change CUDA/drivers.

Local AI Hub là ứng dụng Windows điều phối các workflow AI chạy cục bộ trong
một cửa sổ. Kho Git chỉ chứa mã nguồn, cấu hình mẫu, launcher, adapter, script
và tài liệu; không chứa model, môi trường Python, cache, output, media cá
nhân, log cục bộ hay bí mật.

V3 chuyển workflow chính vào Hub thay vì mở GUI desktop riêng:

- SAM2, AnimeSR, Whisper, Voice, Vision, OCR, Image AI và FFmpeg chạy qua Hub
  API → Job Manager → worker/background backend ẩn.
- FLUX và Qwen Image gọi ComfyUI API trực tiếp; Local Image Studio chỉ thuộc
  Advanced/legacy, không phải luồng chính.
- AIRI là ngoại lệ cửa sổ riêng duy nhất. Hub không embed AIRI, không đọc hay
  sao chép API key của AIRI.

## Khởi động không hiện console

Cài hoặc cập nhật shortcut desktop một lần từ PowerShell:

```powershell
cd D:\LocalAIHub
pwsh -File .\scripts\update_managed_shortcuts.ps1 -Apply
```

Shortcut `Local AI Hub` gọi `Environments\hub\Scripts\pythonw.exe -m
src.app.main`: cửa sổ Hub mở tối đa, kích thước nhỏ nhất 1280 × 720 và không
hiện PowerShell, cmd hay Python console. Các shortcut GUI cũ chỉ được tạo khi
chủ động dùng `-IncludeLegacy`.

Phạm vi của mô tả trên chỉ là route khởi động chuẩn của Hub: shortcut này không
gọi PowerShell hoặc cmd và `pythonw.exe` không tạo console riêng. Cờ
`CREATE_NO_WINDOW`/`SW_HIDE` chỉ kiểm soát tiến trình con do Hub sở hữu; chúng
không sửa PowerShell, Windows Terminal, Codex/ChatGPT hay tiến trình bên ngoài.
Nếu vẫn thấy cửa sổ console nháy, cần truy vết PID, tiến trình cha, executable
và HWND để xác định đúng nguồn trước khi khẳng định đã khắc phục.

Cho mục đích phát triển, có thể chạy giao diện web tại `http://127.0.0.1:8765/ui/`:

```powershell
.\scripts\start_web_ui.cmd
```

API chỉ bind loopback `127.0.0.1`; UI không nhận command shell, executable hay
đường dẫn máy tùy ý.

## Workflow trực tiếp trong Hub

| Workspace | Luồng chính |
| --- | --- |
| Vision Studio | OmniParser, RF-DETR, Grounding DINO và JSON/annotation output |
| SAM2 | Upload ảnh/video, chọn điểm hoặc kéo box trên preview, prompt Grounding DINO, mask/tracking output |
| AnimeSR | Upload, queue, scale, chunk, cancel/resume; RIFE/Real-ESRGAN giữ `partial` khi chưa có CLI contract đã xác minh |
| Whisper | Transcript/SRT, dịch khi backend hỗ trợ và burn subtitle qua FFmpeg ẩn |
| Voice Studio | Qwen3-TTS, Voice Design/Clone và Seed-VC qua worker nền |
| Image AI | Quick FLUX/Qwen qua ComfyUI API, Hub Nodes, Image & Mask Studio không phá hủy và ComfyUI Advanced nhúng trong cùng cửa sổ |
| Media | Probe, trim, concat, resize/crop/rotate/FPS, transcode, audio/subtitle, frames và image sequence qua FFmpeg allowlist |
| OCR | Ảnh/PDF qua PaddleOCR worker, xuất text/Markdown/JSON/tables khi backend có sẵn |

Tệp tải lên được stage trong vùng `Temp` của Hub và nhận một artifact ID mờ.
API/UI chỉ nhận artifact ID và chỉ phục vụ/mở file thuộc `Temp`, `Output` hoặc
`Archive` của Hub; không trả đường dẫn cục bộ của máy cho trình duyệt.

## Trạng thái trung thực

`component_status` mô tả runtime quan sát được; `tool_status` mô tả mức độ
workflow trực tiếp đã được xác minh. `partial` nghĩa là adapter/form đã có
nhưng chưa có smoke chức năng bounded tương ứng. `unavailable` nghĩa là runtime
hoặc backend cần thiết không có. Không có job giả và không tuyên bố inference
hoạt động khi smoke chưa đạt.

Worker chỉ sở hữu PID do Hub tạo, chạy với cờ no-console và log dưới `Logs`.
Cancel/close chỉ dừng worker hoặc ComfyUI do Hub sở hữu; Hub không kill Python,
ComfyUI, AIRI hay tiến trình người dùng không liên quan.

## Bố cục cục bộ

```text
D:\LocalAIHub\
|- src\                 # UI, API, worker và desktop shell
|- Config\              # chỉ file *.example.json được theo dõi Git; policy/state local bị ignore
|- runtime\             # portable applications/engines, bị Git bỏ qua
|- Models\              # model local, bị Git bỏ qua
|- Environments\        # Hub và environment đã migrate, bị Git bỏ qua
|- Output\ Temp\ Archive\ Cache\ Logs\ Reports\
|- scripts\ tests\ docs\
`- dependencies.lock.json
```

Không sao chép model để hợp nhất. Không sửa driver NVIDIA, CUDA hoặc phần mềm
hệ thống. Không ghi đè media nguồn; output chỉ vào vùng Output/Archive của Hub.

## Historical V2/V3 migration record

V2/V3 migration scripts and their reports are retained only as a historical
record for a host that had not yet completed consolidation. A completed host
marks its local V2/V3 state as `historical_snapshot`; the scripts then refuse
to recreate a legacy migration plan or delete anything. Do not run them as a
normal startup, maintenance, or runtime workflow.

Historical local reports include:

- `Reports\LEGACY_CLEANUP_V3.local.md`
- `Reports\V3_INTEGRATION_AND_CLEANUP.local.md`
- `Config\legacy_cleanup_v3.local.json`

Sau một migration được review riêng, `scripts\write_v3_final_report.py` có thể
đọc state lịch sử và ghi báo cáo bàn giao local. Nó không thực hiện cleanup.
Environment Python ngoài Hub luôn cần rebuild có kiểm soát, không kéo-thả/move
venv. Xem [migration và rollback](docs/MIGRATION_AND_ROLLBACK.md) để biết ranh
giới historical này; tiến trình đang hoạt động, user data, system-managed app
và dữ liệu chưa xác minh luôn được giữ lại.

## Kiểm tra có giới hạn

Không benchmark, không loop inference, không stress GPU. V3 có tối đa ba vòng:

1. Static/control plane: syntax, unit test, API, `/ui/`, routing, registry,
   storage và launcher no-console.
2. Functional integration: tối đa một input nhỏ cho từng module khả dụng.
3. Chỉ sau khi sửa lỗi; cold start/restart và regression tối thiểu.

Ví dụ kiểm tra repository:

```powershell
python .\scripts\ci_validate.py
python -m unittest discover -s tests -v
git diff --check
```

Xem thêm [kiến trúc một cửa sổ V3](docs/TRUE_SINGLE_WINDOW_V3.md),
[Node Studio V4](docs/NODE_STUDIO_V4.md), [filesystem layout V2](docs/FILESYSTEM_LAYOUT_V2.md),
[migration và rollback](docs/MIGRATION_AND_ROLLBACK.md) và
[tích hợp AIRI](docs/AIRI_INTEGRATION.md).

## V5 — Node Studio, ComfyUI Advanced và phân phối

V5 thay editor Node Studio tự viết bằng LiteGraph.js offline đã pin phiên bản.
Image AI có ba workspace: Quick, Hub Nodes và ComfyUI Advanced. Advanced nhúng
frontend ComfyUI gốc vào chính WebView Local AI Hub qua loopback, không mở
Chrome hoặc Edge ngoài cho luồng bình thường. Bridge workflow nối artifact Hub
với ComfyUI nhưng không chấp nhận đường dẫn máy thô.

V5-D now wires the typed server-owned bridge described below; the historical
V5-C paragraph remains as a record of the pre-integration partial state.

V5-C bổ sung Unified Workspace và Workflow Library local-first: Dashboard dẫn
người dùng qua Project/Workspace → Capability → Workflow/Nodes → Job →
Artifact/Preview. User workflow state ở local ignored config, schema
workflow-library.v1 đóng và revision-safe; nếu V5-D chưa wire bridge thì UI
hiển thị partial/reason/next action thay vì giả nhận operational.

Phân phối V5 giữ Git nhỏ: Core release chỉ dành cho launcher, frontend, API,
runtime bootstrap nhỏ và wheelhouse nhỏ; model, CUDA, Torch, Paddle,
environment, cache và dữ liệu người dùng không vào Core. Module chỉ có thể tải
on-demand khi manifest có URL HTTPS và SHA-256 đã xác minh; Full không tự tải
model nếu chưa có xác nhận rõ.

Xem thêm tài liệu về [Node Studio V5](docs/NODE_STUDIO_V5.md),
[launcher không console V5](docs/NO_CONSOLE_V5.md),
[Source Audit V5](docs/SOURCE_AUDIT_V5.md) và
[phân phối V5](docs/DISTRIBUTION_V5.md).

V4 thêm tab `Nodes` vào Image AI, SAM2, Media và AnimeSR. Node Studio chạy
offline trong WebView, có typed sockets, DAG validator/cycle detection,
per-node progress, artifact ID riêng tư, cache theo content hash, autosave
local, import/export JSON, undo/redo, preview và Cancel thuộc đúng graph job.
Preset source được theo dõi trong [`workflows/`](workflows/); workflow cá nhân
ở `localStorage` hoặc tệp `.local.json` bị Git bỏ qua.

Desktop hiện mở loading screen ngay khi tạo WebView rồi khởi động API hidden
song song. Tất cả subprocess Hub-owned, bao gồm `tasklist`, `nvidia-smi`,
FFmpeg/FFprobe, worker, ComfyUI và `taskkill`, đi qua helper Windows chung để
không tạo console. Polling nhanh chỉ refresh health/jobs; GPU, application,
model và storage dùng cache hoặc chỉ scan theo yêu cầu. Xem chi tiết tại
[Node Studio V4](docs/NODE_STUDIO_V4.md).

## MILESTONE 1 — Node Image Studio và nền tảng UX

Milestone 1 hoàn thiện một luồng ảnh trực tiếp trong Hub Nodes:

```text
Prompt / Image artifact → Generate → Image Edit (tuỳ chọn)
                       → Upscale → Preview → Save / Export
```

Canvas LiteGraph hiển thị tên node dễ hiểu, cổng có kiểu (`IMAGE`, `TEXT`,
`NUMBER`, `METADATA`...), kết nối sai kiểu, input bắt buộc thiếu, cạnh trùng và
chu trình đều bị validator từ chối. Người dùng có thể zoom/pan, bố trí lại,
undo/redo, lưu cục bộ, import/export graph, chọn template và theo dõi trạng
thái từng node trong queue job. Palette và Inspector luôn hiển thị lý do cùng
“Bước tiếp theo” cho backend chưa sẵn sàng.

Các template ảnh được theo dõi trong Git:

- [`workflows/image_create_upscale.json`](workflows/image_create_upscale.json):
  prompt → FLUX → upscale fallback → preview → save.
- [`workflows/image_edit_upscale.json`](workflows/image_edit_upscale.json):
  image artifact + prompt → Qwen image-to-image → upscale → preview → save.

FLUX, Qwen và Image-to-Image hiện mang trạng thái `partial` vì cần ComfyUI,
model và bounded smoke tương ứng. `image_upscale` có fallback FFmpeg rõ ràng;
không được gọi là AI upscaler. API registry dùng `node-studio.v2`, run/provenance
dùng `node-run.v2`, còn job công khai dùng `job.v2`. Các trường `reason`,
`action`, `next_action` và provenance chỉ công bố thông tin an toàn, không trả
đường dẫn máy hay input thô.

Xem [tài liệu Node Image Studio Milestone 1](docs/NODE_IMAGE_STUDIO_MILESTONE_1.md)
và [bản đồ kiến trúc/thư mục](docs/ARCHITECTURE.md) để biết ownership, route,
template, cấu hình mẫu và cách chạy kiểm thử bounded. Milestone 2 (workflow
video) đã được mở sau PASS của Milestone 1; xem [Video Creative Workflow
Milestone 2](docs/VIDEO_CREATIVE_WORKFLOW_MILESTONE_2.md). Generation video vẫn
hiển thị `unavailable` cho tới khi backend local có bounded smoke; transform và
FFmpeg fallback được cung cấp như đường đi thực tế.

## MILESTONE 3 — Unified Creative UX và workflow productivity

Milestone 3 chuẩn hóa Image AI, Media/Video, SAM2 và AnimeSR trong cùng một cửa sổ:

- Workspace header, navigation, loading/empty/error/unavailable state, reason/action,
  responsive layout và accessibility cơ bản dùng chung.
- Hub Nodes có template discovery, Recent workflows, rename, duplicate, autosave/recovery,
  import validation, export JSON và cảnh báo thay đổi chưa lưu. Workflow cá nhân chỉ nằm
  trong `localStorage` của WebView; preset nguồn vẫn nằm trong `workflows/`.
- Jobs có filter queue/history, progress, cancel, retry an toàn, `next_action`, provenance
  theo node và artifact preview/save/export ngay trong Hub.
- Image Generate/Edit/Upscale và Video Transform/Upscale/Interpolate/Encode/Export đi qua
  form/graph contract hiện có. Backend chưa smoke luôn giữ `partial` hoặc `unavailable` cùng
  reason/action; không tuyên bố inference hoạt động giả.

### Bản đồ source và dữ liệu

| Đường dẫn | Chức năng và ownership |
| --- | --- |
| `src/app/` | Desktop shell, WebView và bootstrap API; không chứa workflow runtime |
| `src/ui/` | Pages, navigation, CSS design system, Node Studio, event/API client; không gọi shell |
| `src/services/api/` | Loopback routes, capability catalog, upload/artifact và public response |
| `src/services/node_studio/` | Registry typed socket, schema/DAG validator, engine và provenance |
| `src/services/job_manager/` | Queue, progress, cancel/retry và process ownership |
| `src/modules/` | Adapter local cho ComfyUI, FFmpeg, SAM2, AnimeSR, Whisper, Vision… |
| `src/shared/` | Path registry, type và utility dùng chung |
| `workflows/` | Preset graph JSON được track trong Git; không chứa model/output/path/secret |
| `Config/*.example.json` | Cấu hình mẫu để setup máy; config local thực tế bị Git ignore |
| `Output/`, `Temp/`, `Cache/`, `Logs/`, `Reports/` | Dữ liệu runtime, artifact và log local; không commit |
| `Models/`, `runtime/`, `Runtimes/`, `Environments/` | Model/portable runtime/environment đã cài; không tự di chuyển hoặc copy |
| `tests/`, `scripts/`, `docs/` | Bounded contract tests, launcher/audit và tài liệu architecture/milestone |

### Phát triển và kiểm tra

```powershell
cd D:\LocalAIHub
node --check src\ui\app.js
node --check src\ui\pages.js
node --check src\ui\node_studio.js
python -m unittest -q tests\test_milestone3_contracts.py tests\test_unified_ui.py tests\test_v4_node_studio.py
python scripts\ci_validate.py
git diff --check
```

HTTP/UI smoke chỉ dùng loopback và dữ liệu nhỏ để kiểm tra route, navigation, state card,
Recent/duplicate/rename và Jobs filter. Milestone 3 không benchmark. Trong resource-safety
override, không chạy FFmpeg/NVENC, video generation/transform/upscale/interpolation/encode,
AnimeSR/RIFE hoặc ComfyUI video; functional video re-smoke ghi `deferred due GPU/resource
contention`.

Xem chi tiết contract tại [MILESTONE_3_UNIFIED_CREATIVE_UX.md](docs/MILESTONE_3_UNIFIED_CREATIVE_UX.md)
và ownership tại [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## MILESTONE 4A — Creative Projects, Asset Library và Reproducible Recipes

Milestone 4A bổ sung một lớp tổ chức sáng tạo cục bộ phía trên workflow đang có:

```text
Project → Artifact đã có trong Hub → Recipe có version → Workflow template
        → Compare Board → chọn/favorite → quay lại Recipe
```

`Projects & Recipes` trong thanh điều hướng không tạo một backend inference mới.
Nó chỉ tổ chức metadata nhỏ, an toàn và có version cho những artifact mà Hub đã
đăng ký. Artifact vẫn thuộc Artifact Store; project không copy media, output,
model, environment hoặc bí mật.

### Creative Project workspace

- Tạo, mở, đổi tên, archive/khôi phục và chọn lại project gần đây. Recent bị
  giới hạn 12 project và dữ liệu project dùng `creative-project.v1`.
- Workspace local dùng `creative-workspace.v1`; file thực tế là
  `Config/creative_workspace.json`, bị Git ignore. Mẫu rỗng, hợp lệ và được
  theo dõi là [`Config/creative_workspace.example.json`](Config/creative_workspace.example.json).
- Lỗi JSON/top-level không hợp lệ luôn trả trạng thái `recovery_required` và
  **không bị Hub tự ghi đè**. Bản ghi con không hợp lệ được bỏ qua có kiểm soát
  với trạng thái `partial_recovery`.
- Export/import Project Manifest dùng `creative-project-export.v1`, chỉ chứa
  relative/opaque reference, recipe metadata và compare metadata. Conflict
  được chọn rõ là `copy`, `skip` hoặc `replace`.

### Asset Library và Compare Board

Asset Library hiển thị contact sheet/preview cho image artifact, placeholder
cho media khác, tìm theo tên/tag, filter favorite/collection và gắn asset hiện
có vào project mà không sao chép file. Mọi action public dùng ID dạng
`artifact_<opaque-id>`; không nhận hoặc trả raw filesystem path. Tag, favorite,
collection, lineage (`parent_artifact_id`/derived ID) và provenance đều là
metadata JSON an toàn.

Compare Board tối đa 8 artifact thuộc cùng một project. Nó so sánh
metadata/settings/provenance, lưu lựa chọn hiện tại, có thể favorite lựa chọn
và mở lại recipe liên quan. Không có thao tác compare nào chạy FFmpeg, encode,
upscale hoặc inference.

### Prompt, Recipe và Template Gallery

Recipe `creative-recipe.v1` có prompt template, variables, style block,
negative block, seed, model, settings, tags và version. Mỗi lần sửa qua API
tăng version; áp dụng recipe chỉ điền các field **có thể chỉnh** của Image AI
Quick hoặc Image Hub Nodes, rồi người dùng quyết định có tạo job hay không.
Recipe Pack `creative-recipe-pack.v1` có import/export validation và cùng
chính sách conflict an toàn.

Workflow Template Gallery đọc các JSON đã theo dõi trong [`workflows/`](workflows/),
lọc theo category/search và nạp template vào scope Hub Nodes tương ứng. Card
luôn hiển thị preflight thật từ node registry: `partial`/`unavailable` kèm
reason/action không được nâng thành `operational` chỉ vì template tồn tại.

### Bản đồ thư mục M4A

| Đường dẫn | Ownership M4A |
| --- | --- |
| `src/services/project_manager/` | Schema, validation, safe recovery, Project/Asset/Recipe/Compare CRUD và import/export contract |
| `src/services/artifact_store.py` | Registry artifact canonical; M4A chỉ đọc/publicize opaque record đã đăng ký |
| `src/services/api/api_server.py` | Route `/api/creative`, `/api/projects`, `/api/assets`, `/api/collections`, `/api/recipes`, `/api/workflow-gallery` |
| `src/ui/pages.js` | Creative Workspace, empty/loading/error states, contact sheet, forms và Gallery card |
| `src/ui/app.js`, `src/ui/api.js` | Client API, state/event layer, download manifest/pack và handoff Quick/Hub Nodes |
| `src/ui/node_studio.js` | Áp dụng Recipe vào graph editable và nạp tracked template; không chạy graph tự động |
| `Config/creative_workspace.example.json` | Mẫu state/contract, không phải user workspace thực tế |
| `tests/test_milestone4_projects.py` | Contract, recovery, public opaque response, recipe graph và loopback API bounded |
| `docs/MILESTONE_4A_CREATIVE_PROJECTS.md` | Contract chi tiết, extension point, limitations và test gate |

### Kiểm tra M4A có giới hạn

```powershell
cd D:\LocalAIHub
node --check src\ui\api.js
node --check src\ui\app.js
node --check src\ui\pages.js
node --check src\ui\node_studio.js
python -m unittest -v tests\test_milestone4_projects.py
python scripts\ci_validate.py
git diff --check
```

Các kiểm tra trên chỉ dùng JSON nhỏ, loopback HTTP và graph recipe trong bộ
nhớ. Không benchmark. Khi resource-safety override còn hiệu lực, M4A không chạy
FFmpeg/NVENC, video generation/transform/upscale/interpolation/encode,
AnimeSR/RIFE hoặc ComfyUI video. Bằng chứng functional video tiếp tục được ghi
`deferred due GPU/resource contention`.

Chi tiết đầy đủ: [MILESTONE_4A_CREATIVE_PROJECTS.md](docs/MILESTONE_4A_CREATIVE_PROJECTS.md).

## MILESTONE 6A — Image & Mask Studio không phá hủy

Image & Mask Studio là workspace con của **Image AI**, dành cho việc tổ chức
layer, mask vector, adjustment metadata và artifact dẫn xuất đã có trong Hub.
Nó không thay thế ảnh nguồn, không render pixel, không tự tạo PNG mask/output
và không khởi chạy model. Canvas chỉ ghi hình học brush đã chuẩn hóa và metadata
bounded; mọi pixel vẫn thuộc Artifact Store.

```text
Artifact ảnh Hub (opaque ID) → Studio session → layer/mask/adjustment
                         → autosave + undo/redo + snapshot/preset
                         → so sánh metadata / liên kết Project + provenance
```

- Tạo phiên từ một image artifact đã đăng ký; thêm source, mask, adjustment
  hoặc generated layer. Gỡ layer chỉ gỡ reference Studio, không xóa artifact.
- Brush `add`/`subtract`, `invert`, `feather`, `grow`, `shrink`, thứ tự/độ mờ/
  visibility layer và adjustment đều là thao tác khai báo không phá hủy.
- Mỗi chỉnh sửa làm thay đổi document Studio được autosave cục bộ và tăng
  `revision`, có undo/redo bounded, snapshot, compare trước/sau và preset để
  áp dụng lại. Lưu preset hoặc hoàn tất pending link không thay document nên
  không tăng revision. Ghi đồng thời dùng `base_revision`; conflict yêu cầu tải
  lại bản nháp server trước khi ghi tiếp.
- Mask export/import dùng manifest đã validate. Muốn có PNG mask hoặc output
  mới, dùng một công cụ đã được ủy quyền, đăng ký/tải artifact qua Hub, rồi gắn
  opaque artifact ID vào layer; Studio không nhận raw path, data URL hay bytes.
- Liên kết Project idempotent về reference/media: Studio lưu pending intent có
  token/revision trước, khóa chỉnh sửa cùng phiên cho đến khi hoàn tất/retry,
  rồi Project Manager chỉ nhận reference artifact thuộc chính phiên và record
  provenance revision theo từng Project; không copy media, không ghi đè lineage
  đã có của artifact dùng chung và không thay đổi ownership Artifact Store. Lần
  gửi lại đúng project/artifact/revision đã completed trả thành công mà không
  tạo intent, snapshot hay revision mới.

### State, policy và an toàn dữ liệu

`Config/image_mask_studio.example.json` cũng đặt `limits.max_state_bytes` (mặc
định 8 MiB). Manager kiểm tra kích thước trước khi parse, chỉ đọc tối đa trần +
1 byte và fail-closed nếu state bị thay thế hoặc thay đổi trong lúc đọc. API,
metadata và state đều từ chối `NaN`, `Infinity` và `-Infinity`; state lỗi được
giữ nguyên và chuyển sang recovery chỉ đọc.
Writer cũng đếm byte serialized trước khi atomic replace nên không thể tự tạo
state vượt trần rồi mới phát hiện ở lần mở sau.

| Tệp | Vai trò | Git |
| --- | --- | --- |
| `Config/image_mask_studio.example.json` | Mẫu policy được track: giới hạn session/layer/history/snapshot/preset và cờ hiển thị SAM2 partial | Track |
| `Config/image_mask_studio.json` | Policy máy cục bộ tùy chọn, copy từ example khi cần tinh chỉnh giới hạn | Ignore |
| `Config/image_mask_studio_state.json` | Bản nháp, session, snapshot và preset runtime; Hub tự tạo/ghi nguyên tử | Ignore |

Hai tệp local trên là độc lập. Không dùng policy config để lưu bản nháp và không
commit state/pixel/model/secret. Nếu state không đọc được, sai contract hoặc có
record/session/history/snapshot vượt contract, Hub trả `recovery_required` hoặc
`recovered_partial` ở chế độ **chỉ đọc** và **không tự ghi đè** state cũ. Kiểm tra
snapshot/export trước khi tạo workspace local mới hoặc nhờ quản trị viên phục hồi.

### Trạng thái capability trung thực

`local_non_destructive_layers` là đường metadata cục bộ đã có contract. SAM2
chỉ là `partial` khi `sam2_assist.configured: true` và vẫn cần smoke runtime
được ủy quyền; nếu chưa cấu hình, nó là `unavailable`. Inpaint có mask và
outpaint luôn `unavailable` cho đến khi có adapter an toàn và smoke riêng. Trong
resource-safety override, SAM2/inpaint/outpaint không được khởi chạy; bằng chứng
functional GPU/video tiếp tục là `deferred due GPU/resource contention`.

### Kiểm tra M6A có giới hạn

```powershell
cd D:\LocalAIHub
node --check src\ui\image_mask_studio.js
node --check src\ui\api.js
node --check src\ui\app.js
node --check src\ui\pages.js
python -m unittest -v tests\test_milestone6_image_mask_studio.py
python scripts\ci_validate.py
git diff --check
```

Các kiểm tra M6A chỉ dùng JSON, artifact tổng hợp nhỏ, loopback HTTP và hợp đồng
UI/canvas. Chúng không chứng minh SAM2, inpaint, outpaint, ComfyUI, FFmpeg hay
bất kỳ inference/GPU/video runtime nào hoạt động.

Chi tiết về contract, API, recovery, security boundary và extension point nằm ở
[MILESTONE_6A_IMAGE_MASK_STUDIO.md](docs/MILESTONE_6A_IMAGE_MASK_STUDIO.md).

## Historical M4A Reliability & Large Media Hardening (V4)

Batch hardening M4A đã bổ sung close gate an toàn cho job đang chạy, persistence
bounded, streaming artifact/upload và cache state có giới hạn. Đây là ghi chép
V4 lịch sử, khi product version Hub công khai là **4.0.0**; product hiện hành
được định nghĩa duy nhất tại `src/shared/version.py`. Các contract `job.v2`,
`node-run.v2` và Creative `*.v1` vẫn giữ version độc lập.

- Khi đóng desktop có active job (`queued`, `starting`, `running`,
  `cancelling`), Hub đưa đúng ba lựa chọn: **Quay lại Hub**, **Hủy jobs và
  thoát**, hoặc **Giữ chạy nền vào khay**. Khi WebView đang loading/chuyển
  trang, bridge thử gửi lại rồi dùng trang lựa chọn local; nếu cả hai bề mặt
  không thể render, thao tác đóng vẫn bị veto và cửa sổ được giữ mở an toàn.
  Không có force-kill tự động khi timeout. Với API do shell/service khác sở
  hữu, active job vẫn đi qua decision gate, nhưng desktop không gửi lệnh hủy,
  shutdown hay idle-backend cleanup tới external owner.
- Artifact download sử dụng streaming + single byte ranges/HEAD. Upload v1 bắt
  buộc `Content-Length`, ghi 4 MiB/chunk vào `Temp/uploads/*.part`, kiểm tra
  SHA-256/disk và atomic register. Default 8 GiB/safety margin 512 MiB nằm ở
  [`Config/hub_config.example.json`](Config/hub_config.example.json).
- `Config/jobs.json` local chỉ giữ active jobs + 500 terminal gần nhất;
  terminal cũ vào `Archive/Jobs/` (đều ignored), rotate ở 16 MiB/file và giữ
  tối đa 30 file. Restart biến active record cũ thành `interrupted` có action
  tạo lại; chỉ `failed`, `cancelled`, `unavailable` có runner trong phiên hiện
  tại mới hiện retry. Callable/retry payload không bao giờ persist.
- Node cache là LRU 256; GraphRunRegistry giữ mọi active run + 100 terminal
  mới nhất. Multi-select Hub Nodes dùng plain-click additive và có blank canvas/
  Esc/**Bỏ chọn** để reset selection.
- ComfyUI Advanced vẫn **partial** cho đến Windows WebView manual acceptance.
  Trong resource override, checklist này và video smoke là `deferred due
  GPU/resource contention`, không được diễn giải thành operational.

Smoke Windows opt-in dùng `pythonw`, HTTP fixture loopback, child CPU dummy và
tray native để xác minh cold start, instance thứ hai dùng API external, close
zero-active, background/restore, cancel cooperative và cleanup. Nó không gọi
`/api/bootstrap`, không khởi chạy AI/GPU/video và chỉ chạy khi port `8765`
đang trống:

```powershell
& 'D:\LocalAIHub\Environments\hub\Scripts\python.exe' tests\windows_lifecycle_smoke.py --run
```

Xem flow, API ranges/upload, ownership, checklist ComfyUI và Windows lifecycle
evidence tại [MILESTONE_4A_RELIABILITY_HARDENING.md](docs/MILESTONE_4A_RELIABILITY_HARDENING.md).
## V5-D End-to-End product surface

The V5-D integration composes the server-owned capability control plane, the
durable job/recovery projection, the local Workflow Library, and opaque
Artifact Store records into the Dashboard and Workspace journey.  The
Dashboard receives a deterministic `v5-product-surface.v1` snapshot with
readiness reason/next action, module preflight status, job recovery counts, and
GPU/storage warnings.  Workflow Library API routes use the existing
`workflow-library.v1` validator and optimistic revisions for list/save,
import/export, conflict recovery, and user-confirmed migration.

All integration projections remain `execution: not_run` and `dry_run: true`;
the product does not accept client manifests, raw paths, secrets, commands, or
callables, and it does not imply runtime/provider/model/video readiness without
separate bounded evidence.  See
[`docs/V5_END_TO_END_PRODUCTIZATION.md`](docs/V5_END_TO_END_PRODUCTIZATION.md).
