# Local AI Hub V7

Local AI Hub là ứng dụng Windows dùng để tập trung các workflow AI cục bộ vào một giao diện desktop duy nhất. Hub quản lý UI, loopback API, Job Manager, component/model/runtime state, project, artifact, workflow và các adapter AI; các backend nặng vẫn chạy như worker hoặc service phía sau thay vì mở nhiều GUI rời rạc.

V7 là lớp kiến trúc và productization phát triển tiếp từ V6. V5 và V6 vẫn được giữ như các dòng lịch sử; V7 không yêu cầu di chuyển installation hiện có hoặc viết lại lịch sử của các phiên bản trước.

> **Trạng thái V7 hiện tại:** Core, Desktop shell, API, catalog V2, component lifecycle foundation, storage/privacy hardening và nhiều workflow đã được chuẩn hóa. Tuy nhiên việc một model xuất hiện trong catalog **không đồng nghĩa model đó đã sẵn sàng tải và cài tự động**. Các trạng thái chưa đủ bằng chứng vẫn được hiển thị là `MANUAL_IMPORT_ONLY`, `MANUAL_INSTALL`, `AUTH_REQUIRED`, `PARTIAL`, `INSTALLED_UNVERIFIED` hoặc `UNAVAILABLE` thay vì nâng sai thành `OPERATIONAL`.

## Milestone 3 — Unified Creative UX

Milestone 3 đưa Vision Studio và SAM2 vào workspace trực quan dùng chung: input đi qua
Artifact Store, job/progress/cancel hiển thị tại route hiện tại, còn Jobs vẫn là lịch sử
canonical. Các adapter Vision/SAM2 chỉ được nâng lên `operational` sau bounded smoke
khớp runtime; nếu thiếu resource hoặc evidence, UI phải giữ `partial`/`unavailable`.
Result contract dùng artifact ID opaque và tọa độ normalized, không đưa path máy vào
browser. Video/GPU smoke vẫn chịu resource-safety gate riêng và không được suy luận từ
file presence.

Khi mô tả machine setup, chỉ dùng các file mẫu được track dưới `Config/*.example.json`;
config local và model/runtime state không thuộc source release.

## 1. Mục tiêu của dự án

Local AI Hub được xây dựng theo các nguyên tắc chính:

- Một desktop app làm trung tâm thay vì mỗi công cụ AI mở một ứng dụng riêng.
- UI không truy cập trực tiếp filesystem, subprocess hoặc model; mọi thao tác đi qua API/service contract.
- Model, runtime và environment được quản lý bằng catalog, receipt, verification và state rõ ràng.
- Existing installation được ưu tiên reuse thay vì tự động copy, move hoặc tải lại dữ liệu lớn.
- Core phải có thể khởi động khi chưa có model.
- Không tự tải AI model ở startup, không tự thay driver/CUDA và không tự cài model chỉ vì model tồn tại trong catalog.
- Public/API boundary ưu tiên opaque ID và dữ liệu path-free thay vì để browser quyết định đường dẫn local.
- Trạng thái cài đặt local và trạng thái nguồn upstream là hai thông tin độc lập.
- Không giả lập `PASS` hoặc `OPERATIONAL` khi thiếu runtime evidence, checksum, license, authentication hoặc smoke verification cần thiết.

## 2. Kiến trúc tổng thể

Luồng dependency chuẩn của V7:

```text
Desktop/WebView
    ↓
UI
    ↓
Loopback API
    ↓
Domain Services
    ↓
AI Modules / Adapters / Workers
    ↓
Platform + Shared Contracts
```

| Layer | Thư mục chính | Trách nhiệm |
|---|---|---|
| Desktop | `src/app/` | Desktop shell, WebView, lifecycle, launcher/bootstrap composition |
| Presentation | `src/ui/` | Render UI, navigation, feature state; không sở hữu filesystem/subprocess |
| Transport | `src/services/api/` | Parse/validate request, route ownership, status mapping và serialization |
| Domain | `src/services/` | Job, artifact, project, workflow, catalog, component/model/runtime managers |
| AI modules | `src/modules/` | Manifest, capability contract, adapter và worker boundary |
| Platform | `src/platform/` | APP/DATA roots, containment, reparse/process safety |
| Shared | `src/shared/` | Schema và low-level contract dùng chung |

