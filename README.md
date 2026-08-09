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
