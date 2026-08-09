# Source Audit V5

Source Audit V5 chỉ đọc các đường dẫn đã được phê duyệt trên ổ D:. Nó không
sao chép, di chuyển, xóa, nén, vendor hay thay đổi bất kỳ cài đặt hiện có nào.
Báo cáo cục bộ có thể chứa đường dẫn máy nên được ghi vào Reports và bị Git bỏ
qua.

Tạo cấu hình cục bộ bằng cách sao chép Config/source_audit_v5.example.json
thành Config/source_audit_v5.local.json, rồi chỉ thêm các thư mục nguồn đã
được phép kiểm tra. Không dùng ổ D: làm một root quét rộng.

Chạy audit:

    python scripts/audit_source_v5.py

Hoặc cung cấp root cho đúng lần audit:

    python scripts/audit_source_v5.py --root D:\LocalAIHub\Adapters --root D:\LocalAIHub\Apps

Kết quả được phân loại thành FIRST_PARTY_SOURCE, UPSTREAM_CLONE,
BUILD_ARTIFACT, MODEL, ENV, CACHE hoặc USER_DATA.

- FIRST_PARTY_SOURCE chỉ là ứng viên. Cần review giấy phép, bí mật, đường dẫn
  máy và trạng thái Git; sau đó chỉ đưa phần nguồn tối thiểu đã sanitize vào
  src, tools, workflows hoặc patches trong một thay đổi được review.
- UPSTREAM_CLONE không được vendor. Ghi repository và commit trong
  dependencies.lock.json; chỉ giữ patch nội bộ đã review.
- BUILD_ARTIFACT, MODEL, ENV, CACHE và USER_DATA luôn ở ngoài Git. Audit không
  là quyền migration hoặc cleanup.

Script có giới hạn depth và số thư mục, không đi theo symlink/junction. Đường
dẫn ngoài ổ D: được ghi là skipped thay vì bị quét.