Dependency direction được giữ theo hướng `UI → API → services → modules → platform/shared`. Module mới được đăng ký qua manifest được validate thay vì cho phép arbitrary Python import từ dữ liệu người dùng.

### API route ownership

HTTP transport đi qua server-owned router rồi mới tới domain service. Route adapter chỉ chịu trách nhiệm request/response boundary; nó không sở hữu inference, model installation, project database hoặc job scheduling. Inventory route được quản lý riêng để tránh một endpoint bị match bởi nhiều implementation khác nhau.

## 3. APP_ROOT và DATA_ROOT

V7 tách khái niệm source/application root khỏi machine data root:

- `APP_ROOT`: source/build của Local AI Hub.
- `DATA_ROOT`: dữ liệu machine-local như Models, Environments, runtime, Output, Projects, Backups và Config.

Owner installation cũ có thể tiếp tục dùng single-root layout. V7 không tự di chuyển hoặc nhân bản model/runtime đang tồn tại. Khi cần một data root khác, setup hỗ trợ cấu hình riêng mà không biến source checkout thành nơi chứa toàn bộ dữ liệu AI.

Các thư mục dữ liệu lớn và dữ liệu cá nhân không thuộc Core release gồm điển hình:

```text
Models/
Environments/
runtime/
Output/
Projects/
Backups/
Reports/
Cache/
Temp/
Logs/
Config/ machine-local state
```

Kho Git chủ yếu chứa source, schema, catalog/config mẫu, scripts, launcher, adapter, tests và tài liệu; không dùng Git để phân phối model weights hoặc dữ liệu người dùng.

## 4. Cài đặt và first run

### Yêu cầu cơ bản

- Windows.
- PowerShell.
- Một Python interpreter tương thích với Hub hoặc managed Hub environment đã có sẵn.
- Model/GPU runtime chỉ cần khi người dùng thực sự sử dụng capability tương ứng.

### Clone và kiểm tra kế hoạch setup

```powershell
git clone <repository-url>
Set-Location .\local-ai-hub
.\scripts\setup_local_ai_hub.ps1
```

Lệnh mặc định là **plan/dry-run**. Nó không nên tự ý thực hiện các thay đổi lớn chỉ vì script được gọi.

### Áp dụng Core setup

```powershell
.\scripts\setup_local_ai_hub.ps1 -Apply
```

Khi `-Apply` thành công, setup có thể tạo/cập nhật shortcut đã được quản lý. Script chỉ nên tạo Core state còn thiếu; model/runtime AI không được tự động tải xuống như một side effect của Core setup.

Có thể truyền data root riêng khi cần:

```powershell
.\scripts\setup_local_ai_hub.ps1 -Apply -DataRoot "D:\LocalAIHubData"
```

## 5. Desktop launcher

Local AI Hub có desktop launcher dùng để mở app mà không cần giữ console window cho luồng sử dụng bình thường. Nếu cần cập nhật shortcut đã quản lý:

```powershell
pwsh -File .\scripts\update_managed_shortcuts.ps1 -Apply
```

Shortcut chỉ nên được tạo khi tìm thấy GUI-capable Python phù hợp. Desktop shell và API lifecycle vẫn dùng cùng kiến trúc với source/dev launch; launcher không tạo một implementation AI thứ hai.

## 6. Production catalog V2

Catalog production hiện dùng schema:

```text
v7-production-catalog.v2
```

Catalog lưu metadata về model/runtime, source identity, revision, files/leaves, install strategy, disposition, license/auth state, resource estimate và update dimensions. Unknown size/checksum không được tự bịa để làm UI trông đầy đủ hơn.

### Model đang được theo dõi

Catalog hiện theo dõi 14 model/model-asset entry:

