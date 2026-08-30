# Plan_Miss — LOCAL AI HUB V8 deferred/local work

Handoff bắt buộc cho Codex sau Wave 0–6 source work. Chỉ đánh dấu DONE khi có evidence đúng loại gate. Linux/GitHub CI không thay thế Windows acceptance. Không tự merge `main`, không tạo/move tag, không bump version, không build/publish release.

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

## 3. Wave 4 Windows filesystem adversarial acceptance — PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

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

## 5. Wave 3 component lifecycle local gaps — PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

Native picker/selection vẫn process-local/expiry-based. Test user cancel, expiry, wrong type/component, reparse/junction selection, selected file thay identity/size/hash sau selection, restart selection→plan→confirm, stale selection không tự rediscover path.

Executing operation cancellation chưa có durable process-owned bridge; không force-kill bằng operation ID.

Composite bundle chưa atomic multi-component rollback. Test partial failure, restart, shared dependency preservation, rollback idempotence, user-data preservation.

Per-component source review còn thiếu official source, exact revision, HTTPS/provider identity, exact size/hash, license, auth, dependency graph, outage/rate-limit behavior. Không bỏ model AUTO_INSTALL guard chỉ để test pass.

## 6. Wave 4 Product UX — source DONE, WebView2 acceptance PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

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

## 7. Wave 5 acceptance evidence — PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

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

`evidence.json` schema `v8-local-acceptance-evidence.v2`, class `local_windows`, platform `windows-x64`, exact source commit, đủ 12 gate IDs và provenance cho từng gate. Report exact-head dùng `RERUN_EXACT_HEAD`; report được tái sử dụng chỉ hợp lệ với `REUSED_UNAFFECTED_EVIDENCE`, originating commit thực và diff-impact scope không chạm gate.

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
- Controlled Windows sharing coverage now holds a task-owned producer with a
  real zero-share `CreateFileW` handle. Publication refuses without a public
  artifact and preserves the producer bytes after the handle is released.
  A task-owned `ENOSPC` write-boundary injection has the same fail-closed,
  no-publication/no-delete outcome. These are bounded proofs only; they do not
  stand in for the still missing AV/indexer or physical disk-full matrix.

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

### 7E. Latest local evidence checkpoint — five truthful PASS gates

At executable source checkpoint
`b72e49bbf04f42d79591d372ae2a1a646a2d4985`, the local Windows evidence
bundle verified five report byte digests against the exact commit:

- `artifact_callsite_inventory` —
  `c2016a500fd1fe0d6cfcd43b43d71227e4306468131cf7805682991b6fcf6f29`;
- `native_picker_restart` —
  `bc5baa1177b288015e1301e6de8d9c15770e95cf389428fac7a768b3f95444e1`;
- `executing_operation_cancel` —
  `7d29dfd1edf14ace470dc1aaaccc143c61bf5f449bd49d1e001062dc5f9d7307`;
- `bundle_atomic_rollback` —
  `1d746da5a4b801d609094f043a760ae8d98c0d6c9ae706346e840b2bd0dae69e`;
- `loopback_api` —
  `5a8d1f98922fe3e0ae7aed30e935154c087c129852053dc1c8d10f44be8fd8fe`.

The remaining gates are explicitly `BLOCKED`, not synthetic PASS:

- `windows_filesystem`: root/reparse/lease/replacement, concurrent producer,
  zero-share Windows handle and bounded ENOSPC probes pass on task-owned roots;
  AV/indexer and physical-volume-full coverage is still missing.
- `real_component_lifecycle` and `runtime_smoke`: the reviewed FFmpeg leaf is
  a legacy reparse-backed runtime; no migration/reference-existing authority
  or V8-managed smoke target exists.
- `webview2_product_ux`: native close/reopen/tray/cancel/listener lifecycle
  smoke passes, but the full operation, keyboard, a11y, five-language and
  HiDPI matrix is not yet automated or observed.
- `sqlite_backup_restore`: archive/restore/lock/failure/controlled route tests
  pass; full WAL/concurrent/read-only/interruption/restart acceptance remains.
- `packaging_upgrade`: staging is dry-run only and a V8 release identity is
  intentionally unapproved.
- `crash_recovery`: output transaction child-crash reconciliation passes, but
  the full job/component/bundle/database/desktop exact-owned-process matrix is
  incomplete.

