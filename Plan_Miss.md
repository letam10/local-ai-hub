# Plan_Miss — LOCAL AI HUB V8 deferred/local work

File này là handoff bắt buộc cho Codex sau Wave 0–3. Chỉ đánh dấu DONE khi có evidence phù hợp với loại gate: source gate có thể chứng minh bằng CI; Windows/runtime gate phải có evidence trên đúng Windows installation. Không suy diễn Linux CI thành Windows acceptance. Không tự merge `main`, không tạo/move tag, không download model/runtime lớn hoặc chạy GPU inference nếu chưa có phê duyệt riêng.

## 1. Tổng hợp kiểm tra đã thực hiện

Wave 0: Storage Authority, SQLite transaction journal, reservation/job/transaction binding, staged-private + copy-once managed object, public commit atomic, exact-identity cleanup; Wave 2 sửa stale aborted staged-row reconciliation để cleanup hội tụ.

Wave 1: durable component operation journal; confirmation bắt buộc; terminal operation không chạy lại; restart mất process-local V7 plan → `plan_session_lost`; committed không tự nâng OPERATIONAL.

Wave 2: new worker-output publication chuyển sang V8, historical V7 reads được giữ. Re-audit Wave 3 đã khóa thêm hai lỗi source-level:
- V8 reservation **không** còn đủ để publish worker filesystem candidate; publication boundary tự re-check V7 scope và chỉ nhận candidate batch có ownership `owned`.
- V8 byte/atomic output giữ public artifact `name` và explicit `media_type`; private producer filename không được rò ra product metadata.

Wave 3 source/control-plane đã làm:
- manual-import plan có opaque durable V8 operation;
- composite-bundle plan có opaque durable V8 operation;
- planned operation có durable cancel; executing cancellation chưa giả lập;
- direct operation list/inspect/confirm/cancel routes;
- trusted-source acceptance classifier dựa trên tracked catalog disposition/source/auth/license/integrity/size;
- không gọi provider, không download, không implicit accept license, không tự bật model AUTO_INSTALL_READY.

Latest source gate đã chạy trên GitHub Actions run #647:
- `python scripts/ci_validate.py`: PASS, 721 tracked files, không forbidden artifact/unmasked secret;
- `python -m unittest discover -s tests -p "test_v8_*.py"`: 23/23 PASS;
- `git diff --check` từ PR base: PASS.

Mỗi lần Codex tiếp tục phải chạy lại ba gate trên, sau đó chạy full relevant tests từ local checkout nếu khả dụng.

## 2. LOCAL_ONLY / không có trên GitHub