| Nhóm | Model/asset |
|---|---|
| Video / Segmentation / Upscale | AnimeSR v2, SAM 2.1 Hiera Small, Practical-RIFE v4, Real-ESRGAN x4plus |
| Audio / Voice | Faster-Whisper large-v3, Qwen3-TTS 1.7B, Seed-VC |
| Vision / OCR | OmniParser v2, RF-DETR Base, Grounding DINO Base, PaddleOCR-VL 0.9B |
| Image | FLUX.2 Klein Base 4B FP8, Qwen Image 2512 FP8, ComfyUI workflow assets |

### Runtime đang được theo dõi

Catalog hiện theo dõi 15 runtime/environment entry. Các nhóm chính gồm FFmpeg/FFprobe, SAM2, AnimeSR, Faster-Whisper, ComfyUI, Practical-RIFE, Real-ESRGAN, OmniParser, RF-DETR, Grounding DINO, PaddleOCR-VL, Qwen3-TTS, Seed-VC, shared Voice environment và AIRI external application.

### Disposition hiện tại

- **FFmpeg/FFprobe**: runtime `AUTO_INSTALL_READY` với source/size/SHA-256 đã pin trong catalog.
- **AI model**: hiện chưa có model nào được coi là production `AUTO_INSTALL_READY`.
- Phần lớn AI model dùng `MANUAL_IMPORT_ONLY` cho tới khi asset graph, checksum, license và runtime dependency được review đầy đủ.
- **FLUX.2 Klein Base 4B FP8** dùng `AUTH_REQUIRED` vì source/provider có authentication/license gate.
- Phần lớn Python AI runtime đang ở `MANUAL_INSTALL`.
- **ComfyUI** và shared Voice runtime có thể ở dạng `REFERENCE_EXISTING` khi installation phù hợp đã tồn tại.
- **AIRI** là external application; Hub không coi AIRI như một local chat model được cài tự động từ catalog.

Disposition là contract về khả năng lifecycle, không phải marketing label. UI chỉ nên hiện `Download & Install` khi record thực sự đủ điều kiện tương ứng.

## 7. Component lifecycle

Luồng chuẩn của component manager:

```text
inspect
  ↓
plan
  ↓
explicit confirmation
  ↓
apply/import/reuse
  ↓
verify
  ↓
OPERATIONAL chỉ khi đủ evidence
```

Public client gửi component ID hoặc opaque plan ID; server mới là bên resolve catalog, source, root và execution details.

Các capability chính:

- **Inspect**: đọc trạng thái bounded từ catalog + managed roots.
- **Plan**: tạo server-owned plan và kiểm tra dependency/disk/source/policy.
- **Manual Import**: import model qua native/server-owned selection thay vì browser gửi raw path.
- **Existing Install Reuse**: đăng ký lại installation hợp lệ mà không buộc copy/redownload.
- **Verify**: kiểm tra fixed leaves, metadata, checksum/evidence theo mức phù hợp.
- **Repair/Update/Uninstall**: chỉ khả dụng khi component contract và executor tương ứng hỗ trợ an toàn; unsupported operation phải fail closed.

## 8. Installation state và Source state

Hai dimension này không được gộp làm một.

### Installation state

Các state chính gồm:

- `NOT_INSTALLED`
- `INSTALLING`
- `PARTIAL`
- `INSTALLED_UNVERIFIED`
- `OPERATIONAL`
- `UPDATE_AVAILABLE`
- `UNAVAILABLE`
- `BROKEN`

File tồn tại chỉ chứng minh một installation observation, **không tự động chứng minh `OPERATIONAL`**. `OPERATIONAL` cần runtime/module evidence phù hợp với catalog hiện hành.

### Source availability

Source/upstream có thể ở các trạng thái như `AVAILABLE`, `DEGRADED`, `AUTH_REQUIRED`, `LICENSE_REQUIRED`, `RATE_LIMITED`, `UNAVAILABLE` hoặc `UNKNOWN` tùy provider và context.

Một component local hợp lệ có thể hoàn toàn ở trạng thái:

```text
Installation: OPERATIONAL
Source: UNAVAILABLE
```

Điều đó có nghĩa local installation vẫn dùng được nhưng download/update/repair cần upstream có thể bị hạn chế. Source 404 hoặc provider outage không được tự biến model local thành `NOT_INSTALLED`.

