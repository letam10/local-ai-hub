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

## V3: legacy cleanup có kiểm chứng

V3 bổ sung inventory riêng cho những đường dẫn AI legacy đã biết. Inventory là
read-only, không đi sâu vào media cá nhân không thuộc candidate và không tính
target của junction như một bản sao dữ liệu.

```powershell
python .\scripts\inventory_legacy_v3.py
```

Kết quả cục bộ bị Git bỏ qua gồm `Reports\LEGACY_CLEANUP_V3.local.md`,
`Reports\V3_INTEGRATION_AND_CLEANUP.local.md` và
`Config\legacy_cleanup_v3.local.json`. Mỗi record ghi rõ loại
`REAL_DIRECTORY`/`JUNCTION`/`SYMLINK`, bytes local, target, tham chiếu từ Hub
registry/config/shortcut/runtime text, dấu hiệu user data/model và tiến trình
đang dùng path.

Sau cleanup và validation, dùng `scripts\write_v3_final_report.py` để ghi
`V3_INTEGRATION_AND_CLEANUP.local.md`. Script này chỉ đọc state, giới hạn tối đa
ba kết quả vòng test, và kiểm tra path đã vắng mặt trước khi đưa vào danh sách
đã xóa; nó không thực hiện cleanup.

Script cleanup mặc định chỉ dry-run:

```powershell
# Xem chính xác junction hoặc thư mục rỗng nào đủ điều kiện.
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveObsoleteJunctions
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveVerifiedEmptyFolders

# Chỉ sau khi review report và dry-run.
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveObsoleteJunctions -Apply -ApprovedJunctionPath 'D:\exact\reviewed-junction'
pwsh -File .\scripts\cleanup_legacy_v3.ps1 -RemoveVerifiedEmptyFolders -Apply
```

`-Apply` không recurse qua target junction. Nó chỉ xóa reparse point dưới các
legacy root đã allowlist khi reference count bằng 0, hoặc một directory thật
đã xác minh rỗng. Nó từ chối user data, active process, folder không rỗng,
unknown path và mọi mục ngoài root được review. Junction còn cần một
`-ApprovedJunctionPath` chính xác để tránh cleanup hàng loạt.

## V3: rebuild environment Python ngoài Hub

Không di chuyển venv bằng `Move-Item`. V3 export state package, dựng
replacement tại `Environments\sam2` hoặc `Environments\animesr`, cài runtime
canonical editable, rồi kiểm tra import trước khi registry dùng replacement:

```powershell
# Chỉ in source/destination, dung lượng, free space và blocker; không thay đổi gì.
pwsh -File .\scripts\migrate_python_environment_v3.ps1 -Component sam2

# Rebuild chỉ khi không có process dùng environment legacy.
pwsh -File .\scripts\migrate_python_environment_v3.ps1 -Component sam2 -Apply
pwsh -File .\scripts\migrate_python_environment_v3.ps1 -Component sam2 -Verify
python .\scripts\refresh_managed_registry.py
```

Sau đó chạy đúng một smoke trực tiếp bounded bằng environment replacement. Chỉ
khi evidence đó đã được ghi, script mới cho phép dry-run/xóa legacy venv:

```powershell
pwsh -File .\scripts\migrate_python_environment_v3.ps1 `
  -Component sam2 -RecordFunctionalSmoke -SmokeEvidence "mô tả ngắn smoke đã pass"
pwsh -File .\scripts\migrate_python_environment_v3.ps1 -Component sam2 -RemoveLegacy
```

Thêm `-Apply` ở lệnh cuối chỉ sau khi replacement, adapter và smoke đều đạt.
Nếu process người dùng còn dùng venv legacy, nếu free disk dự kiến thấp, hoặc
nếu rebuild/install thất bại, script ghi blocker local và giữ nguyên source.
Rollback trong giai đoạn này là giữ legacy environment và đổi registry về
fallback cũ; không có xóa tự động nào xảy ra khi rebuild thất bại.
