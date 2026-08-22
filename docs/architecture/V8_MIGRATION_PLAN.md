# Local AI Hub V8 migration plan

V8 giữ V7 làm compatibility baseline và triển khai theo wave nhỏ. Không wave nào được tự mở rộng sang model/GPU/release nếu dependency trước chưa đạt gate.

## Wave 0 — Storage & Transaction Authority

Status: **FOUNDATION REVIEWED; PRODUCTION EXTENSION ACTIVE IN WAVE 2**.

Owners: `src/platform/storage_authority.py`, `src/services/transaction_store.py`, `src/services/output_authority.py`. Wave 2 production extension: `src/services/v8_transaction_extensions.py`, `src/services/output_authority_v8.py`.

Audit Wave 2 xác nhận fixed-root/reparse/reservation/transaction/copy-once/public-commit invariants. Stale aborted staged-row reconciliation được sửa trong production extension để cleanup hội tụ. Gate còn lại: Windows NTFS reparse/junction/handle/TOCTOU acceptance trong `Plan_Miss.md`.

## Wave 1 — Durable Component Lifecycle

Status: **SERVICE FOUNDATION REVIEWED; COMPONENT API MIGRATED IN WAVE 2**.

Owner: `src/services/component_lifecycle_v8.py`; production Component API façade: `src/services/api/components.py`.

Giữ existing `ComponentInstaller`, `ModelManager`, `RuntimeManager`, receipts và verification. Install/verify/reuse/repair/update/uninstall tạo opaque durable operation trước confirmation; `committed` không tự nâng `INSTALLED_UNVERIFIED` thành `OPERATIONAL`.

Native manual import và composite bundle chưa journal hóa V8; xem `Plan_Miss.md`.

## Wave 2 — Production Callsite Migration

Status: **SOURCE MIGRATION IMPLEMENTED; WINDOWS ACCEPTANCE PENDING**.

Owners:
- `src/services/artifact_access_v8.py`
- `src/services/v8_output_bridge.py`
- `src/services/output_authority_v8.py`
- `src/services/v8_transaction_extensions.py`
- `src/services/job_manager/v8_engine.py`
- `src/services/api/components.py`

New worker outputs dùng V7 ownership scope chỉ như pre-publication safety check, sau đó publish duy nhất qua V8 reservation-bound Output Authority. Historical V7 artifacts/uploads vẫn có read fallback. Package `src.services.job_manager` export `V8DurableWorkEngine` làm production `DurableWorkEngine`, đồng thời giữ `LegacyDurableWorkEngine` cho compatibility/forensic review.

Không tuyên bố Wave 2 production-accepted trước Windows adversarial QA, full local callsite grep và real loopback/desktop acceptance trong `Plan_Miss.md`.

## Wave 3 — Real Component Enablement

Đối chiếu production catalog, source/license/auth/hash/size/runtime graph. Bật từng component theo vertical slice: runtime/dependency → model → adapter → bounded smoke → receipt/evidence → UI state. Không bật hàng loạt model cùng lúc.

## Wave 4 — API & Product UX

Hoàn thiện direct V8 operation endpoints và desktop UI cho Components/Models/Runtimes/Jobs/Artifacts/Update Center theo truthful state. Public boundary chỉ dùng opaque IDs.

## Wave 5 — Acceptance & Release

Clean Windows acceptance, upgrade preservation, reuse/import/install/update/rollback/repair/uninstall, crash recovery, packaging, installer, provenance và release review. Tag/version chỉ thay đổi trong package release được người dùng phê duyệt riêng.

## Quy tắc chung

Mỗi package: plan → implementation → targeted tests → independent QA → integration → next package. Không benchmark nếu không được yêu cầu. Không model/runtime download hoặc GPU inference trong architecture/source-only wave. Dữ liệu machine-local không được commit. Mọi deferred/local item phải được theo dõi trong `Plan_Miss.md`.
