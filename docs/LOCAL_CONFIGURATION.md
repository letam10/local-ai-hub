# Cấu hình cục bộ

`Config/components.json`, `Config/model_registry.json`, `Config/hub_config.json`
và `Config/local.json` là tệp theo máy và bị Git ignore. Chỉ commit các mẫu
`*.example.json` tương ứng. Nhóm mẫu V2 còn có `Config/app.example.json` và
`Config/models.example.json`.

Với launcher Windows, sao chép `Config/local.env.example.cmd` thành
`Config/local.env.cmd` (bị ignore) rồi chỉ điền các path local cần thiết. Không
đặt token, mật khẩu hay API key vào một trong hai tệp. `Config/` là cách viết
chuẩn trên Windows; không tạo thêm thư mục config viết thường song song.

## Image & Mask Studio M6A

`limits.max_state_bytes` mặc định là 8 MiB (giới hạn cấu hình 64 MiB). Đây là
trần byte của autosave state, không phải giới hạn media. Hub kiểm tra regular
file/identity/size/mtime, đọc tối đa trần + 1 byte và không ghi đè state nếu
đọc hoặc parse thất bại. JSON inbound/persisted và metadata chỉ nhận số hữu hạn.

M6A tách policy khỏi state runtime để policy không thể ghi đè bản nháp:

| Tệp | Mục đích | Git |
| --- | --- | --- |
| `Config/image_mask_studio.example.json` | Mẫu `image-mask-studio-config.v1` có limits (gồm preset) và `sam2_assist.configured` | Track |
| `Config/image_mask_studio.json` | Policy máy cục bộ tùy chọn; có thể copy từ example để chỉnh giới hạn bounded | Ignore |
| `Config/image_mask_studio_state.json` | Session/autosave/snapshot/preset runtime `image-mask-studio-state.v1`; do Hub quản lý | Ignore |

`image_mask_studio.json` không chứa path model, backend URL, lệnh, tên model,
pixel, prompt cá nhân hay secret. Chỉ JSON boolean thật `true` mới cho phép UI
hiển thị SAM2 là `partial`; chuỗi như `"false"` vẫn bị coi là chưa cấu hình và
không chứng minh hoặc ép backend thành
`operational`. `image_mask_studio_state.json` không phải file cấu hình để copy
hoặc commit; không sửa tay khi Hub đang chạy. Nếu state hỏng, Hub sẽ báo
`recovery_required` hoặc `recovered_partial` chỉ đọc và không tự ghi đè tệp đó.

Dùng biến môi trường hoặc cấu hình local cho path các cài đặt ngoài, ví dụ
`SAM2_HOME`, `ANIMESR_HOME`, `WHISPER_HOME`, `FFMPEG_PATH`, `FFPROBE_PATH`
và `LOCAL_AI_HOME`. `AIRI_EXECUTABLE` chỉ còn là trường tương thích trong các
mẫu cũ; riêng nút M2 không tin một biến môi trường đơn lẻ mà yêu cầu registry
allowlist cùng identity/fingerprint verification. Không bao giờ đặt secret
hoặc absolute personal path trong tệp được track.

## AIRI launcher registry

`Config/application_registry.local.json` là cấu hình máy cục bộ tùy chọn, bị
Git bỏ qua. Tracked `application_registry.example.json` chỉ là mẫu; không dùng
mẫu đó làm bằng chứng AIRI đã cài.

Entry AIRI có thể khai báo `executable`, `working_directory`, `arguments` và
identity manifest `{"product_name":"AIRI","publisher":"Moeru AI"}`. Các
giá trị này chỉ là allowlist đầu vào cho backend; trước khi hiển thị `verified`
hoặc khởi chạy, backend vẫn phải kiểm tra target `.exe` regular, reparse-safe,
metadata ProductName/CompanyName và fingerprint. Không đặt command shell,
secret/API key hoặc dữ liệu xác thực AIRI vào registry.
