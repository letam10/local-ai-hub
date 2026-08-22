# Plan_Miss — LOCAL AI HUB V8 deferred/local work

Handoff bắt buộc cho Codex sau Wave 0–6 source work. Chỉ đánh dấu DONE khi có evidence đúng loại gate. Linux/GitHub CI không thay thế Windows acceptance. Không tự merge `main`, không tạo/move tag, không bump version, không build/publish release, không download/model/GPU workload lớn nếu chưa có phê duyệt riêng.

## 1. Source work đã hoàn thành

- Wave 0: Storage Authority, SQLite transaction journal, reservation/job/transaction binding, staged-private + copy-once managed object, atomic public commit, exact-identity cleanup.
- Wave 1: durable component operation journal, explicit confirmation, terminal no-reexecution, restart fail-closed, committed không tự nâng OPERATIONAL.
- Wave 2: worker-output publication qua V8 authority; V7 ownership scope chỉ làm safety proof; historical V7 read compatibility giữ nguyên.
- Wave 3: durable import/bundle operations, planned cancel, direct operation routes, catalog-only source acceptance, không provider request/download/license auto-accept/model auto-promote.
- Wave 4: Components Product UX/API source re-audit; finite component metadata/action, fail-closed invalid records, idempotent mount, truthful `REFERENCE_EXISTING`/`MANUAL_INSTALL`, no polling/raw path.
- Wave 5: read-only acceptance gate; re-audit đã thêm actual local report verification thay vì chỉ tin declared SHA-256.
- Wave 6: V8-specific release policy + read-only release candidate/provenance preparation; không chọn/bump version/tag và không sửa historical V7 release evidence.

## 2. LOCAL_ONLY / không tồn tại đầy đủ trên GitHub

Phải inspect trên máy thật, không tạo giả từ example/test fixture:

- `D:\LocalAIHub\Config\`: `v8_control.sqlite3` nếu đã tạo thật, legacy `artifacts.json`, job stores, receipts, component history, local settings, machine registry, recovery/forensic state.
- `D:\LocalAIHub\Models\`, `Environments\`, `runtime\`, `Output\`, `Output\.hub-v8\`, `Cache\`, `Temp\`, `Logs\`, `Reports\`, `Backups\`.
- Projects/workflows/user media/persistent state.
- Existing SAM2, AnimeSR, Faster-Whisper, FFmpeg, AIRI, ComfyUI, FLUX, Qwen và runtime/model installations thật.
- External-managed roots, junction/reparse, ACL, disk/free-space/quota/file-lock state.
- pywebview/WebView2 installation, desktop binaries, shortcuts, installer/package state.
- Native picker selections, selected local paths, provider credentials, local license decisions.
- Local Windows acceptance evidence/report bundle.

Không commit các dữ liệu trên. Không overwrite/remove forensic evidence hoặc user data để test pass.

## 3. Wave 4 Windows filesystem adversarial acceptance — CHƯA LÀM

Dùng clean/controlled NTFS test DATA_ROOT:

- symlink/junction/reparse/mount-point tại DATA_ROOT, Config, Output, `.hub-v8`, object shard, producer ancestor;
- root/ancestor replacement sau `RootLease`;
- producer swap/truncate/rename/replace trong lúc copy;
- managed-object replacement giữa resolve và stream/open;
- Windows file sharing, handle identity, antivirus/indexer lock, TOCTOU validation → copy → authorize → commit;
- crash/process kill ở reserve, producer, copy, stage, authorize, public commit, cleanup, startup reconcile;
- concurrent same-name producers, multi-artifact transaction;
- cancellation trước producer/giữa producer/sau stage/trước commit;
- SQLite locked/read-only/disk-full/corrupt phải fail closed và giữ evidence;
- ambiguous/foreign/reparse files không auto-delete.

## 4. Wave 2 production callsite inventory — cần Codex local

Trên full checkout chạy exact `rg` cho mọi caller của:

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

Với mỗi output producer chứng minh reservation trước producer, ownership proof tại publication boundary, failure/cancel không xóa foreign/user file, public artifact chỉ sau final commit, historical V6/V7 read vẫn hoạt động. GitHub connector search không đủ để tuyên bố 100% callsite coverage.

## 5. Wave 3 component lifecycle local gaps — CHƯA LÀM

Native picker/selection vẫn process-local/expiry-based. Test user cancel, expiry, wrong type/component, reparse/junction selection, selected file thay identity/size/hash sau selection, restart selection→plan→confirm, stale selection không tự rediscover path.

Executing operation cancellation chưa có durable process-owned bridge; không force-kill bằng operation ID.

Composite bundle chưa atomic multi-component rollback. Test partial failure, restart, shared dependency preservation, rollback idempotence, user-data preservation.

Per-component source review còn thiếu official source, exact revision, HTTPS/provider identity, exact size/hash, license, auth, dependency graph, outage/rate-limit behavior. Không bỏ model AUTO_INSTALL guard chỉ để test pass.

## 6. Wave 4 Product UX — source DONE, WebView2 acceptance CHƯA LÀM

Trên desktop app thật xác minh:

- operation journal/source acceptance render đúng;
- invalid/unknown metadata không hiển thị sai thành model/runtime/action khác;
- chỉ `planned` có Confirm/Cancel;
- double-click/re-render không bypass confirmation;
- navigation nhiều lần không duplicate listener/request loop;
- Tab/Shift+Tab/Enter/Space, focus continuity, screen reader/live region;
- 5 language modes không mojibake/overflow; string chưa dịch phải ghi nhận;
- HiDPI/minimum desktop size;
- degraded/error state không lộ stack/path;
- DOM/network public không có raw path/executable/credential/selection path.

## 7. Wave 5 acceptance evidence — CHƯA CÓ WINDOWS EVIDENCE

Required gates:

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

Tạo local/untracked bundle:

```text
<acceptance-root>\
  evidence.json
  reports\
    windows_filesystem.json
    artifact_callsite_inventory.json
    native_picker_restart.json
    executing_operation_cancel.json
    bundle_atomic_rollback.json
    real_component_lifecycle.json
    loopback_api.json
    webview2_product_ux.json
    sqlite_backup_restore.json
    packaging_upgrade.json
    crash_recovery.json
    runtime_smoke.json
