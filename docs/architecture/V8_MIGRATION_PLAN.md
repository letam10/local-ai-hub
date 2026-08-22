# Local AI Hub V8 migration plan

V8 giữ V7 làm compatibility baseline và triển khai theo wave nhỏ. Không wave nào được tự mở rộng sang model/GPU/release nếu dependency trước chưa đạt gate.

## Wave 0 — Storage & Transaction Authority

Status: **FOUNDATION REVIEWED; PRODUCTION EXTENSION ACTIVE IN WAVE 2**.

Owners: `src/platform/storage_authority.py`, `src/services/transaction_store.py`, `src/services/output_authority.py`. Wave 2 production extension: `src/services/v8_transaction_extensions.py`, `src/services/output_authority_v8.py`.

Audit Wave 2 xác nhận fixed-root/reparse/reservation/transaction/copy-once/public-commit invariants. Stale aborted staged-row reconciliation được sửa trong production extension để cleanup hội tụ. Gate còn lại: Windows NTFS reparse/junction/handle/TOCTOU acceptance trong `Plan_Miss.md`.

## Wave 1 — Durable Component Lifecycle

Status: **SERVICE FOUNDATION REVIEWED; EXTENDED THROUGH WAVE 3**.

Owner: `src/services/component_lifecycle_v8.py`; production Component API façade: `src/services/api/components.py`.

Giữ existing `ComponentInstaller`, `ModelManager`, `RuntimeManager`, receipts và verification. Install/verify/reuse/repair/update/uninstall tạo opaque durable operation trước confirmation; Wave 3 journal hóa thêm manual-import plan và composite-bundle plan. `committed` không tự nâng `INSTALLED_UNVERIFIED` thành `OPERATIONAL`.

Native selected-path capability vẫn process-local; bundle chưa có atomic multi-component rollback. Các giới hạn này được theo dõi trong `Plan_Miss.md`.

## Wave 2 — Production Callsite Migration

Status: **RE-AUDITED; PUBLICATION BOUNDARY HARDENED; WINDOWS ACCEPTANCE PENDING**.

Owners:
- `src/services/artifact_access_v8.py`
- `src/services/v8_output_bridge.py`
- `src/services/output_authority_v8.py`
- `src/services/v8_transaction_extensions.py`
- `src/services/job_manager/v8_engine.py`
- `src/services/api/components.py`

New worker outputs dùng V7 ownership scope như pre-publication safety proof, sau đó publish duy nhất qua V8 reservation-bound Output Authority. Wave 3 re-audit khóa thêm invariant: V8 reservation tự nó không chứng minh filesystem ownership; `register_worker_outputs()` phải re-check bounded ownership và chỉ nhận `owned`. Byte/atomic publication giữ caller-facing artifact name/media metadata thay vì private producer filename. Historical V7 artifacts/uploads vẫn có read fallback. Package `src.services.job_manager` export `V8DurableWorkEngine` làm production `DurableWorkEngine`, đồng thời giữ `LegacyDurableWorkEngine` cho compatibility/forensic review.

Không tuyên bố Wave 2 production-accepted trước Windows adversarial QA, full local callsite grep và real loopback/desktop acceptance trong `Plan_Miss.md`.

## Wave 3 — Component Enablement Control Plane

Status: **SOURCE/CONTROL-PLANE IMPLEMENTED; REAL LIFECYCLE PENDING**.

Owners:
- `src/services/component_lifecycle_v8.py`
- `src/services/api/components.py`
- `src/services/api/routes/component_v8.py`
- `src/services/component_enablement_v8.py`

Wave 3 hoàn thành ba phần source-level:

1. import và bundle plan được bind vào opaque V8 `operation_id`, explicit confirmation, finite CAS states và terminal no-reexecution;
2. direct operation routes cho list/inspect/confirm/planned-cancel;
3. trusted-source acceptance phân loại production catalog theo disposition/source verification/auth/license/integrity/download+disk size mà không gọi provider.

Không bật model AUTO_INSTALL_READY bằng suy diễn. `auto_install_eligible` chỉ true nếu catalog đã explicit cho auto và toàn bộ gate source/license/auth/hash/size đều đạt. Production V2 validator vẫn cố ý cấm model AUTO_INSTALL_READY ở thời điểm này.

GitHub Actions source gate sau Wave 3: `ci_validate` PASS, 23/23 V8 tests PASS, `git diff --check` PASS. Đây không thay thế Windows acceptance.

## Wave 4 — Windows Real Lifecycle & Product UX

Wave kế tiếp phải dùng máy Windows thật để xử lý `Plan_Miss.md`: NTFS/reparse/TOCTOU, native picker, full artifact consumer inventory, real component install/reuse/verify/repair/update/rollback/uninstall, bundle failure/rollback, loopback API, WebView2/Desktop operation UI và `v8_control.sqlite3` backup/restore. Mọi real download lớn hoặc GPU inference vẫn cần scope/phê duyệt riêng.

## Wave 5 — Acceptance & Release

Clean Windows acceptance, upgrade preservation, reuse/import/install/update/rollback/repair/uninstall, crash recovery, packaging, installer, provenance và release review. Tag/version chỉ thay đổi trong package release được người dùng phê duyệt riêng.

## Quy tắc chung

Mỗi package: plan → implementation → targeted tests → independent QA → integration → next package. Không benchmark nếu không được yêu cầu. Không model/runtime download hoặc GPU inference trong architecture/source-only wave. Dữ liệu machine-local không được commit. Mọi deferred/local item phải được theo dõi trong `Plan_Miss.md`.