Evidence is local and untracked. Any later source commit must regenerate its
report bundle before it can be used for release evaluation.

### 7F. Continuation checkpoint at `35bf99bb26f6efc5c0f7088d95bf876f3824c2b4`

- The V8 catalog now maps FFmpeg to the separate Hub-owned
  `runtime/v8/ffmpeg/` namespace. The legacy `runtime/tools/ffmpeg` junction
  remains read-only and is never followed, overwritten or removed.
- A real pinned GyanD FFmpeg `9.0.1` lifecycle was run under
  `Temp/V8_FFmpeg_Lifecycle_35bf99b_final`: plan → explicit confirm →
  111253802-byte archive verification → install → version/FFprobe probe →
  16×16 CPU transform → matching runtime evidence → repair → safe update
  refusal without a candidate → uninstall. All recorded checks passed and the
  legacy junction target was unchanged. No model/GPU/provider workload ran.
- The lifecycle maintenance plan now carries its private V2 catalog binding,
  record revision, install strategy and supported revision, so repair/uninstall
  cannot act on a matching component ID with a stale record.
- SQLite WAL acceptance found and fixed cleanup of task-created `-wal`/`-shm`
  sidecars. The final controlled matrix covers WAL, an active concurrent writer,
  read-only source bytes, a bounded larger journal, process-restart restore,
  injected ENOSPC publication refusal, interrupted snapshot behavior and JSON
  rollback after SQLite restore refusal. Result: PASS in
  `Temp/V8_SQLite_Acceptance_35bf99b_final/result.json`.
- Real WebView2 acceptance ran against the exact source through a task-owned
  data root. Five language modes, focus continuity, live-region presence,
  planned confirmation surface, duplicate-ID checks, mojibake/overflow checks,
  path/secret-free DOM and listener release all passed. The gate remains
  BLOCKED for the untested default open-artifact shell and full trusted
  keyboard/HiDPI/degraded/error matrix; lifecycle smoke is not promoted to a
  full UX PASS.
- `python -B -m unittest discover -s tests -p "test_v8_*.py"` passed 85 tests
  at this checkpoint; `scripts/ci_validate.py` passed 741 tracked files.

### 7G. Current truthful closure disposition

- `artifact_callsite_inventory`, `native_picker_restart`,
  `executing_operation_cancel`, `bundle_atomic_rollback`, `loopback_api`,
  `sqlite_backup_restore`, and the bounded CPU `runtime_smoke` are eligible
  for PASS reports after exact final-head byte hashing.
- `real_component_lifecycle` has a PASS install/verify/repair/uninstall slice,
  but remains BLOCKED for a reviewed update candidate/rollback activation.
- `windows_filesystem` remains BLOCKED only for AV/indexer contention and
  physical-volume-full coverage; reparse, lease, replacement, zero-share and
  injected ENOSPC cases remain safe PASS evidence.
- `webview2_product_ux` remains BLOCKED for open-artifact/default-app handling
  and the complete keyboard/screen-reader/HiDPI/degraded matrix.
- `packaging_upgrade` remains BLOCKED on release identity approval and final
  staging inputs. `crash_recovery` remains BLOCKED on the full component,
  database, bundle and desktop crash-phase matrix; output transaction child
  crashes and the bounded desktop lifecycle smoke pass.
- No V8 version/tag, main merge, release build/publish, model download, GPU
  inference or user-process intervention was performed.

### 7H. Exact final-head evidence bundle

The final untracked bundle is
`Temp/V8_Acceptance/35bf99bb26f6efc5c0f7088d95bf876f3824c2b4/evidence.json`.
`scripts/v8_acceptance_gate.py --evidence` verified `source_commit_matches=true`,
`reports_verified=true`, `passed=7`, and the strict run correctly remained
blocked (`STRICT_EXIT=1`) on the five incomplete Windows gates plus release
identity approval.

PASS report SHA-256 values for the exact `35bf99bb26f6efc5c0f7088d95bf876f3824c2b4`
HEAD are:

