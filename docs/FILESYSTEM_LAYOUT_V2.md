# Bố cục hệ thống tệp V2

Local AI Hub V2 tách hoàn toàn mã nguồn được theo dõi bởi Git khỏi phần cài
đặt AI cục bộ của từng máy. GitHub chỉ lưu source first-party, wrapper, cấu
hình mẫu, patch, test và tài liệu; không lưu model weight, environment, runtime
clone, cache, output, media tạm, log hoặc credential.

## Bố cục chuẩn

```text
<local-ai-hub>/
|- src/
|  |- app/                 # desktop shell pywebview
|  |- modules/             # capability manifest và adapter
|  |- services/            # API, MCP, jobs, registry, storage
|  |- shared/              # path registry, schema, validation
|  `- ui/                  # frontend HTML/CSS/JavaScript duy nhất
|- Config/                 # example được track; cấu hình máy bị ignore
|- runtime/                # app, engine và tool portable bị ignore
|- Models/                 # model/checkpoint bị ignore
|- Environments/ Cache/ Output/ Temp/ Logs/ Reports/
|- scripts/ patches/ tests/ docs/
`- dependencies.lock.json
```

`Config/` và `Models/` giữ đúng chữ hoa/thường trên Windows. Không tạo song
song `config/` hoặc `models/`. `runtime/` là cây canonical duy nhất; thư mục
legacy chỉ được giữ qua junction hoặc được báo `external_managed`.

## Desktop và frontend

`src/ui/` được API phục vụ ở `/ui/`. `src/app/main.py` tạo cửa sổ WebView2 qua
pywebview và nạp chính URL đó, vì vậy browser và desktop không bị lệch tính
năng. Cửa sổ có kích thước tối thiểu 1280 × 720 và được maximize sau khi native
window handle xuất hiện.

API chỉ đọc asset dưới `src/ui/`. Các thao tác khởi chạy ứng dụng dùng ID trong
application registry cục bộ; UI không thể truyền executable, working directory
hoặc lệnh shell tùy ý.

## Runtime và model

- `runtime/engines/{vision,speech,voice,image,video}` chứa engine upstream
  portable hoặc junction canonical.
- `runtime/tools/ffmpeg` là một runtime FFmpeg/FFprobe canonical, bao gồm DLL
  sibling cần thiết.
- `runtime/applications` chứa portable application; ứng dụng installer-managed
  chỉ xuất hiện trong registry external.
- `Models/{Vision,OCR,Speech,Voice,Image,Video}` là namespace model canonical.

Luôn ưu tiên configuration, model registry, extra-model path của framework hoặc
junction đã kiểm tra trước khi nghĩ đến sao chép model. Qwen3-TTS thuộc Voice;
Qwen Image thuộc Image và không được trộn lẫn. FLUX và Qwen Image có thể dùng
cùng ComfyUI và một model store vật lý, nhưng vẫn hiện thành capability riêng
trong UI.

Environment Python/Conda/venv không được di chuyển mù quáng. Nếu relocation
chưa có kế hoạch, rollback và smoke riêng thì environment vẫn là
`external_managed` hoặc `partial`. AIRI/Ollama cài đặt bởi hệ thống cũng giữ ở
vị trí installer-managed.

## Manifest và báo cáo cục bộ

Inventory của máy nằm trong `Config/layout_migration.local.json` (bị Git bỏ
qua). Mỗi entry lưu component, classification, nguồn/đích, nguồn/đích model,
environment, dung lượng, tiến trình tham chiếu, chiến lược, rollback, junction,
mức tin cậy, trạng thái, xác minh và quyền cleanup.

Các báo cáo sau cũng là local-only vì có thể chứa đường dẫn riêng của máy:

- `Reports/FINAL_STORAGE_PLAN.local.md`
- `Reports/LEGACY_AI_PATHS.local.md`
- `Reports/FINAL_LOCAL_AI_MIGRATION.local.md`

Không commit hoặc trích nội dung các tệp này vào PR. Chi tiết vận hành nằm ở
[migration và rollback](MIGRATION_AND_ROLLBACK.md).
