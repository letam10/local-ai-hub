# Local AI Hub — Ứng dụng một cửa sổ V3

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
| Image AI | Quick FLUX/Qwen qua ComfyUI API, Hub Nodes và ComfyUI Advanced nhúng trong cùng cửa sổ |
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
|- Config\              # chỉ file *.example.json được theo dõi Git
|- runtime\             # portable applications/engines, bị Git bỏ qua
|- Models\              # model local, bị Git bỏ qua
|- Environments\        # Hub và environment đã migrate, bị Git bỏ qua
|- Output\ Temp\ Archive\ Cache\ Logs\ Reports\
|- scripts\ tests\ docs\
`- dependencies.lock.json
```

Không sao chép model để hợp nhất. Không sửa driver NVIDIA, CUDA hoặc phần mềm
hệ thống. Không ghi đè media nguồn; output chỉ vào vùng Output/Archive của Hub.

## Cleanup legacy V3 và rollback

Inventory trước, không xoá theo tên thư mục:

```powershell
python .\scripts\inventory_legacy_v3.py
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveVerifiedEmptyFolders
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveObsoleteJunctions
```

Hai lệnh cleanup mặc định là dry-run. Chỉ thêm `-Apply` sau khi report local
xác nhận đúng một `REAL_DIRECTORY` rỗng hoặc junction không còn tham chiếu.
Mỗi junction cần thêm chính xác `-ApprovedJunctionPath <path>`; điều này ngăn
một lần apply xóa hàng loạt junction được inventory tìm thấy.
Report bỏ qua Git gồm:

- `Reports\LEGACY_CLEANUP_V3.local.md`
- `Reports\V3_INTEGRATION_AND_CLEANUP.local.md`
- `Config\legacy_cleanup_v3.local.json`

Sau cleanup và kiểm thử, `scripts\write_v3_final_report.py` ghi báo cáo bàn giao
local cuối cùng từ các state trên. Script chỉ đọc state, yêu cầu ghi rõ tối đa ba
vòng validation và chỉ liệt kê path đã xóa khi path đó đã được xác nhận vắng mặt.

Environment Python ngoài Hub cần rebuild có kiểm soát, không kéo-thả/move
venv. Xem [migration và rollback](docs/MIGRATION_AND_ROLLBACK.md) để export
package state, tạo replacement, kiểm tra import, smoke một lần, cập nhật
adapter rồi mới đủ điều kiện xóa legacy environment. Tiến trình đang hoạt động,
user data, system-managed app và dữ liệu chưa xác minh luôn được giữ lại.

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