- `artifact_callsite_inventory`: `e05e7cbd4a10250612160fc3d8e6565b6ff1a16fe76bfc1bde0e6cd2acbed0e6`
- `native_picker_restart`: `93b8451fc3550527c936576186d7bac49103cbca9ff8c036f07bfeb3e357041b`
- `executing_operation_cancel`: `cd462d3bf2e6aa2dd526a2ba860397b1644caa3d0eae92f523386e12e5ae931f`
- `bundle_atomic_rollback`: `7e23e1fb0c34083eb3c8146bab1df7980ee3ec9e79e47fb3150ba35b2ddab322`
- `loopback_api`: `448d788a078f9be106329276f2ead08f96c2f7352019c711845df981168021a3`
- `sqlite_backup_restore`: `1c73fd000134cb62cf7c701d9756e551eb29df5b00cffcb44f708b683e41a90d`
- `runtime_smoke`: `c5b8a1040fd3aced5d073cef5b5fca1f656604dd4b653ff402b36ff85e7902c6`

### 7I. CI correctness closure after Ubuntu failure

- GitHub Actions workflow run `#785` on exact source `35bf99b` failed one
  Ubuntu test: `test_replacement_immediately_before_final_commit_is_not_published`.
  The Windows result was not treated as sufficient: Windows deny-share handles
  prevent the replacement, while POSIX `O_NOFOLLOW` protects only the opened
  inode and still permits pathname rename/replace.
- The source fix is split into two small commits: `6b2b70c` adds post-boundary
  cross-platform identity revalidation, compensating transaction/index rollback
  and the source regression; `b6d660d` makes the foreign-byte race fixture
  portable. Changed source/test/docs files are limited to the V8 storage and
  output-authority boundary.
- Windows full V8 suite after the fix: 87 tests PASS. GitHub Actions run
  `#788` on exact `b6d660d` completed PASS; run `#786` is retained as the
  expected pre-test-correction failure, not evidence of the final source.
- The seven PASS machine reports under the `35bf99b` bundle remain historical
  evidence. After this tracked handoff commit creates the final HEAD, a new
  local untracked bundle must be regenerated with exact final-head report
  bindings and verified by `scripts/v8_acceptance_gate.py --evidence`; no digest
  is to be edited by hand. The five blocked gates remain unchanged.

### 7J. Final V8 correctness handoff before exact-head evidence rebinding

- Source commit `093a5e645bdb03699eb802922d1798516987e08a`
  (`fix(v8): add catalog-bound runtime update activation`) adds a strict V2
  runtime update candidate contract: official HTTPS source identity, archive
  size/SHA-256, fixed executable-leaf mapping and bounded extracted size.
  `UpdateResolver` downloads only that server-owned candidate, while the
  runtime updater activates the isolated `runtime/v8/<slot>` directory with
  rollback storage and V3 bundle-revision receipt binding. The legacy
  `runtime/tools/ffmpeg` junction is not moved or followed.
- The exact Windows source suite passed 89 tests; `ci_validate.py` passed 743
  tracked files; source-only provenance and acceptance preflights remain valid
  and correctly blocked only by release identity/local evidence.
- Official GyanD FFmpeg 8.1.2 essentials was downloaded into task-owned Temp,
  verified at 109728040 bytes with SHA-256
  `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`.
  The controlled lifecycle completed install 9.0.1, candidate update 8.1.2,
  rollback, re-update, repair and uninstall while preserving the legacy
  marker/junction. No FFmpeg executable, model, GPU or provider workload was
  run during this lifecycle proof.
- GitHub Actions push run `32582464094` and PR run `32582466512` both passed on
  the exact source commit above. The final tracked handoff docs commit will be
  the evidence source HEAD; reports are regenerated after that commit and are
  never rebound by editing a digest manually.
- Current gate disposition is truthful: artifact inventory, native picker,
  cancellation, bundle rollback, loopback API and SQLite backup/restore remain
  eligible PASS after exact-head report regeneration; real component lifecycle
  is technically PASS after the candidate/rollback proof; Windows filesystem
  remains BLOCKED for prohibited physical-volume-full and unavailable
  AV/indexer contention; WebView2 remains BLOCKED for open-artifact/default-app
  and full UIA/keyboard/HiDPI/degraded coverage; packaging remains BLOCKED only
  on unapproved release identity; crash recovery remains BLOCKED on the full
  component/database/bundle/desktop child-crash matrix; runtime smoke remains
  separate and must not imply operational readiness.

### 7K. Final four-gate closure audit from `1f2a1a3d` — this handoff commit