## 9. Update Center

Update checking được thiết kế theo nguyên tắc:

- Manual mặc định.
- Có thể có lightweight schedule theo policy được hỗ trợ.
- Không polling Internet liên tục.
- Check Update không đồng nghĩa auto-install.
- `latest_upstream` và `latest_supported` là hai khái niệm khác nhau.
- Upstream có phiên bản mới nhưng Hub chưa review phải được thể hiện như unsupported/newer-upstream thay vì tự nâng cấp.
- Apply/update chỉ được thực hiện khi có catalog-bound plan, source/candidate hợp lệ và executor hỗ trợ operation đó.

Update dimensions có thể được phân loại thành `backend`, `runtime`, `dependencies` và `model`. Một label update trong catalog không có nghĩa mọi dimension đã có production executor hoàn chỉnh.

## 10. Workflow và backend

Luồng chính hướng tới việc chạy các capability qua Hub thay vì yêu cầu người dùng mở GUI riêng:

```text
UI
 ↓
Hub API
 ↓
Job Manager
 ↓
Module Adapter / Worker
 ↓
Backend hoặc external engine
 ↓
Artifact Store / Project workflow
```

Các nhóm capability gồm:

- SAM2: segmentation.
- AnimeSR: anime/video upscale backend.
- Practical-RIFE: frame interpolation.
- Real-ESRGAN: upscale.
- Faster-Whisper: transcription/subtitle workflow.
- Vision/OCR: OmniParser, RF-DETR, Grounding DINO, PaddleOCR-VL.
- Voice: Qwen3-TTS, Seed-VC.
- Image generation: FLUX/Qwen Image thông qua ComfyUI khi runtime/model/workflow assets tương ứng đã được chuẩn bị.
- FFmpeg/FFprobe: media tool/runtime dùng chung.
- AIRI: external application, không phải embedded Hub runtime.

Backend có trong source hoặc catalog không đồng nghĩa backend đã được smoke-test trên GPU của mọi máy. V7 giữ trạng thái `PARTIAL`, `MANUAL` hoặc `INSTALLED_UNVERIFIED` khi bằng chứng production chưa đủ.

## 11. Job, Artifact và Project boundary

Job Manager chịu trách nhiệm lifecycle của job; Artifact Store chịu trách nhiệm artifact publication/opaque artifact identity; Project layer quản lý metadata/workflow state thay vì để browser sở hữu raw filesystem.

Nguyên tắc an toàn:

- Không coi arbitrary path từ client là artifact authority.
- Artifact public dùng opaque ID.
- Upload/output phải qua bounded validation.
- Không xóa unknown/user file chỉ vì file nằm gần output của một job.
- Failed/cancelled workflow không được giả thành completed artifact.
- Output, preview và provenance phải phản ánh truthful state của job.

Một số output-producer ownership/reservation hardening vẫn thuộc nhánh V7 Operational Closure; vì vậy README không tuyên bố mọi producer path đã đạt final production ownership contract nếu chưa có QA tương ứng.

## 12. Bảo mật và dữ liệu

V7 áp dụng fail-closed ở các boundary có rủi ro cao:

- Path traversal và path escape bị từ chối.
- Symlink/junction/reparse cần được kiểm tra trước thao tác nhạy cảm.
- Public API không nên nhận arbitrary executable, command, destination hoặc download URL do browser tự chỉ định.
- Catalog/source metadata là server-owned authority cho component operations.
- Config/backup/workflow/project data dùng bounded schema và atomic-write pattern ở các đường đã được harden.
- Diagnostic/public projection không nên lộ secret, raw local path hoặc unrestricted exception/log content.
- Startup không tự quét toàn bộ ổ đĩa.
- Hub không tự đổi GPU driver hoặc CUDA installation.
- Core release không bundle Models, Environments hoặc user Output.

## 13. Không có local conversational brain mặc định

Local AI Hub không yêu cầu cài một local conversational LLM để Core hoạt động. Tool models như OCR, TTS, segmentation, image generation hoặc voice conversion được quản lý như capability riêng; chúng không được coi là chat brain của Hub.

