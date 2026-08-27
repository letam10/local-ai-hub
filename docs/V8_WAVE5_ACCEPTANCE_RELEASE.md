# V8 Wave 5 — Acceptance Gate

Status: **SOURCE PREFLIGHT RE-AUDITED/HARDENED; WINDOWS ACCEPTANCE BLOCKED**.

Wave 5 không đồng nghĩa release. Nó tạo fail-closed acceptance gate để Codex/local QA chứng minh Windows evidence trước release activation. Linux GitHub Actions không thay thế Windows acceptance.

## Source owners

- `architecture/v8_acceptance_gates.json`
- `scripts/v8_acceptance_gate.py`
- `tests/test_v8_wave5_acceptance_gate.py`
- `Plan_Miss.md`
- `docs/architecture/V8_MIGRATION_PLAN.md`

## Required local gates

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

## Evidence bundle

Tracked source chỉ định schema, còn evidence/report thật phải nằm local và **không commit Git**.

Cấu trúc local:

```text
<acceptance-root>/
  evidence.json
  reports/
    windows_filesystem.json
    artifact_callsite_inventory.json
    ...
```

`evidence.json` dùng schema `v8-local-acceptance-evidence.v1`, class `local_windows`, platform `windows-x64`, bind exact `source_commit`, chứa đúng toàn bộ required gate IDs.

Mỗi gate `PASS` phải có `report_sha256` và file deterministic `reports/<gate_id>.json`.

Mỗi gate trong `architecture/v8_acceptance_gates.json` cũng khai báo
`required_checks`.  Report `PASS` phải có đúng tập check đã khai báo và mọi
giá trị phải là boolean `true`; một key `true` tùy ý hoặc report thiếu check
không được coi là bằng chứng.  Preflight vẫn đối chiếu digest report, source
commit và platform trước khi tính gate.

Riêng `webview2_product_ux` phải báo cáo route render, điều hướng thật,
dark/light, DPI 100/125/150 (hoặc bằng chứng không khả dụng cho từng mức),
degraded/error recovery, frontend-ready, normal close và ít nhất một đường
tương tác native/trusted.  Nếu UIA InvokePattern không dùng được, report phải
ghi rõ đường mouse/keyboard native thay thế hoặc để check đó `BLOCKED`; không
được che khuất giới hạn host.

`crash_recovery` phải có bằng chứng lỗi khởi động API/frontend hoặc process
failure cùng watchdog rollback và relaunch payload trước.  `real_component_lifecycle`
phải ghi lightweight helper đã thực sự chạy, hoặc khai báo rõ giới hạn nếu chỉ
thực hiện verify/cancel bounded mà không thể chạy helper an toàn.

PASS report dùng schema `v8-local-gate-report.v1` và phải bind:

- exact `gate_id`;
- status `PASS`;
- platform `windows-x64`;
- exact `source_commit`;
- finite `checks` object, ít nhất một check và mọi check = `true`.

Preflight tự đọc bytes report, giới hạn kích thước, tính SHA-256 và so với digest trong evidence. Chỉ khai một digest string không còn đủ để PASS.

## Read-only preflight

Source-only:

```powershell
python scripts/v8_acceptance_gate.py --source-only
```

Strict local acceptance:

```powershell
python scripts/v8_acceptance_gate.py --strict-release --evidence <acceptance-root>\evidence.json
```

Source-only có thể exit 0 khi source contract hợp lệ nhưng `merge_ready=false`; đó là trạng thái đúng nếu Windows evidence còn thiếu. Integration CI không yêu cầu tag. `release_ready` chỉ có ý nghĩa ở `pre_tag`/`post_tag`, còn `tagged_release_ready` chỉ PASS sau khi tag hiện hữu trỏ đúng exact commit.

## Wave 5 re-audit finding

Bản đầu Wave 5 chỉ yêu cầu `report_sha256` trong evidence nhưng chưa tự chứng minh local report file tồn tại và digest khớp actual bytes. Re-audit đã đóng khoảng trống này bằng deterministic report binding/hashing như mô tả trên.

## Windows filesystem gate — invariant-based acceptance boundary

The Windows filesystem gate treats the safety invariant as the product contract:
an unsafe root/ancestor/lease, a producer held by another process, a replaced
managed object, or bounded write failure must refuse publication, preserve
foreign/producer bytes, and leave no public artifact. Antivirus/indexer locks
and physical-volume exhaustion are external mechanisms, not reproducible test
inputs for this repository. They are represented by the same observable
invariants using task-owned zero-share `CreateFileW` handles (including a
separate child process) and injected `ENOSPC` at the managed-copy boundary.
The acceptance report must name those substitutions explicitly; it must never
disable Defender/indexing or fill a physical volume to manufacture PASS.

## Release provenance transition

Wave 5 không còn dùng V7 provenance như contract V8. Wave 6 thêm V8-specific read-only release policy/provenance preparation. Historical V7 release manifest/tags vẫn immutable.

Release vẫn bị khóa vì:

- 12 local Windows gates chưa có valid PASS evidence bundle;
- final V8 release identity chưa được người dùng phê duyệt;
- product version activation chưa thực hiện;
- strict release preflight chưa PASS trên exact release commit;
- main merge/tag/release vẫn cần user approval.

Chi tiết nằm trong `Plan_Miss.md` và `docs/V8_WAVE6_RELEASE_PROVENANCE_PREPARATION.md`.
