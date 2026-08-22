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

New worker outputs dùng V7 ownership scope như pre-publication safety proof, sau đó publish duy nhất qua V8 reservation-bound Output Authority. V8 reservation tự nó không chứng minh filesystem ownership; `register_worker_outputs()` phải re-check bounded ownership và chỉ nhận `owned`. Byte/atomic publication giữ caller-facing artifact name/media metadata thay vì private producer filename. Historical V7 artifacts/uploads vẫn có read fallback.

Không tuyên bố Wave 2 production-accepted trước Windows adversarial QA, full local callsite grep và real loopback/desktop acceptance trong `Plan_Miss.md`.

## Wave 3 — Component Enablement Control Plane

Status: **RE-AUDITED; SOURCE CORRECTNESS CLOSED; REAL LIFECYCLE PENDING**.

Owners:
- `src/services/component_lifecycle_v8.py`
- `src/services/api/components.py`
- `src/services/api/routes/component_v8.py`
- `src/services/component_enablement_v8.py`

Wave 3 hoàn thành:

1. import và bundle plan bind vào opaque V8 `operation_id`, explicit confirmation, finite CAS states và terminal no-reexecution;
2. direct operation routes cho list/inspect/confirm/planned-cancel;
3. trusted-source acceptance phân loại production catalog theo disposition/source verification/auth/license/integrity/download+disk size mà không gọi provider.

Wave 4 re-audit sửa thêm hai contract:

- `REFERENCE_EXISTING` là disposition có thẩm quyền và không được biến thành download/source/license gate;
- route V8 Components phải dùng application-owned `ApiContext` binding thay vì tự tạo/call module singleton từ transport layer.

Không bật model AUTO_INSTALL_READY bằng suy diễn. `auto_install_eligible` chỉ true nếu catalog đã explicit cho auto và toàn bộ gate source/license/auth/hash/size đều đạt. Production V2 validator vẫn cố ý cấm model AUTO_INSTALL_READY ở thời điểm này.

## Wave 4 — Windows Real Lifecycle & Product UX

Status: **SOURCE-SIDE PRODUCT UX IMPLEMENTED; WINDOWS REAL-LIFECYCLE ACCEPTANCE PENDING**.

Source owners bổ sung:
- `src/services/api/context.py`
- `src/services/api/routes/component_v8.py`
- `src/ui/features/components/render.js`
- `src/ui/features/components/v8_control_plane.js`
- `src/ui/index.html`
- `tests/test_v8_wave4_product_ux.py`

Phần source-side đã làm:

- V8 operation list/inspect/confirm/cancel đi qua `ApiContext`;
- operation list dùng bounded `limit`;
- Components UI hiển thị opaque operation journal và source acceptance;
- chỉ operation `planned` có confirm/cancel UI;
- source acceptance hiển thị truthful disposition/gates, không raw path;
- không polling, không auto-download/auto-install khi mở trang;
- duplicate `snapshot-status` DOM ID cũ được loại bỏ khi mount module V8.

Phần **Windows Real Lifecycle** không thể được chứng minh từ GitHub repository và vẫn phải dùng máy Windows thật để xử lý `Plan_Miss.md`: NTFS/reparse/TOCTOU, native picker, full artifact consumer inventory, real install/reuse/verify/repair/update/rollback/uninstall, bundle failure/rollback, executing cancellation, loopback API, WebView2 interaction/accessibility, installer/shortcut/upgrade preservation và `v8_control.sqlite3` backup/restore.

Source validation đã đạt GitHub Actions run #667: `ci_validate` PASS, 27/27 V8 tests PASS, `git diff --check` PASS. Đây không thay thế Windows acceptance.

## Wave 5 — Acceptance & Release

Chỉ bắt đầu sau khi Wave 4 Windows gate được xử lý. Phạm vi: clean Windows acceptance, upgrade preservation, lifecycle matrix, crash recovery, packaging, installer, provenance và release review. Tag/version chỉ thay đổi trong package release được người dùng phê duyệt riêng.

## Quy tắc chung

Mỗi package: plan → implementation → targeted tests → independent QA → integration → next package. Không benchmark nếu không được yêu cầu. Không model/runtime download hoặc GPU inference trong architecture/source-only wave. Dữ liệu machine-local không được commit. Mọi deferred/local item phải được theo dõi trong `Plan_Miss.md`.