- `windows_filesystem` is eligible for PASS under the invariant-based boundary
  documented in `docs/V8_WAVE5_ACCEPTANCE_RELEASE.md`. The controlled suite now
  has 7 PASS tests, including a task-owned child process holding a Windows
  zero-share `CreateFileW` producer handle. Publication refused, no public
  artifact appeared, and producer bytes remained intact after the child exited.
  Root/ancestor lease replacement, nested reparse/non-regular refusal,
  concurrent same-name producers, foreign-producer preservation, managed-object
  zero-share cleanup refusal and injected `ENOSPC` also passed. AV/indexer
  contention and physical-volume exhaustion were not manufactured: they are
  external mechanisms represented by the same deny-share/ENOSPC invariants.
- The complete Windows V8 source suite passed 90 tests after the child-process
  regression was added. `ci_validate.py` passed 743 tracked files. The actual
  task-owned child-handle harness and a separate managed-object handle harness
  were run outside the repository and their roots remain evidence-only.
- Real WebView2 was exercised from a task-owned data root. UI Automation saw the
  real WebView2 DOM (RootWebArea, navigation buttons, language combobox and
  focus landmark). Repeated Dashboard/Components navigation, Vietnamese to
  English locale switching, component plan rendering, native confirmation then
  Cancel, planned-operation Cancel, focus continuity and a deliberate API-down
  refresh (`API đang khởi động...`) were observed with no stack/path/secret
  echo. No artifact record was available in the task snapshot, so opening a
  default application was not claimed. The gate remains BLOCKED for the full
  trusted keyboard/HiDPI/screen-reader matrix and open-artifact/default-app
  proof.
- Crash support was rerun: output child-crash recovery, component journal,
  bundle restart/manual-review, SQLite backup/restore and desktop lifecycle
  smoke all passed in bounded tests (26 focused crash/component/bundle/SQLite
  tests plus the real desktop smoke). The gate remains BLOCKED until the
  complete `os._exit` matrix across component, bundle, SQLite and desktop
  phases is captured as one exact-head report; no crash result is promoted from
  synthetic fixtures alone.
- Packaging was re-audited as source-only/isolated staging. APP_ROOT and
  DATA_ROOT remain separate, the core builder is dry-run/`execution=not_run`,
  and the installer builder refuses before creating output when the reviewed
  tag/version identity is absent. No production `distribution`, registry,
  PATH, installer, portable image, upgrade or rollback was mutated. The gate
  remains BLOCKED only on user-approved V8 release identity and the separately
  authorized activation package.
- After this tracked handoff commit, all PASS reports must be regenerated under
  a new exact-head `Temp/V8_Acceptance/<final-head>/` bundle. The verifier must
  report `source_commit_matches=true` and `reports_verified=true`; no report
  digest may be edited manually. The final matrix is expected to be 9 PASS and
  3 BLOCKED (`webview2_product_ux`, `packaging_upgrade`, `crash_recovery`),
  subject to the exact-head verifier and GitHub Actions on the new commit.

## 8. Real component lifecycle — PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

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

## 9. Loopback API / Desktop / packaging — PARTIAL/HISTORICAL - SEE LATEST CHECKPOINT

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

## 16. Final technical release-candidate checkpoint — handoff-bound exact HEAD

Checkpoint này đóng CI correctness và local technical handoff; nó không kích
hoạt release. Source fix duy nhất sau `afa08bd` là projection artifact durable
trong `src/ui/shared/rendering.js`, kèm regression trong
`tests/test_v5_job_recovery_artifact_ui.py`. Jobs UI giờ dùng cùng sanitizer
bounded cho record durable và hiển thị đúng opaque preview/open controls.

- Final source SHA is always the branch tip reported by the exact-head verifier
  and the latest GitHub Actions pair; source-fix parent là
  `9abcb290d70bb6549740dbfbb17b48674348db81`, source baseline là `afa08bd`.
- Windows filesystem re-audit: 7/7 adversarial tests PASS, gồm child-process
  zero-share, root/ancestor replacement, nested reparse, foreign producer và
  injected ENOSPC. AV/indexer và physical-volume-full vẫn được đại diện bằng
  invariant task-owned; không disable security hay fill disk.
