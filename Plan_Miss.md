# Plan_Miss — LOCAL AI HUB V8 deferred/local work

File này là handoff bắt buộc cho Codex sau Wave 0–5 source work. Chỉ đánh dấu DONE khi có evidence phù hợp với gate: source gate có thể chứng minh bằng CI; Windows/runtime/desktop gate phải có evidence trên đúng Windows installation. Không suy diễn Linux CI thành Windows acceptance. Không tự merge `main`, không tạo/move tag, không bump version, không build/publish release và không chạy download/model/GPU workload lớn nếu chưa có phê duyệt riêng.

## 1. Source work đã hoàn thành

Wave 0: Storage Authority, SQLite transaction journal, reservation/job/transaction binding, staged-private + copy-once managed object, public commit atomic, exact-identity cleanup.

Wave 1: durable component operation journal; explicit confirmation; terminal operation không chạy lại; mất process-local plan sau restart → `plan_session_lost`; `committed` không tự nâng OPERATIONAL.

Wave 2: worker-output publication qua V8 authority với V7 ownership scope làm safety proof; historical V7 read compatibility được giữ; reservation không tự chứng minh filesystem ownership; public byte/atomic artifact giữ name/media type.

Wave 3: import/bundle có opaque durable operation; planned cancel; direct operation routes; catalog-only source acceptance; không provider request/download/license auto-accept/model auto-promote.

Wave 4 source re-audit + Product UX:
- `REFERENCE_EXISTING` không bị biến thành download gate;
- route V8 Components dùng `ApiContext` application-owned bindings;
- operation list bounded `1..500`;
- `plan_session_lost` confirm → HTTP 409;
- invalid public `component_type`/operation action bị loại fail-closed, không mặc định thành model;
- V8 control-plane mount idempotent;
- `REFERENCE_EXISTING` label = `Use Existing`; `MANUAL_INSTALL` = `Manual Install`;
- operation/source-acceptance UI không polling, không raw path.

Wave 5 source preflight:
- tracked gate contract: `architecture/v8_acceptance_gates.json`;
- read-only checker: `scripts/v8_acceptance_gate.py`;
- source tests: `tests/test_v8_wave5_acceptance_gate.py`;
- documentation: `docs/V8_WAVE5_ACCEPTANCE_RELEASE.md`;
- local evidence phải là `local_windows`, `windows-x64`, bind exact `source_commit`, finite gate states và mỗi PASS phải có report SHA-256;
- CI source-only preflight được phép PASS trong khi `release_ready=false` nếu local evidence/V8 release provenance còn thiếu.

GitHub Actions run #699 đã PASS trên source Wave 5 trước lần cập nhật handoff cuối:
- `python scripts/ci_validate.py`: PASS, 728 tracked files;
- `python -m unittest discover -s tests -p "test_v8_*.py"`: 33/33 PASS;
- `python scripts/v8_acceptance_gate.py --source-only`: PASS với blocker kỳ vọng `V8_RELEASE_PROVENANCE_NOT_REVIEWED` + `LOCAL_WINDOWS_EVIDENCE_REQUIRED`;
- JavaScript syntax gate: PASS;
- `git diff --check`: PASS.

Codex phải chạy lại toàn bộ gate ở HEAD mới nhất trước local acceptance.

## 2. LOCAL_ONLY / hiện không tồn tại trên GitHub

Phải inspect trên máy thật, không tạo giả từ example/test fixture:

- `D:\LocalAIHub\Config\` machine state: `v8_control.sqlite3` nếu đã tạo thật, legacy `artifacts.json`, job stores, receipts, component history, local settings, machine registry, recovery/forensic state.
- `D:\LocalAIHub\Models\`, `Environments\`, `runtime\`, `Output\`, `.hub-v8`, `Cache\`, `Temp\`, `Logs\`, `Reports\`, `Backups\`.
- Projects/workflows/user media/persistent data.
- Existing SAM2, AnimeSR, Faster-Whisper, FFmpeg, AIRI, ComfyUI, FLUX, Qwen và mọi runtime/model thực tế.
- External-managed roots, junction/reparse, ACL, disk/free-space/quota/file-lock state.
- pywebview/WebView2 installation, desktop binaries, shortcuts, installer/package state.
- Native picker selections, selected local paths, provider credentials, local license decisions.
- Local acceptance reports và evidence JSON cho Wave 5.

Không commit các dữ liệu này vào Git. Không overwrite/remove forensic evidence hoặc user data để test pass.

## 3. Wave 4 Windows filesystem adversarial acceptance — CHƯA LÀM

Dùng clean/controlled NTFS test DATA_ROOT:

- symlink/junction/reparse/mount-point tại DATA_ROOT, Config, Output, `.hub-v8`, object shard, producer ancestor;
- root/ancestor replacement sau `RootLease`;
- producer swap/truncate/rename/replace trong lúc copy;
- managed-object replacement giữa resolve và stream/open;
- Windows file sharing, handle identity, antivirus/indexer lock và TOCTOU validation → copy → authorize → commit;
- crash/process kill ở reserve, producer, copy, stage, authorize, public commit, cleanup, startup reconcile;
- concurrent same-name producers, multi-artifact transaction;
- cancellation trước producer/giữa producer/sau stage/trước commit;
- SQLite locked/read-only/disk-full/corrupt phải fail closed, giữ evidence;
- ambiguous/foreign/reparse files không auto-delete.

## 4. Wave 2 production callsite inventory — cần Codex local

Trên full checkout chạy `rg`/exact grep cho mọi caller của:

- `artifact_store.resolve`
- `artifact_store.describe`
- `register_worker_outputs`
- `atomic_write_job_output`
- `begin_job_output_scope`
- `prepare_job_output_scope`
- `finalize_job_output_scope`
- `stage_job_artifact`
- `_mark_staged_linked`
- `publish_staged`
- `DurableWorkEngine`, `LegacyDurableWorkEngine`, `V8DurableWorkEngine`

Với từng output-producing tool xác minh reservation trước producer, ownership proof tại publication boundary, failed/cancelled không xóa foreign/user file, public artifact chỉ sau final commit, historical V6/V7 reads vẫn hoạt động.

GitHub connector search không đủ để tuyên bố 100% callsite coverage.

## 5. Wave 3 component lifecycle local gaps — CHƯA LÀM

Native picker/selection vẫn process-local và expiry-based. Test:

- user cancel;
- expiry;
- wrong component/type;
- reparse/junction selection;
- selected file thay identity/size/hash sau selection;
- restart giữa selection→plan→confirm;
- stale selection không tự rediscover path.

Executing operation cancellation hiện chưa có durable process-owned bridge; không force-kill bằng operation ID.

Composite bundle vẫn ordered V7 child execution; chưa atomic multi-component rollback. Cần partial failure/restart/shared dependency/rollback idempotence/user-data preservation tests.

Per-component source review còn thiếu: official source, exact revision, HTTPS/provider identity, exact size/hash, license, auth, runtime/dependency graph, outage/rate-limit behavior. Không bỏ model AUTO_INSTALL guard chỉ để test pass.

## 6. Wave 4 Product UX — source DONE, WebView2 acceptance CHƯA LÀM

Source owners hiện có:

- `src/services/api/context.py`
- `src/services/api/routes/component_v8.py`
- `src/ui/features/components/index.js`
- `src/ui/features/components/render.js`
- `src/ui/features/components/v8_control_plane.js`
- `src/ui/index.html`
- `tests/test_v8_wave4_product_ux.py`

Trên desktop app thật xác minh:

- operation journal/source acceptance render đúng;
- invalid/unknown metadata không hiển thị sai thành model/runtime/action khác;
- planned mới có Confirm/Cancel;
- double-click/re-render không bypass confirmation;
- navigation nhiều lần không duplicate listener/request loop;
- Tab/Shift+Tab/Enter/Space, focus continuity, screen reader/live region;
- 5 language modes không mojibake/overflow; string chưa dịch phải ghi nhận, không fake PASS;
- HiDPI/minimum desktop size;
- degraded/error state không lộ stack/path;
- DOM/network public không có raw path/executable/credential/selection path.

## 7. Wave 5 Acceptance evidence contract — CHƯA CÓ LOCAL EVIDENCE

Tracked required gate IDs:

1. `windows_filesystem`
2. `artifact_callsite_inventory`
3. `native_picker_restart`
4. `executing_operation_cancel`
5. `bundle_atomic_rollback`
6. `real_component_lifecycle`
7. `loopback_api`
8. `webview2_product_ux`
9. `sqlite_backup_restore`
10. `packaging_upgrade`
11. `crash_recovery`
12. `runtime_smoke`

Codex local tạo **untracked** evidence JSON theo `v8-local-acceptance-evidence.v1`. Mỗi gate PASS phải tham chiếu SHA-256 của report local tương ứng. Evidence phải bind đúng HEAD cần release.

Chạy:

```powershell
python scripts/v8_acceptance_gate.py --strict-release --evidence <LOCAL_EVIDENCE_JSON>
```

Không sửa script/gate/evidence chỉ để biến BLOCKED thành PASS.

## 8. Real component lifecycle matrix — CHƯA LÀM

Trên clean/controlled Windows root, từng vertical slice:

- inspect → plan → explicit confirm → install/import/reuse → verify → receipt → bounded smoke → OPERATIONAL evidence;
- repair phải reacquire/reconstruct khi policy cho phép;
- update: candidate → stage → verify → activate → rollback;
- rollback giữ previous version/evidence;
- uninstall preserve shared dependency/project/workflow/user media;
- source outage không demote valid local install;
- auth/license gate dừng đúng chỗ;
- restart/crash recovery giữa state;
- bundle chứng minh rollback/restart semantics.

Mọi download >1 GB hoặc model/GPU inference phải dừng hỏi người dùng nếu chưa có approval riêng.

## 9. Loopback API / Desktop / packaging — CHƯA LÀM

- real `127.0.0.1:8765`, không LAN bind;
- startup/shutdown/restart/concurrency;
- V8 operation/source routes qua HTTP thật;
- artifact GET/HEAD/range cho V8 + historical V7;
- Windows open-artifact shell;
- pywebview/WebView2 close/reopen/crash/reload;
- installer/portable/managed shortcut/upgrade preservation;
- clean clone + APP_ROOT/DATA_ROOT reuse.

## 10. `v8_control.sqlite3` backup/restore — CHƯA LÀM

Không copy live SQLite DB theo cách tạo snapshot rách. Test WAL/journal mode, idle/concurrent backup, clean restore, schema mismatch, corrupt/read-only/locked, interrupted backup/restore, artifact-object↔DB identity, legacy V7 compatibility, failed validation không overwrite forensic DB.

## 11. Runtime/model/GPU — DEFERRED

Chưa chạy: FFmpeg workload, SAM2, AnimeSR, Faster-Whisper, ComfyUI, FLUX/Qwen inference, GPU/VRAM/CUDA behavior. Source Wave 0–5 không thực hiện provider/cloud request.

## 12. Release provenance V8 — CỐ Ý CHƯA SỬA

Hiện GitHub vẫn có **V7 release contract**, đây là blocker đúng thiết kế:

- `src/shared/version.py` = `7.1.0`;
- `scripts/verify_release_provenance.py` review branch `feature/v7-operational-closure`;
- intended tag `v7.1.0`/v7 series;
- `distribution/release_manifest.json` là historical V7 manifest;
- existing V7 tags không được move/rewrite.

Không đổi các file/giá trị này thành V8 cho tới khi local Windows acceptance đủ evidence **và** người dùng phê duyệt package release/version/tag riêng. Khi đó tạo V8 provenance contract mới/fresh manifest; không sửa historical V7 evidence thành giả V8.

## 13. Git branch cleanup chưa thực hiện

Ba temp branch phải verify không có unique work rồi mới delete:

- `feature/local-ai-hub-v8-wave0-temp`
- `feature/local-ai-hub-v8-wave0-temp2`
- `feature/local-ai-hub-v8-wave0-temp3`

Không force-push/rewrite history để cleanup. `PLAN.md` không được tái tạo; handoff chính là `Plan_Miss.md`.

## 14. Release gate

- PR #102 giữ OPEN + DRAFT.
- Không merge `feature/local-ai-hub-v8` → `main` tự động.
- Không bump version/create-move tag/build-publish installer từ source preflight.
- Wave 5 `release_ready` chỉ được true khi 12 local gates PASS trên exact commit và V8 provenance được review riêng.
- Người dùng quyết định cuối cùng về release/main merge.
