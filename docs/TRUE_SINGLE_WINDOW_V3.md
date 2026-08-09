# Local AI Hub V3 — tích hợp thật trong một cửa sổ

## Mục tiêu và ranh giới

V3 thay thế các nút “mở Studio” trong workflow chính bằng form Hub gọi API
allowlist. Luồng xử lý là:

```text
UI Hub → Hub API loopback → Job Manager → worker hoặc backend nền ẩn → artifact Hub
```

AIRI là ngoại lệ duy nhất có cửa sổ riêng. AIRI vẫn do installer quản lý; Hub
không cố embed nó, không quản lý credential của nó và không dừng tiến trình
của nó.

SAM2 Mask Studio, Anime Upscale Studio và Local Image Studio chỉ còn là
advanced/legacy fallback. Chúng không xuất hiện như action chính trong sidebar
hay workspace.

## Worker và lifecycle

- Worker dùng command cố định, `shell=False`, `CREATE_NO_WINDOW` và startup
  info ẩn trên Windows.
- stdout/stderr được ghi dưới `Logs\workers`; không hiện terminal cho người
  dùng.
- Job Manager giữ handle/PID của process do chính Hub khởi tạo. Cancel chỉ tác
  động tới các handle đó.
- ComfyUI chỉ được Hub dừng khi Hub là bên khởi chạy nó. ComfyUI đang chạy từ
  bên ngoài chỉ được quan sát, không bị kill.
- Đóng desktop shell gọi lifecycle endpoint để dừng backend Hub-owned rảnh.

## Dữ liệu và artifact

Browser không truyền raw local path cho worker. Upload trả artifact ID; API
resolve ID vào file thuộc `Temp\uploads`. Output được đăng ký thành artifact
chỉ khi thuộc `Temp`, `Output` hoặc `Archive` của Hub. Response công khai thay
đường dẫn Windows bằng tên tệp hoặc artifact URL.

Do đó UI có thể preview/mở output trong Jobs mà không hiển thị bố cục ổ đĩa của
người dùng.

## Tình trạng module

| Module | Backend chính | Trạng thái trước smoke bounded |
| --- | --- | --- |
| SAM2 | Worker gọi runtime/model canonical | `partial` |
| AnimeSR | Worker gọi inference script canonical | `partial` |
| Whisper | Wrapper Faster-Whisper hiện có | `partial` hoặc `unavailable` |
| Voice | Qwen3-TTS và Seed-VC wrapper | `partial` |
| Vision/OCR | OmniParser, RF-DETR, Grounding DINO, PaddleOCR wrapper | `partial` |
| Image AI | ComfyUI API workflow FLUX/Qwen | `partial` |
| FFmpeg | Command allowlist/FFprobe | `partial`, riêng probe thành `operational` sau smoke |

Không đổi một workflow sang `operational` chỉ vì GUI cũ khởi chạy được. Cần một
smoke chức năng trực tiếp có input bounded trước khi thay đổi trạng thái.

## Giao diện

Sidebar được giữ nguyên. Mỗi workspace có form thao tác, phản hồi cạnh action,
preview file local trước upload và Jobs luôn hiển thị trạng thái/cancel/resume.
SAM2 hỗ trợ click để thêm point, hoặc kéo trên preview để điền box. Media nhận
artifact bổ sung cho concat/image sequence và tạo manifest tạm trong vùng Hub,
không có ô lệnh shell.

Giao diện hỗ trợ dark/light/system, tối thiểu 1280 × 720 và không tạo global
horizontal scroll. Shortcut chính dùng `pythonw.exe` để người dùng double-click
Hub mà không có console window.

## Không phải benchmark

Kiểm tra tích hợp phải giới hạn tối đa ba vòng: control plane/static, mỗi
module một input nhỏ nếu khả dụng, rồi cold-start regression sau khi sửa lỗi.
Không đo FPS, lặp inference, so model hay stress GPU.