- WebView2: artifact được register qua production V8 transaction/output bridge
  từ task-owned data root; Jobs UI hiển thị artifact durable; WebView2 UIA đã
  mở preview video, gọi open-artifact shell, kiểm tra Enter/Escape và
  Tab/Shift+Tab focus, vi/en/zh/ja/ko locale, DPI 120 với resize 980/760 và
  bounded API-down error. UIA evidence ở `Temp/V8_WebView2_UX_afa08bd/`.
- Crash matrix: subprocess `os._exit(23)` chạy output reserve/producer/staged/
  authorized/committed/copy, component journal, bundle journal, SQLite
  before-backup/after-archive và desktop listener. Reconcile/blocked/manual
  review, archive hash/sidecar và listener release đều PASS trong
  `Temp/V8_Crash_Matrix_afa08bd/run_1787444405/crash_matrix.json`.
- Packaging candidate chỉ là staging local, chưa publish: version `8.0.0`,
  intended tag `v8.0.0`, `published=false`, `release_activation=false`,
  `real_tag_created=false`. ZIP:
  `Temp/V8_Candidate_<final-head>/candidate_8.0.0-final/artifacts/LocalAIHub-Core-Win64-v8.0.0.zip`,
  55,079,626 bytes, SHA-256
  `ef6e596f25aa5e7a9f697822309c39cb16c651ac6e806c48c12da6b1a7c2911d`.
  Candidate validation chứng minh APP_ROOT/DATA_ROOT separation, portable
  launch, task-owned shortcut, upgrade, rollback và uninstall/reinstall giữ
  nguyên Config/Output/Models/Projects sentinel. Metadata và inventory nằm
  trong cùng task-owned candidate staging; không đụng canonical distribution,
  registry, PATH, user shortcut hay user data.
- Focused UI 8 PASS, full V8 discovery 90 PASS, Windows suite 7 PASS, crash /
  component / bundle / SQLite support 26 PASS, JS syntax và `ci_validate.py`
  PASS (743 tracked files). Source-only provenance với candidate input
  `8.0.0/v8.0.0` trả `candidate_available`, writes=false.
- Exact-head evidence phải nằm tại
  `Temp/V8_Acceptance/<final-head>/evidence.json` và được regenerate sau
  commit handoff; verifier phải trả
  `source_commit_matches=true`, `reports_verified=true`, 12/12 PASS. Chỉ
  `V8_RELEASE_IDENTITY_APPROVAL_REQUIRED` còn lại; `candidate_version/tag`
  trong tracked release policy vẫn null và product version vẫn 7.1.0.
- Cặp GitHub Actions mới nhất trên exact final HEAD (push và PR) phải đều
  `success`; các run ID/URL cuối cùng được ghi trong PR #102 và báo cáo bàn
  giao. Không coi vòng này hoàn tất nếu SHA của run, evidence và branch tip
  không trùng nhau.

Release-only approvals vẫn cần người dùng: chọn/đổi product version, tạo
thật tag `v8.0.0`, activation/build/publish release, và merge PR #102 vào
`main`. PR #102 giữ OPEN+DRAFT; V8 không mở tag/release trong checkpoint này.

## 17. V8 release activation preflight — identity prepared, activation STOPPED

Section này là handoff hiện hành cho vòng release-activation preparation; các
ghi chú ở Section 12–16 về trạng thái **trước khi chọn identity** chỉ còn là
lịch sử. Vòng này không tạo tag thật, không merge `main` và không publish
GitHub Release.

- Starting exact source: `409e4f675c00283a7b8bcfc65e08ba8965b4d096` trên
  `feature/local-ai-hub-v8`; origin/main không đi trước và không có divergence.
- Identity preparation commit: `768b9d0ce61ee8a9a2116b5eaf412daf8ad6af8c`,
  `chore(v8): prepare 8.0.0 release identity`.
- Release-candidate handoff commits include
  `0401157aec4a9d6e539f764d3973ad90a2b4552e`; the exact final tip is always
  read back from the branch and is never hard-coded into evidence paths.
- Tracked identity files: `src/shared/version.py` (`PRODUCT_VERSION=8.0.0`),
  `architecture/v8_release_policy.json`
  (`candidate_version=8.0.0`, `candidate_tag=v8.0.0`,
  `approval.identity=approved`) và hai V8 provenance/acceptance tests đồng bộ
  với policy mới. Historical V7 manifest/tag evidence không bị sửa.
