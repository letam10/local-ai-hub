# Plan_Miss — LOCAL AI HUB V8 deferred/local work

File này là handoff bắt buộc cho Codex sau Wave 0–4. Chỉ đánh dấu DONE khi có evidence phù hợp với loại gate: source gate có thể chứng minh bằng CI; Windows/runtime/desktop gate phải có evidence trên đúng Windows installation. Không suy diễn Linux CI thành Windows acceptance. Không tự merge `main`, không tạo/move tag, không download model/runtime lớn hoặc chạy GPU inference nếu chưa có phê duyệt riêng.

## 1. Tổng hợp source work đã hoàn thành

Wave 0: Storage Authority, SQLite transaction journal, reservation/job/transaction binding, staged-private + copy-once managed object, public commit atomic, exact-identity cleanup; Wave 2 sửa stale aborted staged-row reconciliation để cleanup hội tụ.

Wave 1: durable component operation journal; confirmation bắt buộc; terminal operation không chạy lại; restart mất process-local V7 plan → `plan_session_lost`; committed không tự nâng OPERATIONAL.

Wave 2: new worker-output publication chuyển sang V8, historical V7 reads được giữ. Publication boundary tự re-check V7 ownership scope; V8 reservation không tự chứng minh filesystem ownership. Byte/atomic output giữ đúng public artifact name/media type.

Wave 3: manual-import và composite-bundle plan có opaque durable operation; planned operation có durable cancel; direct operation routes; tracked-catalog source acceptance; không provider request, download, implicit license accept hoặc implicit model AUTO_INSTALL_READY.

Wave 3 re-audit trong Wave 4 đã sửa:
- `REFERENCE_EXISTING` là disposition có thẩm quyền; không được biến thành license/source/integrity/size download gate.
- V8 Component route dùng `ApiContext` application-owned bindings thay vì transport layer tự gọi singleton manager.
- Operation list có bounded `limit=1..500`.
- direct confirm gặp `plan_session_lost` trả conflict 409, không giả thành success.

Wave 4 source-side Product UX đã làm:
- Components page có V8 operation journal riêng.
- Hiển thị opaque operation ID/state/action/result.
- Chỉ `planned` operation có Confirm/Cancel action.
- Mỗi component có truthful V8 source-acceptance state + disposition + source/auth/license/integrity/size gates.
- Không raw workstation path/credential/command trong surface mới.
- Không polling; refresh theo page render/navigation hoặc nút user refresh.
- New V8 UX module được mount từ `src/ui/index.html`.
- Duplicate `id="snapshot-status"` cũ trong `index.html` đã được loại bỏ.

Source validation sau Wave 4 implementation: GitHub Actions run #667 PASS:
- `python scripts/ci_validate.py`: PASS, 723 tracked files, không forbidden artifact/unmasked secret;
- `python -m unittest discover -s tests -p "test_v8_*.py"`: 27/27 PASS;
- `git diff --check` từ PR base: PASS.

Codex phải chạy lại ba gate trên ở HEAD mới nhất trước khi bắt đầu local acceptance.

## 2. LOCAL_ONLY / hiện không tồn tại trên GitHub

Các dữ liệu sau phải inspect trên máy thật. Không tạo giả từ `*.example.*` hoặc source test fixture:

