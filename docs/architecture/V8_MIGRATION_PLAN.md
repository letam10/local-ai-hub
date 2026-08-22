# Local AI Hub V8 migration plan

V8 giữ V7 làm compatibility baseline và triển khai theo wave nhỏ. Không wave nào được tự mở rộng sang model/GPU/release nếu dependency trước chưa đạt gate.

## Wave 0 — Storage & Transaction Authority

Status: **FOUNDATION IMPLEMENTED** trên `feature/local-ai-hub-v8`.

Owners: `src/platform/storage_authority.py`, `src/services/transaction_store.py`, `src/services/output_authority.py`.

Gate production còn lại: Windows reparse/junction/handle-race acceptance và migration callsite V7 Job/Artifact.

## Wave 1 — Durable Component Lifecycle

Status: **SERVICE FOUNDATION IMPLEMENTED**.

Owner: `src/services/component_lifecycle_v8.py`.

Giữ existing `ComponentInstaller`, `ModelManager`, `RuntimeManager`, receipts và verification. Thêm opaque durable component operation journal; không tự nâng `INSTALLED_UNVERIFIED` thành `OPERATIONAL`.

Gate production còn lại: API/Desktop composition, native import journal và clean-machine lifecycle acceptance.

## Wave 2 — Production Callsite Migration

Migrate Job Manager, DurableWorkEngine, Artifact Store/public streaming và component API sang authority V8. Không giữ hai publication authority cùng hoạt động cho cùng một job. Windows adversarial QA là gate bắt buộc.

## Wave 3 — Real Component Enablement

Đối chiếu production catalog, source/license/auth/hash/size/runtime graph. Bật từng component theo vertical slice: runtime/dependency → model → adapter → bounded smoke → receipt/evidence → UI state. Không bật hàng loạt model cùng lúc.

## Wave 4 — API & Product UX

Wire V8 component operations/output authority vào loopback API và desktop UI. Public boundary chỉ dùng opaque IDs. Hoàn thiện Components/Models/Runtimes/Jobs/Artifacts/Update Center theo truthful state.

## Wave 5 — Acceptance & Release

Clean Windows acceptance, upgrade preservation, reuse/import/install/update/rollback/repair/uninstall, crash recovery, packaging, installer, provenance và release review. Tag/version chỉ thay đổi trong package release được người dùng phê duyệt riêng.

## Quy tắc chung

Mỗi package: plan → implementation → targeted tests → independent QA → integration → next package. Không benchmark nếu không được yêu cầu. Không model/runtime download hoặc GPU inference trong architecture/source-only wave. Dữ liệu machine-local không được commit.