- Provenance dry-run ở identity HEAD: `contract_valid=true`,
  `product_version_matches=true`, `tag_available=true`,
  `execution=not_run`, `writes_performed=false`; không có blocker kỹ thuật.
- Exact-head evidence is always at
  `Temp/V8_Acceptance/<final-head>/evidence.json`; the latest verifier must
  return `source_commit_matches=true`, `reports_verified=true`, `passed=12`,
  `pending_gates=[]`. The rebind metadata records that release-identity/docs
  changes do not claim a physical gate rerun.
- Candidate unpublished is always rebuilt under
  `Temp/V8_Candidate_<final-head>/` from the final branch tip, never reused
  from an older HEAD. The current deterministic candidate is
  `LocalAIHub-Core-Win64-v8.0.0.zip`, 55,079,627 bytes,
  SHA-256 `6d49296083c0d517ecdfab1096bcf72b2d6669186c233e9d055876d3f5f08fd2`,
  metadata `published=false`, `release_activation=false`,
  `real_tag_created=false`. Portable launch (Python 3.12), task-owned shortcut,
  upgrade, rollback và uninstall/reinstall preservation đều PASS; Config,
  Output, Models, Projects sentinel không bị thay đổi.
- V8 Windows full suite: 90 PASS; `scripts/ci_validate.py`: 743 tracked files,
  no forbidden artifacts/unmasked secrets. No further tracked handoff commit is
  expected before the irreversible activation decision; if one is required,
  rebuild candidate and regenerate evidence again from its exact final tip.
- Remaining irreversible operations, chưa được thực hiện: real tag
  `v8.0.0`, merge PR #102 vào `main`, GitHub Release publish. PR #102 vẫn
  OPEN+DRAFT; `PLAN.md` vẫn untracked và không được đưa vào commit.

## 18. V8.0.1 stable product shell remediation — preparation only

Starting source is the immutable V8.0.0 merge commit `c98b8388`. Work is
isolated on the dedicated `fix/v8.0.1-stable-product-shell` branch; the
canonical checkout and its untracked `PLAN.md` remain untouched. This pass
does not create `v8.0.1`, change `main`, repair the user's Desktop/Start Menu
links, or publish a release.

Tracked product identity now prepares `8.0.1` / `v8.0.1`, while approval state
remains `required` until a separate user-authorized release activation. V7
release manifests/tags are historical and unchanged. The installed shell
contract is documented in `docs/operations/V8_0_1_STABLE_PRODUCT_SHELL.md`.

Implemented source contracts:

- stable windowed `LocalAIHub.exe` bootstrap with AppUserModelID
  `LocalAIHub.Desktop`, bundled-runtime-only launch, bounded product,
  installation and current-pointer manifests, full no-reparse chain checks,
  manifest-hash verification and atomic pointer activation;
- separate installed `APP_ROOT` / persistent `DATA_ROOT` resolution, dynamic
  storage/config authority and path-free storage projections;
- typed owned-job close verification (`unknown` never becomes a fake count),
  truthful cancel availability, and readiness topbar state derived from the
  same API status source;
- canonical `LA` SVG plus deterministic 16/24/32/48/64/128/256 ICO, shared by
  shortcut/installer/tray/WebView paths where the host API supports it;
- development-only Temp data launcher, stable-only shortcut repair, staged
  versioned payload builder, and installer code that writes installation
  configuration at install time while preserving persistent data.

Bounded evidence on the private branch:

- focused stable-shell/storage/close/provenance suite: 14 PASS;
- V8 suite excluding the occupied fixed-port loopback test: 102 PASS;
  the complete 103-test discovery has one environment failure because another
  user-owned `pythonw.exe` already listens on `127.0.0.1:8765`; it was not
  stopped or altered;
- startup, lifecycle, storage, UI, shortcut, and compatibility subset: 88
  PASS; JS syntax and Python compilation PASS; `scripts/ci_validate.py` PASS
  with 743 tracked files and no unmasked secrets/forbidden artifacts;
- task-owned launcher build completed as a validation executable (8,207,698
  bytes, SHA-256
  `ebad247cb37c9ed2add74a46d791d60b4d1edefedbbe6773af8eefdf27542d72`). A
  staged candidate layout and inventory were validated with `execution=not_run`;
  no official package or production runtime claim is made because the reviewed
  portable Core runtime is not installed in the repository data roots.

