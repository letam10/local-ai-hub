# Plan_Miss — LOCAL AI HUB V8 deferred/local work

File này là handoff bắt buộc cho Codex sau Wave 0–2. Chỉ đánh dấu DONE khi có evidence trên đúng Windows installation; không suy diễn từ CI Linux. Không tự merge `main`, không tạo/move tag, không download model/runtime lớn hoặc chạy GPU inference nếu chưa có phê duyệt riêng.

## 1. Tổng hợp kiểm tra đã thực hiện

Wave 0 lần trước: Storage Authority, SQLite transaction journal, reservation/job/transaction binding, staged-private + copy-once managed object, public commit atomic, exact-identity cleanup; synthetic symlink/foreign reservation/mutable producer/object replacement tests.

Wave 1 lần trước: durable component operation journal; confirmation bắt buộc; terminal operation không chạy lại; restart mất V7 in-memory plan → `plan_session_lost`; committed không tự nâng OPERATIONAL.

Kiểm tra hiện tại: đọc lại Wave 0/1 và production V7 callsites (`artifact_store`, direct `HubJobManager`, durable engine, Component API, artifact streaming). Phát hiện stale aborted staged-row reconciliation và xử lý trong Wave 2 production extension. Wave 2 source migration chuyển new worker-output publication sang V8, giữ historical V7 reads, thêm V8 durable-engine production alias và Component API lifecycle mapping.

Mỗi lần tiếp tục phải chạy lại: `python scripts/ci_validate.py`; `python -m unittest discover -s tests -p "test_v8_*.py"`; `git diff --check` từ PR base; sau đó full relevant tests nếu local checkout cho phép.

## 2. LOCAL_ONLY / không có trên GitHub

Phải inspect trên máy thật, tuyệt đối không tạo giả từ example config:
- `D:\LocalAIHub\Config\` machine state, gồm `v8_control.sqlite3` khi V8 được chạy thật, legacy `artifacts.json`, job stores, receipts, component history và các local/recovery files.
- `D:\LocalAIHub\Models\`, `Environments\`, `runtime\`, `Output\`, `Cache\`, `Temp\`, `Logs\`, `Reports\`, `Backups\`, project/workflow persistent state và user media.
- Existing SAM2/AnimeSR/Whisper/FFmpeg/AIRI/model/runtime installations và external-managed paths.
- Desktop-installed binaries, shortcuts, packaging state, Windows services/process ownership, actual disk/free-space conditions.
Không commit các dữ liệu trên vào Git.

## 3. Windows adversarial acceptance bắt buộc

Chạy bounded synthetic/local tests trên NTFS:
- symlink/junction/reparse/mount-point ở DATA_ROOT, Config, Output, `.hub-v8`, object shard và producer ancestor;
- replace root/ancestor sau khi `RootLease` được cấp;
- producer swap/truncate/rename trong lúc copy; managed-object replacement trong lúc resolve/stream;
- Windows file sharing/locking/handle identity và TOCTOU giữa validation → copy → authorize → commit;
- crash/process kill ở các mốc reserve, copy, stage, authorize, final commit, cleanup và startup reconcile;
- xác minh final public commit không cần một mandatory metadata write khác để artifact tồn tại hợp lệ;
- concurrent same-name producers và nhiều artifacts/job;
- cancellation trước producer, giữa producer, sau stage, trước/đúng lúc public commit;
- API byte-range/HEAD streaming từ V8 object và historical V7 artifact, không lộ raw path;
- SQLite corruption/read-only/disk-full/locked-file recovery phải fail closed và không overwrite evidence.

## 4. Wave 2 follow-up cần local/Codex

- Chạy full consumer grep trên local checkout cho mọi direct use của `artifact_store.resolve`, `describe`, `register_worker_outputs`, `atomic_write_job_output`, `stage_job_artifact`, `publish_staged`; GitHub connector search không trả kết quả đáng tin cho một số symbol nên chưa thể chứng minh 100% callsite coverage.
- Xác minh direct `HubJobManager` thật tạo V8 reservation trước producer cho tất cả output-producing tools; tool nào tạo output nhưng không thuộc output-scope/`requires_published_artifact` phải được phân loại và sửa fail-closed.
- Xác minh `V8DurableWorkEngine` với một bounded fake/local adapter thật: reservation trước adapter, persist managed producer, restart/cancel, retry, artifact lineage.
- Legacy managed V6 artifact CAS (`stage_job_artifact/_mark_staged_linked/publish_staged`) chưa bị destructive-migrate; historical links phải còn đọc được. Lập migration/backfill riêng nếu muốn bỏ JSON store.
- Fold `V8ProductionTransactionStore`/`ProductionOutputAuthority` extensions vào core Wave 0 chỉ sau Windows acceptance; không xóa compatibility layer trước khi evidence đạt.
- Thiết kế backup/restore/migration cho `Config/v8_control.sqlite3`; backup không được copy DB đang transaction theo cách tạo snapshot không nhất quán.

## 5. Component lifecycle còn thiếu

- Native picker/manual import hiện vẫn là V7 process-local `selection_id`; cần durable selection/import operation, expiry/restart contract và Windows picker acceptance.
- Composite component bundle vẫn dùng V7 bundle service; cần một durable V8 bundle operation, dependency transaction/rollback semantics.
- Direct `/api/.../operations/{operation_id}` lookup/confirm/cancel routes và Desktop UI state chưa làm; Wave 2 chỉ giữ plan-id compatibility.
- Real clean-machine lifecycle chưa chạy: install, reuse, verify, repair reacquire, update, rollback, uninstall, shared dependency preservation.
- Upstream/source/license/auth/hash/size discovery và real AUTO_INSTALL_READY enablement thuộc Wave 3, không được suy ra từ source tests.
- `committed` operation vẫn không được coi là OPERATIONAL nếu thiếu bounded runtime evidence.

## 6. API/Desktop/runtime acceptance chưa chạy

- Loopback API `127.0.0.1:8765` real launch, shutdown, concurrent request and restart acceptance.
- Desktop WebView2 composition/UI wiring cho V8 operation IDs/output objects.
- Windows open-artifact shell behavior.
- Installer/shortcut/upgrade preservation and clean-clone acceptance.
- FFmpeg/SAM2/AnimeSR/Whisper/ComfyUI/FLUX/Qwen real workload, GPU/VRAM, CUDA/driver interaction: DEFERRED; không chạy trong Wave 0–2 source work.
- No provider/cloud request executed.

## 7. Git/connector cleanup chưa làm được

Ba branch tạm do Git-object work trước đó phải **verify vẫn chỉ trỏ base/không có unique work rồi mới delete**:
- `feature/local-ai-hub-v8-wave0-temp`
- `feature/local-ai-hub-v8-wave0-temp2`
- `feature/local-ai-hub-v8-wave0-temp3`

GitHub connector hiện không expose delete-ref; không force-push/rewrite history để thay thế thao tác delete branch. Codex local có thể dùng `git push origin --delete <branch>` sau verification và explicit cleanup scope.

`PLAN.md` không được tái tạo; file handoff chính theo yêu cầu hiện tại là `Plan_Miss.md`.

## 8. Release còn thiếu

- Không merge PR V8 vào `main` trong Wave 0–2.
- Không tạo V8 tag/version/release.
- Sau Windows acceptance phải cập nhật PR evidence, resolve Plan_Miss, rồi người dùng quyết định release/main merge.
