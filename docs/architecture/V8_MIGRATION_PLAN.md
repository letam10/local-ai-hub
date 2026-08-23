# Local AI Hub V8 migration plan

V8 giữ V7 làm compatibility baseline và triển khai theo wave nhỏ. Không wave nào được tự mở rộng sang model/GPU/release nếu dependency trước chưa đạt gate.

## Wave 0 — Storage & Transaction Authority

Status: **FOUNDATION REVIEWED; PRODUCTION EXTENSION ACTIVE IN WAVE 2**.

Owners: `src/platform/storage_authority.py`, `src/services/transaction_store.py`, `src/services/output_authority.py`; production extension: `src/services/v8_transaction_extensions.py`, `src/services/output_authority_v8.py`.

Gate còn lại: Windows NTFS reparse/junction/handle/TOCTOU acceptance trong `Plan_Miss.md`.

## Wave 1 — Durable Component Lifecycle

Status: **SERVICE FOUNDATION REVIEWED; EXTENDED THROUGH WAVE 3**.

Owner: `src/services/component_lifecycle_v8.py`; production Component API façade: `src/services/api/components.py`.

Giữ existing ComponentInstaller/ModelManager/RuntimeManager/receipts/verification. Opaque durable operation, explicit confirmation, finite states, no terminal re-execution; `committed` không tự nâng OPERATIONAL.

## Wave 2 — Production Callsite Migration

Status: **RE-AUDITED; PUBLICATION BOUNDARY HARDENED; WINDOWS ACCEPTANCE PENDING**.

New worker outputs dùng V7 ownership scope như pre-publication safety proof và publish duy nhất qua V8 reservation-bound Output Authority. Historical V7 reads được giữ. Full local callsite inventory và Windows adversarial QA vẫn ở `Plan_Miss.md`.

## Wave 3 — Component Enablement Control Plane

Status: **RE-AUDITED; SOURCE CORRECTNESS CLOSED; REAL LIFECYCLE PENDING**.

Import/bundle bind opaque operation; direct operation routes; tracked-catalog source acceptance; `REFERENCE_EXISTING` không biến thành download gate; route V8 Components dùng application-owned `ApiContext`; không implicit license/model auto-install promotion.

## Wave 4 — Windows Real Lifecycle & Product UX

Status: **SOURCE-SIDE PRODUCT UX RE-AUDITED; WINDOWS REAL-LIFECYCLE ACCEPTANCE PENDING**.

Source owners:
- `src/services/api/context.py`
- `src/services/api/routes/component_v8.py`
- `src/ui/features/components/index.js`
- `src/ui/features/components/render.js`
- `src/ui/features/components/v8_control_plane.js`
- `src/ui/index.html`
- `tests/test_v8_wave4_product_ux.py`

Source-side đã có bounded operation list, planned-only Confirm/Cancel, finite public component metadata, fail-closed invalid records, idempotent mount, truthful source-acceptance labels, no polling và no raw path/credential surface.

Windows Real Lifecycle vẫn cần máy thật: NTFS/reparse/TOCTOU, native picker, full artifact inventory, real component lifecycle, bundle rollback, executing cancellation, loopback API, WebView2, installer/upgrade và SQLite backup/restore.

## Wave 5 — Acceptance Gate

Status: **SOURCE PREFLIGHT RE-AUDITED/HARDENED; WINDOWS ACCEPTANCE BLOCKED**.

Owners:
- `architecture/v8_acceptance_gates.json`
- `scripts/v8_acceptance_gate.py`
- `tests/test_v8_wave5_acceptance_gate.py`
- `docs/V8_WAVE5_ACCEPTANCE_RELEASE.md`
- `Plan_Miss.md`

Wave 5 re-audit phát hiện declared `report_sha256` trước đây chưa được đối chiếu với report bytes thật. Contract mới yêu cầu mỗi gate PASS có local sibling `reports/<gate_id>.json`; preflight tự hash report, so sánh digest và xác minh report bind đúng gate/platform/source commit với finite true checks. Evidence/report paths không được emit.

Source-only CI có thể PASS trong khi `release_ready=false`. Đây là trạng thái hợp lệ nếu local Windows evidence hoặc release identity còn thiếu.

Lệnh source-only:

```powershell
python scripts/v8_acceptance_gate.py --source-only
```

Lệnh strict local acceptance:

```powershell
python scripts/v8_acceptance_gate.py --strict-release --evidence <LOCAL_EVIDENCE_JSON>
```

## Wave 6 — Release Provenance Preparation

Status: **SOURCE POLICY IMPLEMENTED; RELEASE IDENTITY/ACTIVATION NOT APPROVED**.

Owners:
- `architecture/v8_release_policy.json`
- `scripts/v8_release_provenance.py`
- `tests/test_v8_wave6_release_provenance.py`
- `docs/V8_WAVE6_RELEASE_PROVENANCE_PREPARATION.md`

Wave 6 tách V8 release policy khỏi historical V7 provenance. Policy hiện chỉ xác định generation V8, release branch `feature/local-ai-hub-v8`, SemVer major 8, tag `v<version>`/`v8.*`, immutable tags và user-controlled version/tag/main merge.

**Không có candidate version/tag được phê duyệt trong tracked policy.** `candidate_version` và `candidate_tag` vẫn `null`, approval state vẫn `required`. `src/shared/version.py` vẫn 7.1.0 và historical V7 release evidence không bị sửa.

Có thể dry-run một đề xuất mà không ghi gì:

```powershell
python scripts/v8_release_provenance.py --candidate-version 8.0.0 --candidate-tag v8.0.0
```

Ví dụ trên không phải phê duyệt `8.0.0`; nó chỉ kiểm tra policy/tag availability.

## Release gate hiện tại

Blocked until:
- 12 Windows/local acceptance gates trong `Plan_Miss.md` có PASS report thật và bind exact source commit;
- người dùng chọn/phê duyệt final V8 version + tag;
- release activation package cập nhật product version/provenance/build inputs nhất quán mà không sửa historical V7 evidence;
- strict acceptance pass trên exact release source commit;
- người dùng phê duyệt main merge/tag/release actions.

Không merge `main`, bump version, create/move tag hoặc build/publish release artifact tự động.

## Quy tắc chung

Mỗi package: plan → implementation → targeted tests → independent QA → integration → next package. Không benchmark nếu không được yêu cầu. Không model/runtime download hoặc GPU inference trong architecture/source-only wave. Dữ liệu machine-local không được commit. Mọi deferred/local item phải được theo dõi trong `Plan_Miss.md`.