Release provenance source-only validation reports `contract_valid=true` and
`product_version_matches=true`; the only release blocker is the explicit
`V8_RELEASE_IDENTITY_APPROVAL_REQUIRED`. No V8.0.1 tag, shortcut migration,
installer run, GitHub release, GPU/model/provider workload, or user-data
operation was performed. Before delivery, rerun the final bounded gates,
commit/push the branch and open a DRAFT PR; stop before any production
 shortcut repair or irreversible release action.

## 19. V8.0.1 real product installation closure — installed, shortcut repaired, smoke blocked

This checkpoint continues the stable-shell branch from the V8.0.0 merge
without changing `main`, creating `v8.0.1`, or publishing a release. The
dedicated branch reached the final source tip recorded by Git after the
installation-handoff commit; the exact SHA is read back from the branch and
is not inferred from this document.

- The loopback harness now chooses a task-owned free loopback port while the
  production default remains `8765`; the complete V8 discovery passed **105
  tests** despite a pre-existing listener on `127.0.0.1:8765`.
- The candidate uses the official CPython `3.12.10` Windows x64 embeddable
  runtime from `python.org`, archive SHA-256
  `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`,
  license `PSF-2.0`, and a deterministic PyPI wheelhouse manifest. The
  embedded runtime has `PYTHONNOUSERSITE=1`, a bounded internal `../../app`
  path, and imports `webview`/`mcp` with an empty `PATH`.
- The final task-owned candidate was rebuilt from the branch tip with 3,565
  files and a bundled runtime. The stable launcher SHA-256 is
  `4509019776122ccfedd5a9d5ffec4d8ed0e0d2b57281eb9a0a69dae3c37838c1`.
  Isolated API launch returned `healthy`, version `8.0.1`, `active_jobs=0`;
  simulated `8.0.1 -> 8.0.2-testpayload -> 8.0.1` pointer update/rollback
  preserved the launcher SHA and removed only the task-owned fake payload.
- The one-time per-user installation was materialized at
  `%LOCALAPPDATA%\Programs\LocalAIHub` with `DATA_ROOT=D:\LocalAIHub`.
  Desktop and Start Menu now contain `Local AI Hub.lnk`; both target the
  stable EXE, have empty arguments, the installed working directory, and
  `LocalAIHub.exe,0` as the embedded LA icon. Before the repair no matching
  shortcut existed; bounded before/after metadata is preserved under the
  task-owned Temp evidence area.
- GitHub push and PR validation both passed on the exact final source tip
  after each source fix. The DRAFT PR remains open.

The production shortcut smoke is **BLOCKED**, not a PASS: an already-running
user-owned process `pythonw.exe -m src.services.api.api_server` from the
system Python installation owns `127.0.0.1:8765` (PID was preserved). The
installed shortcut launches the bundled `LocalAIHub.exe` and bundled
`pythonw.exe`, but the application reuses the pre-existing API listener, so
the observed `/health` response is version `7.1.0`, not a self-contained
`8.0.1` API. Codex did not stop or modify that process. No production
self-contained/runtime-readiness claim is valid until the user closes or
otherwise authorizes handling of that external listener and the shortcut
smoke is rerun. No tag, main merge, release publish, GPU/model/provider
workload, or user-data relocation was performed.

The later exact-head runtime-bundle remediation (commit `2839a93`) excludes
pip-generated `Lib/site-packages/bin` console helpers whose shebang metadata
contained a developer system-Python path. Its rebuilt candidate contains
3,549 files and 3,044 runtime files with no such path match. The already
materialized external installation is intentionally not destructively cleaned
or replaced-for-removal in this checkpoint; the candidate remediation is
ready for a separately authorized reinstall/cleanup decision.

## 20. V8.0.1 API identity and self-contained fallback remediation

This checkpoint records the authorized remediation of the occupied-default-port
blocker without stopping or reusing the user-owned API process. The source fix is
committed on `fix/v8.0.1-stable-product-shell` and the final branch SHA is always
read back from Git rather than copied into evidence.

- The API health contract now exposes bounded identity fields: product ID and
  version, API protocol, opaque installation ID, AppUserModel ID, process owner,
  and the actual loopback bind. No local paths, secrets, or raw process details
  are returned.