Phải inspect trên máy thật, tuyệt đối không tạo giả từ example config:
- `D:\LocalAIHub\Config\` machine state, gồm `v8_control.sqlite3` khi V8 được chạy thật, legacy `artifacts.json`, job stores, receipts, component history và các local/recovery files.
- `D:\LocalAIHub\Models\`, `Environments\`, `runtime\`, `Output\`, `Cache\`, `Temp\`, `Logs\`, `Reports\`, `Backups\`, project/workflow persistent state và user media.
- Existing SAM2/AnimeSR/Whisper/FFmpeg/AIRI/model/runtime installations và external-managed paths.
- Desktop-installed binaries, shortcuts, packaging state, Windows services/process ownership, actual disk/free-space conditions.
- Any local native-picker selection, provider credential, local license acceptance record hoặc user-selected import path.

Không commit các dữ liệu trên vào Git.

## 3. Windows adversarial acceptance bắt buộc

Chạy bounded synthetic/local tests trên NTFS:
- symlink/junction/reparse/mount-point ở DATA_ROOT, Config, Output, `.hub-v8`, object shard và producer ancestor;
- replace root/ancestor sau khi `RootLease` được cấp;
- producer swap/truncate/rename trong lúc copy; managed-object replacement trong lúc resolve/stream;
- Windows file sharing/locking/handle identity và TOCTOU giữa validation → copy → authorize → commit;
- crash/process kill ở reserve, copy, stage, authorize, final commit, cleanup và startup reconcile;
- xác minh final public commit không cần mandatory metadata write khác để artifact tồn tại hợp lệ;
- concurrent same-name producers, multi-artifact/job và explicit public name/media metadata;
- cancellation trước producer, giữa producer, sau stage, trước/đúng lúc public commit;
- API byte-range/HEAD streaming từ V8 object và historical V7 artifact, không lộ raw path;
- SQLite corruption/read-only/disk-full/locked-file recovery phải fail closed và không overwrite evidence.

## 4. Wave 2 follow-up cần local/Codex

- Chạy full consumer grep trên local checkout cho mọi direct use của `artifact_store.resolve`, `describe`, `register_worker_outputs`, `atomic_write_job_output`, `stage_job_artifact`, `_mark_staged_linked`, `publish_staged`; GitHub connector search không chứng minh được 100% callsite coverage.
- Xác minh direct `HubJobManager` thật tạo V8 reservation trước producer cho tất cả output-producing tools; tool tạo output nhưng không thuộc output-scope/`requires_published_artifact` phải được phân loại và sửa fail-closed.
- Xác minh `V8DurableWorkEngine` với bounded fake/local adapter trong production composition: reservation trước adapter, managed producer, byte output metadata, restart/cancel/retry/artifact lineage.
- Legacy managed V6 artifact CAS chưa destructive-migrate; historical links phải còn đọc được. Thiết kế migration/backfill riêng nếu sau này bỏ JSON store.
- Chỉ fold `V8ProductionTransactionStore`/`ProductionOutputAuthority` extensions vào core Wave 0 sau Windows acceptance; không xóa compatibility layer trước evidence.
- Thiết kế backup/restore/migration nhất quán cho `Config/v8_control.sqlite3`; không copy SQLite DB đang transaction theo cách tạo snapshot rách.

## 5. Wave 3 — phần source đã làm, phần còn thiếu

ĐÃ LÀM trên GitHub/source:
- V8 journal cho `import` và `bundle` action.
- `plan_fingerprint` được dùng làm durable binding khi legacy plan không có `expected_state_fingerprint` riêng.
- direct routes: `GET /api/components/operations`, `GET /api/components/operations/{operation_id}`, `POST .../confirm`, `POST .../cancel`.
- source acceptance routes: `GET /api/components/source-acceptance`, `GET /api/components/{component_id}/source-acceptance`.
- finite source acceptance không trả raw local path và không gọi network.
- tracked catalog test chứng minh không model nào bị auto-promote; SAM2 có verified source/hash/size nhưng vẫn manual do disposition; FLUX vẫn AUTH_REQUIRED.

CÒN THIẾU / cần Codex local:
- Native picker selection/path vẫn process-local và expiry-based; operation journal không làm selection restart-resumable. Sau restart phải fail closed, không tự tìm lại path.
- Windows native picker acceptance: expiry, wrong component, reparse selection, file changed after selection, restart, user cancel.
- Executing component operation cancellation bridge: hiện chỉ `planned → cancelled`; không force-cancel executor đang chạy nếu không có process-owned cancellation contract.
- Composite bundle vẫn dùng ordered V7 child execution; chưa có atomic multi-component transaction/rollback. Cần test partial child failure, shared dependency preservation, rollback và restart.
- Direct operation API đã có nhưng Desktop/WebView2 UI cho operation ID/state/confirm/cancel chưa có.
- Trusted-source acceptance chỉ đọc catalog. Chưa có live upstream discovery/probe, provider auth flow, license acceptance record hoặc official-source revalidation.
- Phải review từng component trước real enablement: canonical source, revision, size, digest, license, auth, runtime/dependency graph. Không bật hàng loạt.
- Production catalog validator hiện chủ động cấm model AUTO_INSTALL_READY; không bỏ guard này chỉ để test pass. Chỉ thay đổi bằng package riêng sau per-component evidence.
- `committed` operation vẫn không được coi là OPERATIONAL nếu thiếu bounded runtime evidence.

## 6. Real component lifecycle chưa chạy

Trên clean/controlled Windows test root cần chạy từng vertical slice:
- inspect → plan → confirm → install/import/reuse → verify → receipt → bounded smoke → OPERATIONAL evidence;
- repair phải reacquire/reconstruct đúng artifact khi được phép, không chỉ revalidate;
- update phải có candidate/stage/verify/activate/rollback và không auto-update;
- uninstall phải preserve shared dependency và user/project data;
- bundle phải chứng minh rollback/restart semantics;
- source outage không được demote một local installation hợp lệ;
- license/auth-required component phải dừng đúng gate.

Mọi download lớn >1GB hoặc GPU/model inference phải dừng hỏi người dùng trước nếu chưa có phê duyệt riêng.

## 7. API/Desktop/runtime acceptance chưa chạy

- Loopback API `127.0.0.1:8765` real launch, shutdown, concurrent request, operation routes, artifact streaming và restart acceptance.
- Desktop WebView2 composition/UI wiring cho V8 operation IDs, source acceptance và output objects.
- Windows open-artifact shell behavior.
- Installer/shortcut/upgrade preservation and clean-clone acceptance.
- FFmpeg/SAM2/AnimeSR/Whisper/ComfyUI/FLUX/Qwen real workload, GPU/VRAM, CUDA/driver interaction: DEFERRED.
- No provider/cloud request executed trong Wave 0–3 source work.

## 8. Git/connector cleanup chưa làm được

Ba branch tạm phải **verify không có unique work rồi mới delete**:
- `feature/local-ai-hub-v8-wave0-temp`
- `feature/local-ai-hub-v8-wave0-temp2`
- `feature/local-ai-hub-v8-wave0-temp3`

GitHub connector hiện không expose delete-ref. Không force-push/rewrite history để thay thế. Codex local có thể dùng `git push origin --delete <branch>` sau verification và explicit cleanup scope.

`PLAN.md` không được tái tạo; handoff chính là `Plan_Miss.md`.

## 9. Release còn thiếu

- Giữ PR #102 OPEN + DRAFT trong Wave 0–3.
- Không merge V8 vào `main` tự động.
- Không tạo V8 tag/version/release.
- Sau Windows real-lifecycle acceptance phải cập nhật PR evidence, đóng/resolved các mục `Plan_Miss.md`, rồi người dùng quyết định release/main merge.