```

`evidence.json` schema `v8-local-acceptance-evidence.v1`, class `local_windows`, platform `windows-x64`, exact source commit và đủ 12 gate IDs.

Mỗi PASS report phải là bounded JSON schema `v8-local-gate-report.v1` với exact `gate_id`, `status=PASS`, `platform=windows-x64`, exact `source_commit`, non-empty finite `checks` và mọi check = true. `scripts/v8_acceptance_gate.py` tự hash actual report bytes; digest phải bằng `report_sha256` trong evidence. Không được chỉ điền digest giả.

Chạy:

```powershell
python scripts/v8_acceptance_gate.py --strict-release --evidence <acceptance-root>\evidence.json
```

Không sửa gate/script/report chỉ để biến BLOCKED thành PASS.

### 7A. Kết quả chạy local Windows 2026-08-22 — evidence chưa đủ để release

Đã chạy trên Windows với dữ liệu thử nghiệm cô lập và không chạm `Config`,
`Output`, model, runtime hay forensic bundle của máy:

- Source fix `b8ebceddae037c0678d4c84875f06ef30320ff28`
  (`fix(v8): encode NTFS identities for SQLite`) sửa overflow khi Windows
  `st_ino` vượt SQLite signed INTEGER. Full `test_v8_*.py` chạy 41 test PASS,
  `ci_validate.py` PASS với 732 tracked files, source-only V8 policy hợp lệ.
- Các kiểm tra filesystem/transaction V8 đang có chạy 11 test PASS trên root
  thử nghiệm, gồm output-root reparse refusal, reservation/job binding,
  staged-not-public, detached managed object, replacement fail-closed và
  reconciliation. Đây là coverage hữu ích nhưng chưa bao phủ toàn bộ ma trận
  adversarial ở mục 3, nên `windows_filesystem` chưa được đánh dấu PASS.
- Inventory trực tiếp dùng đủ 13 biểu thức bắt buộc ở mục 4. `DurableWorkEngine`
  production export là `V8DurableWorkEngine`; test V8 migration pass. Năm test
  write-path V6 cũ vẫn fail khi không inject `V8OutputBridge` riêng vào fixture;
  `v8_engine.py` là base-identical với V8 remote và không bị source fix này sửa.
  Vì chứng minh full producer compatibility chưa hoàn tất, gate inventory chưa PASS.
- Controlled `HubHTTPServer` thật trên `127.0.0.1:8765` đã phục vụ V8 operation
  list/detail/confirm/cancel và source-acceptance qua HTTP, payload path-free,
  sau shutdown listener được giải phóng. Artifact GET/HEAD/range V8 + historical
  V7, restart và concurrency đầy đủ chưa có evidence cùng gate.
- Component/bundle/receipt/restart static-fixture matrix 38 test PASS; backup
  and recovery supporting matrix 32 test PASS; durable recovery supporting
  matrix 29 test PASS. Đây không thay thế lifecycle có component thật, backup
  V8 SQLite live, crash-kill hay rollback bundle multi-component.
- Real pywebview lifecycle smoke đã chạy: close/reopen, tray roundtrip,
  cooperative owned-worker cancel và listener release pass; second-instance
  probe fail. Vì vậy `webview2_product_ux` vẫn BLOCKED.
- Package/release source tests cho V8 policy pass, nhưng legacy package-builder
  refusal `SOURCE_BRANCH_MISMATCH` trên V8 là đúng boundary. Candidate version/tag
  vẫn null; không build package, không tag, không merge.
- Runtime/model/GPU smoke chưa chạy: Hub Python environment không hiện diện ở
  `Environments/hub`, `ffmpeg` không có trên PATH; hơn nữa model/GPU inference
  cần phê duyệt riêng theo mục 11.

Kết luận hiện tại: local evidence phải ghi đúng PASS/BLOCKED theo source commit
cuối cùng; strict release vẫn BLOCKED. Không coi source/static fixture pass là
Windows production, desktop product, model-runtime hay release readiness.

### 7B. Tiến độ tiếp theo sau source remediation — partial evidence được xác minh

Sau checkpoint trên, V8 đã bổ sung các source/test package nhỏ, reviewable:

- V8 SQLite backup/restore dùng SQLite backup API, validate integrity/schema
  trước restore, không overwrite backup leaf đang tồn tại và giữ journal live
  khi snapshot corrupt/schema mismatch. Nguồn của `backup_to()` được mở
  read-only; backup trên journal Config thật có thể kiểm chứng không đổi
  source bytes/identity trong lúc tạo snapshot ở root task-owned.
- Direct `HubJobManager` và `V8DurableWorkEngine` local-adapter proof reserve
  trước producer, publish object V8 detached/path-free; V6 write fixture gọi
  tường minh `LegacyDurableWorkEngine`, còn production alias giữ V8 authority.
- Native selection test phủ cancel/missing, expiry, wrong type, reparse, đổi
  file sau selection và restart stale token; stale token không tự rediscover.
- Composite bundle rollback chỉ uninstall component mới do lần confirm hiện tại
  cài; shared dependency có sẵn được preserve. Milestone `pending`/`reused`/
  `installing`/`installed`/`rollback_pending`/`rolled_back` được journal theo
  V8 operation trước side effect. Rollback failure hoặc restart giữa step sẽ
  thành `manual_review`, không suy diễn ownership theo component name để
  uninstall dữ liệu/user installation; compensation hoàn tất không được
  replay khi restart.
- Executing install cancellation được bind vào đúng process-local cancel event
  của invocation; operation không có owned bridge bị refuse, không force-kill.
- Loopback V8/V7 artifact GET/HEAD/range, concurrent reads và restart đã chạy
  trên 127.0.0.1:8765. WebView2 lifecycle smoke pass sau khi second instance
  singleton refusal được coi là behavior đúng thay vì tạo app/API thứ hai.
- V8 cleanup Windows dùng handle-bound identity-attested deletion; replacement
  foreign trước cleanup không bị xóa. Crash child acceptance phủ reserve,
  producer, staged, authorized và post-commit recovery.

Historical evidence at source `529e3108227ddde64aab7197febd4e72ff504b9f`
verified four PASS reports by their actual bytes/digests:

- `artifact_callsite_inventory` —
  `7a723e65191a3a99799401cae4b3acc6e2925de4abc38a6258d1da29f890f9fb`;
- `native_picker_restart` —
  `6b467b9466fbaee00d791c482a626f11f66172707b71ecbe675311b416a0c5a4`;
- `executing_operation_cancel` —
  `057df3d370b304d1cf7bccb0bb5cbfff9ac71e451db916b969d86a4732eaf974`;
- `bundle_atomic_rollback` —
  `f407af04fecafca179979279387a14cf4e024c311912e51b33decc77559ab1ee`.

Eight gates remain BLOCKED because their full acceptance matrix is not yet
complete, not because a report is absent: Windows filesystem sharing/physical
disk-full/AV matrix, real component operational lifecycle, shell open-artifact,
product UX/i18n/a11y, V8 BackupManager lock/read-only/interruption flow,
packaging identity, remaining crash cleanup phases, and runtime/model/GPU
approval. Evidence must be regenerated after every source commit; strict
release cannot be inferred from these partial PASS reports.

### 7C. FFmpeg source acceptance correction — source-only, install pending

- The pinned GyanD `9.0.1` release asset was independently reconciled against
  the release API and HTTPS HEAD metadata: the essentials ZIP is
  `111253802` bytes with SHA-256
  `fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9`.
  GyanD's build page classifies the static essentials build as GPLv3.
- The V2 production catalog now records the reviewed `GPL-3.0-or-later`
  contract plus a fixed archive-to-managed-leaf mapping. Source acceptance and
  the executable lifecycle share the same finite license allowlist; a
  `review_required` V2 record refuses before downloader invocation.
- The archive has **not** yet been downloaded or installed by this source
  change. A fresh explicit lifecycle plan, confirmation, pinned-download
  verification, receipt, and bounded CPU-only smoke remain required before
  `real_component_lifecycle` or `runtime_smoke` can become PASS.
- Live Windows preflight then found the legacy `runtime/tools/ffmpeg` target is
  a junction into the Anime Upscale Studio runtime, which contains an older
  Jellyfin FFmpeg 4.4.1 build. It is intentionally not treated as a V8-managed
  leaf and must not be overwritten, removed, or followed by the installer.
  V8 planning and confirmation now refuse that existing/reparse target before
  a downloader is created. A separate, explicitly scoped migration or
  reference-existing decision is required before any FFmpeg download can run.

### 7D. BackupManager V8 SQLite archive integration — fixture-verified, production matrix pending

- `BackupManager` now adds the fixed opaque archive member
  `v8/v8_control.sqlite3` only when an existing safe V8 control journal is
  present. It obtains that member through the transaction store's read-only
  SQLite backup API, not by copying raw live database bytes. Legacy JSON-only
  backups remain readable with their original manifest shape.
- Restore stages and validates the SQLite snapshot before changing Config,
  applies ordinary JSON members first, then restores the existing V8 journal
  last; a SQLite refusal rolls back any already-applied JSON members. A
  missing/reparse/corrupt V8 journal or snapshot fails closed and does not
  create a new live journal from the archive.
- Task-owned fixture coverage includes archive membership and hash integrity,
  copy-only restore, post-plan V8 conflict refusal, corrupt snapshot refusal,
  reparse-backed source refusal, a SQLite EXCLUSIVE-lock timeout and injected
  snapshot failure with no JSON-only archive publication, plus JSON rollback
  when the final SQLite restore refuses. The real backup HTTP route also
  completed create/inspect/plan/unconfirmed/confirmed restore against a
  controlled loopback Config root. A separate read-only snapshot of the real
  local journal was performed earlier; this integration has not restored or
  overwritten the real `Config/v8_control.sqlite3`.
- The full Windows matrix is still required before `sqlite_backup_restore`
  can be PASS: WAL/concurrent writer behavior through `BackupManager`,
  read-only/locked/ENOSPC fault injection, interrupted restore/restart, and
  actual backup-route confirm/cancel checks. No release or runtime claim is
  implied by the source/fixture result.

## 8. Real component lifecycle — CHƯA LÀM

Từng vertical slice trên controlled Windows root:

- inspect → plan → explicit confirm → install/import/reuse → verify → receipt → bounded smoke → OPERATIONAL evidence;
- repair reacquire/reconstruct khi policy cho phép;
- update candidate → stage → verify → activate → rollback;
- rollback giữ previous version/evidence;
- uninstall preserve shared dependencies/project/workflow/user media;
- source outage không demote valid local install;
- auth/license gate dừng đúng chỗ;
- restart/crash recovery giữa state;
- bundle chứng minh rollback/restart semantics.

Download >1 GB hoặc model/GPU inference phải dừng hỏi người dùng nếu chưa có approval riêng.

## 9. Loopback API / Desktop / packaging — CHƯA LÀM

- real `127.0.0.1:8765`, không LAN bind;
- startup/shutdown/restart/concurrency;
- V8 operation/source routes qua HTTP thật;
- artifact GET/HEAD/range cho V8 + historical V7;
- Windows open-artifact shell;
- pywebview/WebView2 close/reopen/crash/reload;
- installer/portable/managed shortcut/upgrade preservation;
- clean clone + APP_ROOT/DATA_ROOT reuse.

## 10. `v8_control.sqlite3` backup/restore — PARTIAL, full Windows acceptance còn thiếu

Không copy live DB theo cách tạo snapshot rách. Nguồn hiện dùng SQLite backup
API cho member archive cố định và fixture đã phủ clean restore/schema mismatch/
corrupt/reparse/plan conflict. Vẫn phải test WAL/journal mode, idle/concurrent
backup qua BackupManager, read-only/locked/ENOSPC, interrupted backup/restore,
artifact-object↔DB identity, legacy V7 compatibility và failed validation không
overwrite forensic DB.

## 11. Runtime/model/GPU — DEFERRED

Chưa chạy FFmpeg workload, SAM2, AnimeSR, Faster-Whisper, ComfyUI, FLUX/Qwen inference, GPU/VRAM/CUDA behavior. Source Wave 0–6 không thực hiện provider/cloud workload.

## 12. Wave 6 release identity/provenance — SOURCE POLICY DONE, USER APPROVAL CÒN THIẾU

Tracked files mới:

- `architecture/v8_release_policy.json`
- `scripts/v8_release_provenance.py`
- `tests/test_v8_wave6_release_provenance.py`
- `docs/V8_WAVE6_RELEASE_PROVENANCE_PREPARATION.md`

V8 provenance framework giờ đã tách khỏi V7, nhưng **final release identity chưa được chọn**:

- `candidate_version = null`;
- `candidate_tag = null`;
- approval identity = `required`;
- `src/shared/version.py` vẫn `7.1.0`;
- historical `distribution/release_manifest.json` và V7 tags phải giữ nguyên.

Codex không tự chọn final version/tag. Khi người dùng duyệt, có thể dry-run trước:

```powershell
python scripts/v8_release_provenance.py --candidate-version <8.x.y> --candidate-tag <v8.x.y>
```

Dry-run không ghi file/ref. Sau approval cần package riêng để cập nhật release identity/product version/provenance/build inputs nhất quán; không rewrite V7 evidence.

## 13. Yêu cầu người dùng còn cần phê duyệt trước release

Sau khi Windows gates có evidence thật, cần người dùng xác nhận rõ:

1. final V8 version, ví dụ `8.0.0` nếu người dùng chọn;
2. final tag phải khớp exact version, ví dụ `v8.0.0`;
3. cho phép release activation package sửa V8 product version/provenance/build metadata;
4. sau strict PASS, cho phép merge V8 → main;
5. cho phép create immutable tag và build/publish release artifact.

Không suy diễn việc người dùng yêu cầu “tiếp tục Wave” thành approval cho 5 hành động release trên.

## 14. Git branch cleanup chưa thực hiện

Ba temp branch phải verify không có unique work rồi mới delete:

- `feature/local-ai-hub-v8-wave0-temp`
- `feature/local-ai-hub-v8-wave0-temp2`
- `feature/local-ai-hub-v8-wave0-temp3`

Không force-push/rewrite history để cleanup. `PLAN.md` không tái tạo; handoff chính là `Plan_Miss.md`.

Ngày 2026-08-22 đã kiểm tra cả ba remote ref cùng trỏ
`964cacc445d612026cdd9123995d56f9bb367631`, mỗi ref có 0 commit riêng so với
V8, rồi xóa đúng ba ref tạm bằng non-force deletion. Không xóa branch V8,
`main`, tag, PR hay local evidence.

## 15. Release gate hiện tại

- PR #102 giữ OPEN + DRAFT.
- Không tự merge V8 → main.
- Không bump version/create-move tag/build-publish installer.
- Source gate phải PASS ở HEAD mới nhất.
- 12 local Windows gates phải PASS với report bytes được hash/verified trên exact source commit.
- Final V8 release identity cần user approval.
- Release activation/provenance package cần review riêng.
- Strict release preflight phải PASS trước bất kỳ tag/release action nào.
