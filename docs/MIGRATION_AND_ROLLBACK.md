# Migration và rollback an toàn

## Điều kiện trước khi di chuyển

Chạy `scripts/migrate_layout_v2.ps1` chỉ sau khi đã xem manifest cục bộ bị Git
bỏ qua. Script mặc định là `-DryRun`; `-Apply` chỉ xử lý entry được đánh dấu
`planned` hoặc `needs_review` nhưng chỉ tự di chuyển classification portable có
`confidence: high`.

Trước một physical move, manifest cần có:

- component, nguồn và đích rõ ràng;
- dung lượng quan sát, tiến trình đang tham chiếu và ước tính free space;
- cùng volume để dùng atomic `Move-Item`, hoặc trạng thái bị chặn;
- chiến lược rollback và yêu cầu legacy junction;
- smoke test có giới hạn sẽ thực hiện sau move.

Script từ chối di chuyển cross-volume, destination đã tồn tại, source đang có
tiến trình tham chiếu hoặc projected free space dưới 4 GiB. Nó không tự dừng
tiến trình của người dùng, không sao chép model, không di chuyển venv mù quáng
và không xoá legacy directory.

## Quy trình chuẩn

```powershell
# Chỉ quét inventory/report local; không move hay download.
python scripts/refresh_final_inventory.py

# Xem kế hoạch không thay đổi hệ thống.
pwsh -File scripts/migrate_layout_v2.ps1

# Áp dụng các entry đã review.
pwsh -File scripts/migrate_layout_v2.ps1 -Apply

# Sau khi từng runtime có smoke test đạt, ghi nhận verify.
pwsh -File scripts/migrate_layout_v2.ps1 -MarkVerified

# Tạo lại report local sau cùng.
python scripts/refresh_final_inventory.py
```

Sau `-Apply`, đường dẫn cũ chỉ nhận junction khi destination tồn tại. Smoke test
phải dùng executable/engine ở destination canonical và kiểm tra junction cũ
trỏ đúng vào nó. Chỉ sau đó mới dùng `-MarkVerified`.

## Rollback

Rollback là thao tác vật lý có chủ ý, chỉ dùng khi có nhu cầu khôi phục đã được
review. Script chỉ rollback entry `verified` hoặc `moved_pending_verification`,
từ chối ghi đè dữ liệu thật ở legacy source và từ chối thao tác khi có tiến trình
tham chiếu destination.

```powershell
# Wrapper tương đương với migrate_layout_v2.ps1 -Rollback
pwsh -File scripts/rollback_layout_v2.ps1
```

Với entry junction-only, rollback chỉ xóa junction sau khi kiểm tra nó thật sự
là reparse point. Với entry đã move, rollback xóa junction cũ rồi reverse move
trong cùng volume. Sau rollback phải làm smoke test lại và tái tạo report.

## Legacy cleanup

Không có cleanup tự động. `Reports/LEGACY_AI_PATHS.local.md` phân loại đường
dẫn thành junction, external managed, user data, cache/unknown hoặc còn cần
review. Chỉ người dùng mới có thể cho phép cleanup sau khi tất cả launcher và
backend liên quan đã có bounded smoke test; không xóa USER_DATA,
SYSTEM_MANAGED, UNKNOWN hoặc bất kỳ path nào còn được tham chiếu.