AIRI là external application và được quản lý tách biệt với model/tool lifecycle của Hub.

## 14. Trạng thái hỗ trợ hiện tại

Điều V7 **đã có nền tảng**:

- Windows desktop shell/WebView.
- Loopback API + route ownership.
- Data-driven production catalog V2.
- Model/Runtime/Module Manager.
- Server-owned inspect/plan/confirmation lifecycle.
- Manual model import và existing-install reuse foundation.
- Runtime evidence và state projection.
- Job Manager, Artifact Store, Project/Workflow infrastructure.
- Backup/config/storage/privacy hardening.
- Node Studio và workflow UI foundation.
- Core packaging/setup/shortcut infrastructure.

Điều README **không tuyên bố đã hoàn tất** nếu chưa có evidence tương ứng:

- One-click install cho mọi AI model.
- Automatic Python environment rebuild cho mọi backend.
- GPU smoke/inference trên mọi model trong catalog.
- Full automatic repair/update cho mọi runtime/model/dependency graph.
- Việc upstream source luôn online hoặc có trusted fallback.
- Việc một file/model chỉ cần tồn tại là `OPERATIONAL`.

## 15. Project structure

```text
Config/                 Catalog/schema/config mẫu
architecture/           Inventory và architecture metadata
src/app/                Desktop shell và application lifecycle
src/ui/                 UI, feature presentation, Node Studio
src/services/api/       API router, route adapters, request/response boundary
src/services/           Domain services, managers, jobs, artifacts, projects
src/modules/            AI module manifests/adapters/workers
src/platform/           Paths, roots, containment và platform safety
src/shared/             Shared contracts/schema
scripts/                Setup, validation, packaging và maintenance scripts
docs/                   Architecture, operations và productization docs
tests/                  Regression/contract tests
```

Machine-local AI assets không nên được coi là source-tree content.

## 16. Validation dành cho development

Repository có source validation riêng. Một gate thường dùng:

```powershell
python -B scripts/ci_validate.py
```

Ngoài ra thay đổi production cần giữ các gate phù hợp như Python syntax/compile, Node syntax đối với JS đã sửa, `git diff --check`, contract tests và targeted regression trước khi chạy full suite. Test fixture không được dùng để tuyên bố real GPU/model execution nếu thực tế không chạy.

## 17. Tài liệu chính

- [`docs/V7_FINAL_PRODUCTIZATION.md`](docs/V7_FINAL_PRODUCTIZATION.md): productization boundary và installation contract.
- [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md): kiến trúc V7, layer ownership và compatibility.
- [`Config/v7_production_catalog.example.json`](Config/v7_production_catalog.example.json): production catalog V2 mẫu được track trong source.
- `docs/operations/`: tài liệu vận hành và các hardening contract theo feature/package.

## 18. Nguyên tắc khi phát triển tiếp

Khi thêm model/runtime/capability mới:

1. Khai báo manifest/catalog rõ ràng, không dùng floating source làm production authority nếu chưa được review.
2. Tách model, runtime, dependency và adapter thành các ownership riêng.
3. Xác định source identity, license/auth requirement, required leaves và verification level.
4. Chỉ cấp `AUTO_INSTALL_READY` khi install graph đủ thông tin và executor thực sự hỗ trợ.
5. Không nâng `OPERATIONAL` chỉ dựa vào file presence.
6. Không làm startup phụ thuộc vào việc model/provider đang online.
7. Không auto-update model/runtime mà không có plan và xác nhận phù hợp.
8. Giữ user data ngoài release artifacts và tránh destructive cleanup đối với dữ liệu không chứng minh được ownership.

---

Local AI Hub V7 ưu tiên **truthful state, reuse dữ liệu hiện có, server-owned lifecycle và fail-closed safety** hơn việc giả định mọi AI component đều có thể tải/cài/chạy tự động. Khi một capability chưa đủ source, runtime, license hoặc verification để được coi là production-ready, Hub phải hiển thị đúng giới hạn đó thay vì che giấu bằng trạng thái thành công giả.