- `D:\LocalAIHub\Config\` machine state, gồm `v8_control.sqlite3` nếu V8 đã chạy thật, legacy `artifacts.json`, job stores, receipts, component history, machine registry, recovery/forensic state và local settings.
- `D:\LocalAIHub\Models\`.
- `D:\LocalAIHub\Environments\`.
- `D:\LocalAIHub\runtime\`.
- `D:\LocalAIHub\Output\`, bao gồm historical V6/V7 outputs và `.hub-v8` nếu đã tạo thật.
- `D:\LocalAIHub\Cache\`, `Temp\`, `Logs\`, `Reports\`, `Backups\`.
- Project/workflow persistent state và user media.
- Existing SAM2, AnimeSR, Whisper, FFmpeg, AIRI, ComfyUI, FLUX, Qwen và các runtime/model installations thực tế.
- External-managed runtime/model roots hoặc junction/reparse tồn tại trên máy.
- Desktop-installed binaries, pywebview/WebView2 runtime, shortcut, packaging/installer state.
- Windows process/service ownership và process đang chạy.
- Actual disk/free-space/quota/ACL/locking state.
- Native-picker selections, selected local file/folder paths, provider credentials và local license acceptance records.

Không commit các dữ liệu này vào Git. Không overwrite/remove forensic evidence hoặc user files để làm test pass.

## 3. Windows filesystem adversarial acceptance — CHƯA LÀM

Dùng clean/controlled test DATA_ROOT trên NTFS, không phá dữ liệu thật:

- symlink/junction/reparse/mount-point tại DATA_ROOT, Config, Output, `.hub-v8`, object shard và producer ancestor;
- root/ancestor replacement sau khi `RootLease` đã cấp;
- producer swap/truncate/rename/replace trong khi copy;
- managed-object replacement giữa resolve và stream/open;
- Windows file sharing, handle identity, antivirus/indexer lock và TOCTOU giữa validation → copy → authorize → commit;
- crash/process kill ở reserve, producer, copy, stage, authorize, final public commit, cleanup và startup reconcile;
- final public commit phải là persistence boundary cuối cùng bắt buộc để artifact hợp lệ;
- concurrent same-name producers và multi-artifact transaction;
- cancellation trước producer, giữa producer, sau stage, trước/đúng lúc commit;
- SQLite locked/read-only/disk-full/corrupt behavior phải fail closed, giữ evidence và không replace DB bằng dữ liệu giả;
- ambiguous/foreign/reparse files không được auto-delete.

## 4. Wave 2 production callsite inventory — cần Codex local

GitHub connector search không chứng minh được 100% symbol/callsite coverage. Trên full checkout chạy exact grep/ripgrep và phân loại mọi caller của:

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
- `DurableWorkEngine` / `LegacyDurableWorkEngine` / `V8DurableWorkEngine`

Với từng output-producing tool xác minh:

1. V8 reservation được tạo trước producer/adaptor work.
2. Filesystem candidate được ownership scope chứng minh tại publication boundary.
3. Producer failure/cancel không xóa foreign/user file.
4. Public artifact chỉ xuất hiện sau V8 final commit.
5. Historical V6/V7 artifact vẫn read được.

Xác minh direct `HubJobManager` và `V8DurableWorkEngine` bằng bounded fake/local adapter trong production composition: reserve → adapter → managed producer/bytes → publish → restart/cancel/retry → artifact lineage.

Legacy V6/V7 JSON/CAS chưa destructive-migrate. Nếu muốn bỏ legacy store, lập migration/backfill package riêng và test downgrade/read compatibility trước.

## 5. Wave 3 local gaps — CHƯA LÀM

Native picker/selection vẫn process-local, expiry-based. Operation journal không làm selected path restart-resumable. Phải test trên Windows:

- user cancel picker;
- expired selection;
- wrong component/type;
- selected file/folder là reparse/junction;
- selected file đổi identity/size/hash sau selection trước confirm;
- restart giữa selection và confirm;
- restart giữa plan và confirm;
- stale selection không được tự rediscover theo path.

Executing component-operation cancellation chưa có durable process-owned bridge. Hiện chỉ `planned → cancelled`. Không force-kill process từ operation ID cho tới khi ownership/cancel handle được thiết kế và test.

Composite bundle vẫn ordered V7 child execution, chưa atomic multi-component rollback. Cần test partial child success/failure, restart, shared dependency preservation, rollback idempotence và user-data preservation.

Trusted-source acceptance hiện chỉ đọc tracked catalog. Còn thiếu real upstream/source review:

- canonical official source;
- exact revision/release;
- HTTPS/provider identity;
- exact size/digest;
- license/SPDX/review state;
- auth/gated-source behavior;
- runtime/dependency graph;
- source outage/rate-limit/auth-failure behavior.

Production catalog validator đang cố ý cấm model AUTO_INSTALL_READY. Không bỏ guard chỉ để test pass. Mọi component promotion phải là package riêng có evidence.

## 6. Wave 4 Product UX — source DONE, Windows/WebView2 acceptance CHƯA LÀM

Source files hiện đã có:

- `src/services/api/context.py`
- `src/services/api/routes/component_v8.py`
- `src/ui/features/components/render.js`
- `src/ui/features/components/v8_control_plane.js`
- `src/ui/index.html`
- `tests/test_v8_wave4_product_ux.py`

Codex phải mở **desktop app thật** và xác minh:

- Components page render V8 panel đúng trong WebView2;
- recent operation list hiển thị đúng sau create/confirm/cancel;
- source acceptance gắn đúng component row;
- `planned` có Confirm/Cancel, terminal/executing không hiện action sai;
- browser confirmation không thể bị bypass bằng double-click/re-render;
- keyboard Tab/Shift+Tab/Enter/Space hoạt động;
- focus không bị mất hoặc nhảy sai sau refresh/render;
- screen-reader labels/live-region hợp lý;
- 5 language modes không tạo mojibake/overflow/truncation; strings chưa được dịch phải được ghi nhận và sửa source, không fake acceptance;
- responsive layout ở desktop minimum size và HiDPI scaling;
- refresh không tạo request loop/polling;
- route navigation ra/vào Components không nhân đôi listener/request vô hạn;
- API error/degraded state không làm mất dữ liệu đang hiển thị hoặc lộ stack/path;
- raw path, executable, credential, selection path không xuất hiện trong DOM/network JSON public.

Static/Linux tests **không đủ** để đánh dấu các mục này DONE.

## 7. Real component lifecycle matrix — CHƯA LÀM

Trên clean/controlled Windows test root, làm từng vertical slice chứ không bật hàng loạt:

- inspect → plan → explicit confirm → install/import/reuse → verify → receipt → bounded smoke → OPERATIONAL evidence;
- repair phải reacquire/reconstruct artifact đúng khi policy cho phép, không chỉ revalidate;
- update phải candidate → stage → verify → activate → rollback, không auto-update;
- rollback giữ version/evidence trước và không đụng user data;
- uninstall preserve shared dependencies, project/workflow/user media;
- source outage không demote local installation hợp lệ;
- auth/license-required component dừng đúng gate;
- restart/crash recovery giữa từng state;
- bundle phải chứng minh partial-failure rollback/restart semantics.

Ưu tiên component nhỏ/trusted/runtime trước. Không suy diễn từ synthetic fixture sang production component.

Mọi download >1 GB hoặc GPU/model inference phải dừng hỏi người dùng nếu chưa có phê duyệt riêng.

## 8. Loopback API / Desktop / packaging acceptance — CHƯA LÀM

- Real launch `127.0.0.1:8765`; không bind LAN.
- Startup/shutdown/restart/concurrent request acceptance.
- Direct V8 operation routes và source-acceptance routes qua real HTTP server.
- Artifact GET/HEAD/range streaming cho V8 object và historical V7 artifact.
- Windows open-artifact shell behavior.
- pywebview/WebView2 lifecycle, close/reopen, crash/reload.
- Installer/portable package/managed shortcut/upgrade preservation.
- Clean-clone setup và APP_ROOT/DATA_ROOT reuse.
- Không thay CUDA/driver hoặc model registry ngoài explicit plan.

## 9. `v8_control.sqlite3` backup/restore/migration — CHƯA LÀM

Thiết kế và test consistent snapshot. Không copy file SQLite đang transaction theo cách tạo snapshot rách.

Acceptance phải bao phủ:

- WAL/journal mode thực tế;
- backup trong idle và concurrent read/write;
- restore vào clean controlled root;
- schema/version mismatch;
- corrupt/read-only/locked DB;
- interrupted backup/restore;
- preserved artifact object ↔ DB identity;
- legacy V7 data vẫn đọc được;
- không overwrite existing forensic DB khi restore validation fail.

## 10. Runtime/model/GPU acceptance — DEFERRED

Chưa chạy trong Wave 0–4 source work:

- FFmpeg real workload;
- SAM2 segmentation;
- AnimeSR upscale;
- Faster-Whisper transcription;
- ComfyUI lifecycle;
- FLUX/Qwen inference;
- GPU/VRAM/CUDA behavior.

No provider/cloud request được thực hiện bởi source work này.

## 11. Git/branch cleanup chưa thực hiện

Ba branch tạm phải **verify không có unique work rồi mới delete**:

- `feature/local-ai-hub-v8-wave0-temp`
- `feature/local-ai-hub-v8-wave0-temp2`
- `feature/local-ai-hub-v8-wave0-temp3`

GitHub connector hiện không expose delete-ref. Không force-push/rewrite history để thay thế. Codex local có thể dùng `git push origin --delete <branch>` chỉ sau khi xác minh chúng không chứa unique work và cleanup scope được cho phép.

`PLAN.md` không được tái tạo. Handoff chính là `Plan_Miss.md`.

## 12. Release gate — CHƯA ĐƯỢC PHÉP

- Giữ PR #102 OPEN + DRAFT trong Wave 4.
- Không merge V8 vào `main` tự động.
- Không tạo/move V8 tag/version/release.
- Wave 5 Acceptance & Release chỉ bắt đầu sau khi các Windows real-lifecycle gates cần thiết ở trên có evidence và `Plan_Miss.md` được cập nhật theo kết quả thật.
- Người dùng quyết định cuối cùng về release/main merge.
