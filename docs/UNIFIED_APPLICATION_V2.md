# Ứng dụng hợp nhất Local AI Hub V2

## Mục tiêu

Một frontend tĩnh (`src/ui`) phục vụ tại `/ui/` là nguồn giao diện duy nhất cho
cả trình duyệt lẫn desktop. `src/app/main.py` chỉ tạo cửa sổ pywebview/WebView2
và nạp URL loopback đó; nó không chứa một UI Tkinter thứ hai.

Các trang của frontend là Dashboard, AIRI, Vision Studio, SAM2, OCR, Whisper,
Voice, Image AI, Media, AnimeSR, Jobs, Models & Storage và Settings. Trang chỉ
hiển thị capability theo phản hồi API hiện thời, không tự suy diễn rằng một
backend đang hoạt động.

## Ranh giới an toàn

- API chạy tại `127.0.0.1:8765` và chỉ phục vụ asset từ `src/ui`.
- Launcher nhận một ID có trong `application_registry`; backend tự discovery và
  xác minh executable theo allowlist local/Windows, còn UI không cung cấp
  executable, thư mục làm việc, đối số hoặc command line.
- AIRI/Ollama là ứng dụng hệ thống bên ngoài. Chúng không bị di chuyển hoặc sao
  chép vào runtime của Hub.
- FLUX và Qwen Image có thể là hai mục UI, nhưng dùng cùng Local Image Studio,
  ComfyUI và kho model vật lý. Qwen3-TTS vẫn là engine voice tách biệt.
- Nếu adapter direct chưa được smoke test, UI/API báo `partial` hoặc
  `unavailable`; không tạo job giả để biểu thị inference đang chạy.

## Vận hành

Desktop bắt đầu với kích thước tối thiểu 1280 × 720 và yêu cầu maximize sau khi
native window handle được tạo. Khi API chưa chạy, desktop shell chỉ khởi động
tiến trình API loopback của chính Hub và chờ health check có giới hạn.

Frontend lưu lựa chọn giao diện sáng/tối/hệ thống trong `localStorage`. Không
có CDN, telemetry hoặc tải script từ mạng. Phần Jobs và Models & Storage lấy
dữ liệu từ API để hiển thị tiến độ, dung lượng, legacy junction và kết quả
quét read-only.

## Smoke test tối thiểu

1. Khởi động API, kiểm tra `/health`, `/ui/`, `/api/dashboard`, `/api/storage`
   và `/api/applications`.
2. Mở desktop shell để xác minh WebView2 cùng URL `/ui/`.
3. Với ứng dụng đã cài, chỉ chạy kiểm tra khởi động hoặc self-test có giới hạn;
   AIRI phải vượt qua identity/fingerprint verification trước khi launch, và
   không chạy benchmark hay inference nặng chỉ để kiểm tra UI.
4. Đóng riêng cây tiến trình được tạo bởi smoke test. Không dừng tiến trình
   người dùng đã tồn tại.

Chi tiết migration, manifest và rollback nằm trong
`docs/MIGRATION_AND_ROLLBACK.md`.