- Startup classifies absent, compatible same-install, LocalAIHub-incompatible,
  and foreign HTTP listeners. It never treats an arbitrary HTTP 200 or a 7.1.0
  API as compatible with 8.0.1, and it never terminates a foreign listener.
- When the default port is occupied by an incompatible/foreign listener, the
  bundled API selects a free loopback session port, passes the selected port and
  bind through the child environment, and the UI displays the actual endpoint.
  The session port is not persisted as installation state and the mutex is
  scoped to installation plus selected port.
- Focused identity/collision tests and the complete V8 discovery suite pass on
  the source-fix tip; push and PR workflows passed on that exact tip. The
  production shortcut smoke was rerun while the preserved external listener
  remained alive: bundled API health reported product 8.0.1, protocol v8-api.v1,
  the bundled process owner and a fallback loopback port; close released only
  the owned listener and left the external process unchanged. A second-instance
  check produced one owned API and no duplicate listener.
- The installed payload was rebuilt from the source-fix candidate and stale
  system-Python helper files were removed only after exact old-manifest/hash
  comparison. Unknown generated cache files were preserved. The persistent data
  root remains `D:\LocalAIHub`; the simulated 8.0.1 to test payload to 8.0.1
  pointer rollback is complete and the task-owned test payload is gone.
- Desktop and Start Menu shortcuts remain immutable and point to the stable
  installed executable with no arguments and the embedded application icon.
  No main/tag/release operation or model/GPU/runtime workload was performed.

The tracked handoff commit itself is the source of the next exact-head evidence
binding. Candidate packaging, acceptance metadata, CI readback and PR status
must use the final branch SHA after this commit; historical evidence remains
labelled with its original source commit and is not rewritten.

## 21. V8.0.1 final release-readiness policy audit

The V8 release policy keeps `release_branch=feature/local-ai-hub-v8` as
generation/integration-line metadata. The provenance verifier does not compare
that value with the current checkout branch and does not authorize a candidate
from a branch-name match. The current candidate is intentionally prepared on
`fix/v8.0.1-stable-product-shell`.

Trust binding is exact and branch-independent: acceptance evidence and the
candidate manifest bind the source commit/tree; post-tag provenance binds the
peeled immutable tag target to the approved release commit; and a wrong tag
target is rejected. A regression proves that an approved exact commit is
accepted from a temporary review branch while the wrong-target tests remain
fail-closed.

The user-approved preparation identity is now tracked as `8.0.1` / `v8.0.1`
with `approval.identity=approved`. This does not authorize tag creation, main
merge, or release publication. Historical V7/V8.0.0 release evidence remains
immutable. Final pre-tag provenance is expected to be activation-ready with no
blockers once exact-head acceptance is rerun.

## 22. V8 release phase-governance correction — integration is tag-independent

The release verifier now treats `integration` as its default phase. It validates
the tracked source/product contract and exact commit without reading or requiring
any tag, including historical or unrelated tags. `release_branch` remains the
V8 generation/integration-line metadata and is not current-checkout or release
authorization authority.

`pre_tag` and `post_tag` require an explicit version, tag, and expected commit:
pre-tag refuses an already-existing requested tag, while post-tag requires the
existing tag to peel to the exact expected commit. Candidate version and planned
tag are intentionally decoupled in tracked policy, so a version can be prepared
before a user selects a tag. Acceptance results expose separate
`technical_ready`, `merge_ready`, `release_ready`, and `tagged_release_ready`
fields; a normal integration pass never implies a tagged release. Historical
V7/V8.0.0 tags/evidence remain immutable.

## 23. V8.0.1 installed startup payload/runtime hotfix

Real shortcut reproduction exposed a P0 split-install defect that unit tests
with mocked readiness did not cover. The desktop compared API identity using
the version payload app root while the API used the stable installation root;
installed startup also allowed legacy/system Core Python selection, leaked an
owned child when readiness failed, and wrote startup logs below the mutable
payload. The corrective contract keeps `LocalAIHub.exe`, shortcuts and
`DATA_ROOT` stable while repairing only the active version payload: identity is
installation-root plus data-root, installed mode uses the validated bundled
runtime from `current.json`, every failed owned startup is reaped, and bounded
diagnostics go to the data-root Logs authority. Full source tests, a real
split-install smoke and exact-head CI are required before payload repair.
