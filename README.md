# Local AI Hub — Ứng dụng hợp nhất V2

Local AI Hub là lớp điều phối chạy hoàn toàn trên Windows cho các ứng dụng và
engine AI cục bộ. Kho Git chỉ chứa mã nguồn, cấu hình mẫu, launcher, adapter và
tài liệu; không chứa model, môi trường Python, cache, kết quả, media cá nhân,
nhật ký cục bộ hoặc bí mật.

Phiên bản V2 cung cấp một giao diện duy nhất ở `/ui/`. Giao diện này chạy giống
nhau trong trình duyệt và trong cửa sổ desktop `pywebview` dùng WebView2. API
chỉ bind vào loopback `127.0.0.1`; không có CDN hay backend điều khiển tùy ý.

## Khởi động

Mở ứng dụng desktop (cửa sổ tối thiểu 1280 × 720, tự phóng to):

```powershell
cd D:\LocalAIHub
.\scripts\launch_local_ai_hub.cmd
```

Mở cùng giao diện bằng trình duyệt:

```powershell
cd D:\LocalAIHub
.\scripts\start_web_ui.cmd
# truy cập http://127.0.0.1:8765/ui/
```

Hoặc chỉ chạy API:

```powershell
.\scripts\start_api.cmd
```

Giao diện gồm Dashboard, AIRI, Vision Studio, SAM2, OCR, Whisper, Voice,
Image AI, Media, AnimeSR, Jobs, Models & Storage và Settings. Các thẻ điều
khiển chỉ gọi API loopback đã allowlist; không nhận executable hoặc tham số
shell từ trình duyệt.

## Trạng thái hiển thị trung thực

Hub phân biệt trạng thái thành phần và trạng thái công cụ:

- `component_status`: `installed`, `running`, `partial`, `not_installed` hoặc
  `external_system_app`.
- `tool_status`: `operational`, `partial`, `unavailable`, `planned` hoặc
  `error`.

Một ứng dụng desktop đã khởi chạy được không đồng nghĩa adapter API trực tiếp
đã được xác minh. Ví dụ, SAM2 Mask Studio và Anime Upscale Studio có thể mở từ
launcher quản lý, nhưng endpoint inference trực tiếp vẫn báo `unavailable` cho
đến khi có smoke test adapter riêng. AnimeSR không tạo job giả khi executor
chưa được xác minh. Whisper cũng được báo đúng là chưa cài nếu máy không có
backend và model tương ứng.

## Bố cục và lưu trữ V2

```text
D:\LocalAIHub\
|- src\ui\                 # giao diện HTML/CSS/JavaScript thuần
|- src\app\                # desktop shell pywebview
|- src\services\           # API, registry, storage và jobs
|- Config\                  # chỉ cấu hình mẫu được theo dõi bởi Git
|- runtime\                 # ứng dụng/engine bên thứ ba, bị Git bỏ qua
|- Models\                  # model cục bộ, bị Git bỏ qua
|- Environments\ Cache\ Output\ Temp\ Logs\ Reports\
|- Scripts\ tests\ docs\
`- dependencies.lock.json
```

Các model FLUX và Qwen Image dùng chung một kho vật lý thông qua đường dẫn
chuẩn và junction không sao chép dữ liệu. Qwen Image là hệ tạo ảnh; nó tách
biệt hoàn toàn với Qwen3-TTS (engine giọng nói). SAM2, AnimeSR, FFmpeg/FFprobe,
ComfyUI, Practical-RIFE và Real-ESRGAN chỉ được nhận diện là sẵn sàng khi
đường dẫn cục bộ tương ứng tồn tại và đã có kiểm tra phù hợp.

AIRI và Ollama là ứng dụng được cài đặt bởi hệ thống. Hub chỉ hiển thị và khởi
chạy chúng qua registry cố định khi được cấu hình; không di chuyển thư mục
cài đặt, model hay thông tin đăng nhập của chúng.

## Quy tắc an toàn dữ liệu

- Không sao chép model để “hợp nhất”; ưu tiên `Move-Item` cùng ổ đĩa sau khi
  kiểm tra tiến trình, sau đó tạo junction tương thích ở đường dẫn cũ.
- Không di chuyển environment Python/Conda/venv một cách mù quáng. Những môi
  trường không di động vẫn là external/partial trong registry.
- Không đụng đến thư mục chứa dự án, media, download, cache hoặc output của
  người dùng nếu chưa được phân loại rõ ràng là runtime portable.
- Không sửa NVIDIA driver, CUDA hay phần mềm hệ thống.
- Không ghi đè media nguồn. Kết quả chỉ thuộc thư mục output cục bộ được cấu
  hình.
- Model được tải theo nhu cầu; mặc định chỉ có một tác vụ GPU nặng tại một
  thời điểm.

`Config/layout_migration.local.json` là manifest cục bộ bị Git bỏ qua. Nó ghi
nhận nguồn/đích, dung lượng, chiến lược move, junction, rollback, mức tin cậy
và trạng thái xác minh. Ba báo cáo cục bộ cũng bị bỏ qua có chủ đích:

- `Reports/FINAL_STORAGE_PLAN.local.md`
- `Reports/LEGACY_AI_PATHS.local.md`
- `Reports/FINAL_LOCAL_AI_MIGRATION.local.md`

Chúng có thể chứa đường dẫn riêng của máy nên không được commit hoặc đưa vào
mô tả PR.

## Cấu hình cục bộ

Sao chép các tệp `Config/*.example.json` và `Config/local.env.example.cmd` theo
quy ước cục bộ của máy. Tệp cấu hình thật bị `.gitignore`; không thêm API key,
token, mật khẩu hoặc đường dẫn cá nhân vào Git. Khi thay đổi schema, hãy cập
nhật tệp `.example` tương ứng trong cùng PR.

`Config/application_registry.example.json` là allowlist launcher. Nó giới hạn
ứng dụng có thể mở và không chấp nhận lệnh hay đối số tùy ý từ API/UI.

## API cục bộ

Các endpoint chính:

- `GET /health`, `/tools`, `/models`, `/components`, `/jobs`
- `GET /api/dashboard`, `/api/storage`, `/api/models`, `/api/applications`,
  `/api/settings`
- `POST /api/applications/<id>/launch` và `/api/storage/scan`
- `POST /media/probe` cùng các route capability đã được allowlist

API phục vụ asset giao diện ở `/ui/` với kiểm tra đường dẫn để không thể truy
cập tệp ngoài `src/ui`. Gọi launcher chỉ sử dụng ID ứng dụng đã đăng ký; không
có route chạy shell tùy ý.

## Kiểm tra có giới hạn

Chạy kiểm tra phù hợp nhất với thay đổi, không benchmark hay stress test:

```powershell
python -m unittest discover -s tests
python scripts/ci_validate.py
python scripts/api_smoke.py --image <duong-dan-anh-cuc-bo>
python src/services/mcp/mcp_smoke.py
```

Có thể dùng `ffprobe` để kiểm tra media và launcher `--self-test` khi ứng dụng
hỗ trợ. Không chạy inference model nặng chỉ để chứng minh giao diện. Mọi công
cụ hoặc route chưa có smoke test chức năng phải tiếp tục hiển thị `partial` hoặc
`unavailable`.

Tài liệu bổ sung: [bố cục V2](docs/FILESYSTEM_LAYOUT_V2.md),
[migration và rollback](docs/MIGRATION_AND_ROLLBACK.md) và
[ứng dụng hợp nhất V2](docs/UNIFIED_APPLICATION_V2.md).
