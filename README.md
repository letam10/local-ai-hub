# Local AI Hub

Trình điều phối chạy thuần Windows cho các ứng dụng và engine AI cục bộ. Kho mã
này chỉ chứa mã nguồn first-party, adapter, wrapper, ví dụ cấu hình và tài liệu;
không chứa bản cài đặt trên máy, model, environment, cache hay dữ liệu media cá
nhân.

## Các nguyên tắc an toàn

- Thư mục gốc cài đặt cục bộ được cung cấp qua `LOCAL_AI_HOME`; repository
  không cần và không chứa đường dẫn tuyệt đối riêng của máy.
- Các bản cài đặt AnimeSR, SAM 2, FFmpeg, Whisper/ASR, Ollama, ComfyUI và AIRI
  đang có được tham chiếu qua cấu hình cục bộ bị Git bỏ qua; chúng không bị di
  chuyển hoặc ghi đè.
- Model nặng chỉ được nạp theo yêu cầu; chính sách mặc định chỉ cho phép một
  tác vụ GPU nặng chạy tại một thời điểm.
- API chỉ bind vào `127.0.0.1`. Cầu nối MCP mặc định sử dụng stdio.
- Media nguồn không bao giờ bị ghi đè; kết quả được ghi bên dưới thư mục output
  cục bộ đã cấu hình.
- Dùng smoke test chức năng có giới hạn thay cho vòng lặp benchmark hoặc stress
  test.

## Mô hình trạng thái sẵn sàng

Local AI Hub báo cáo hai trạng thái độc lập:

- `component_status` mô tả trạng thái thành phần cục bộ đã phát hiện, ví dụ
  `installed`, `running`, `missing` hoặc `planned`.
- `tool_status` mô tả mức độ sẵn sàng của một công cụ đã được allowlist:
  `operational`, `partial`, `queue_only`, `unavailable`, `planned` hoặc
  `error`.

Một component đã được cài đặt không đồng nghĩa mọi tool dựa trên component đó
đều đang hoạt động. Ví dụ, SAM 2 có thể được cài dưới dạng GUI bên ngoài trong
khi các tool `segment_image` và `track_video_object` vẫn ở trạng thái không khả
dụng. Một tool `partial` sẽ tự báo `unavailable` khi component cục bộ mà nó cần
chưa được cài đặt.

## Mức độ sẵn sàng backend hiện tại

Nội dung sau mô tả contract của mã nguồn đã import. Nó không khẳng định một máy
cụ thể đã cấu hình đầy đủ mọi component cục bộ.

### Đang hoạt động

Smoke test control-plane không cần model, có giới hạn, bao phủ các route sau
khi Hub đang chạy:

- `GET /health`
- `GET /tools`
- `GET /models`
- `GET /components`
- `GET /jobs`

### Hoạt động một phần

Các route này có adapter được allowlist, nhưng repository chưa lưu kết quả smoke
test backend chức năng có giới hạn:

- `/media/probe`
- `/vision/ui/parse`
- `/vision/detect`
- `/vision/ground`
- `/ocr/parse`
- `/speech/transcribe`
- `/voice/tts`
- `/voice/design`
- `/voice/clone`
- `/voice/convert`

### Chỉ đưa vào hàng đợi

- `/video/upscale/anime` tạo job record có kiểm soát nhưng không thực thi
  AnimeSR cho đến khi executor được xác minh.

### Chưa khả dụng / chờ backend

- `/vision/segment` và `/vision/track` vẫn chưa khả dụng vì adapter backend
  trực tiếp của SAM 2 chưa được xác minh.
- `/video/subtitle` vẫn chưa khả dụng vì quá trình mux đầu ra subtitle-video
  chưa được xác minh.

## Mã nguồn V2 và bố cục hệ thống tệp

```text
D:\LocalAIHub\
|- src\
|  |- app\                 # bootstrap và vòng đời ứng dụng desktop
|  |- app_config\          # mặc định/schema/service cho settings
|  |- modules\             # một contract cho mỗi capability
|  |- services\            # API, MCP, jobs, storage, migration
|  `- shared\              # paths, schemas, validation và utilities dùng chung
|- Config\                 # chữ hoa/thường chuẩn trên Windows; ví dụ được track
|- runtime\                # engine/ứng dụng bên thứ ba bị Git bỏ qua
|- Models\                 # checkpoint/thư mục model bị Git bỏ qua
|- Cache\ Output\ Temp\ Logs\  # trạng thái riêng của máy, bị Git bỏ qua
|- scripts\ patches\ tests\ docs\
`- dependencies.lock.json
```

GitHub là nguồn chuẩn cho mã nguồn và tài liệu. Engine runtime, model,
environment, cache, output, file tạm và secret được cài riêng và tuyệt đối
không được commit. `Config/` là cách viết hoa/thường chuẩn duy nhất trên
Windows; không tạo cây `config/` song song.

Migration manifest cục bộ là `Config/layout_migration.local.json` (bị Git bỏ
qua). Phần giải thích đã được làm sạch về bố cục và quy tắc rollback nằm tại
[`docs/FILESYSTEM_LAYOUT_V2.md`](docs/FILESYSTEM_LAYOUT_V2.md) và
[`docs/MIGRATION_AND_ROLLBACK.md`](docs/MIGRATION_AND_ROLLBACK.md).

## Các engine cục bộ hiện có

- Vision: OmniParser v2, RF-DETR Nano, Grounding DINO Swin-T và PaddleOCR-VL
  1.6 có các adapter Windows native độc lập.
- Voice: Qwen3-TTS 0.6B CustomVoice, 1.7B VoiceDesign, 1.7B Base và Seed-VC
  tiny được biểu diễn bằng các adapter nạp theo yêu cầu.
- Component bên ngoài được tái sử dụng: AnimeSR v2, SAM 2, Faster-Whisper,
  FFmpeg/FFprobe, Ollama, ComfyUI và AIRI vẫn ở các đường dẫn hiện có.

Qwen3-TTS là engine giọng nói. Nó khác với các hệ thống tạo ảnh Qwen Image và
Qwen-Image-2512; model và thư mục của các hệ thống này không bao giờ được trộn
lẫn.

## API và MCP

API loopback là `http://127.0.0.1:8765`. Các route chỉ đọc gồm `/health`,
`/tools`, `/models`, `/components` và `/jobs`. `GET /tools` trả về cả
`component_status` lẫn `tool_status`, kèm lý do cho trạng thái capability được
báo cáo.

`src/services/mcp/local_ai_mcp_server.py` sử dụng stdio và chỉ chuyển tiếp các
tool đã đặt tên, nằm trong allowlist. Các shim `Hub/`, `Adapters/` và `MCP/` đã
deprecated vẫn được giữ để launcher/import hiện có tiếp tục hoạt động cho đến
khi có PR dọn dẹp riêng.

## Kiểm tra

```powershell
python scripts/ci_validate.py
python scripts/diagnose.py
python scripts/api_smoke.py --image <local-image>
python src/services/mcp/mcp_smoke.py
```

Chỉ chạy smoke check nhỏ nhất phù hợp với mục đích. Không dùng các lệnh này để
benchmark, stress test hoặc chạy inference model lặp lại.

Xem `Reports/` để biết evidence và các giới hạn đã được làm sạch. Report cục bộ
được tạo tự động có thể chứa đường dẫn riêng của máy và được Git bỏ qua một cách
có chủ đích.
