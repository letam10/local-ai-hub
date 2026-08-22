# V8 Wave 5 — Acceptance & Release

Status: **SOURCE PREFLIGHT IMPLEMENTED; WINDOWS ACCEPTANCE AND RELEASE EXECUTION BLOCKED**.

Wave 5 không được hiểu là đã release. Phần source trong wave này chỉ tạo fail-closed acceptance gate để Codex/local QA có một contract duy nhất trước khi tiến tới packaging, installer, provenance, version/tag và main merge. Linux GitHub Actions không thay thế Windows acceptance.

## Source owners

- `architecture/v8_acceptance_gates.json`
- `scripts/v8_acceptance_gate.py`
- `tests/test_v8_wave5_acceptance_gate.py`
- `Plan_Miss.md`
- `docs/architecture/V8_MIGRATION_PLAN.md`

## Gate model

Tracked contract yêu cầu evidence `local_windows`, platform `windows-x64`, bind vào đúng `source_commit`. Mỗi required gate phải có finite status và report SHA-256. Evidence manifest không nhận raw path, command, executable, credential hoặc free-form machine detail.

Required local gates:

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

## Read-only preflight

Source-only validation:

```powershell
python scripts/v8_acceptance_gate.py --source-only
```

Lệnh trên phải exit `0` khi tracked contract hợp lệ, nhưng `release_ready` vẫn phải `false` nếu thiếu local Windows evidence hoặc V8 release provenance chưa được review.

Strict local release check sau này:

```powershell
python scripts/v8_acceptance_gate.py --strict-release --evidence <LOCAL_EVIDENCE_JSON>
```

Evidence JSON phải có schema:

```json
{
  "schema_version": "v8-local-acceptance-evidence.v1",
  "evidence_class": "local_windows",
  "platform": "windows-x64",
  "source_commit": "<exact git commit>",
  "gates": {
    "windows_filesystem": {"status": "PASS", "report_sha256": "<sha256>"}
  }
}
```

Thực tế file phải chứa **đúng toàn bộ required gate IDs**, mỗi gate `PASS` phải có SHA-256 của report local tương ứng. File evidence không commit vào Git.

## Release provenance blocker hiện tại

V8 branch hiện vẫn kế thừa release contract V7:

- `src/shared/version.py` vẫn là product `7.1.0`;
- `scripts/verify_release_provenance.py` vẫn review branch `feature/v7-operational-closure`;
- intended tag vẫn thuộc `v7.*`;
- `distribution/release_manifest.json` là historical V7 manifest.

Đây là blocker **cố ý**. Wave 5 source preflight không tự đổi version/tag/branch provenance để làm gate xanh. Chỉ khi Windows acceptance đủ evidence và người dùng phê duyệt release package riêng mới được thiết kế V8 version/tag/provenance rồi build artifact.

## Wave 4 re-audit carried into Wave 5

Trước khi mở preflight, Wave 4 source được siết thêm:

- Components UI không còn mặc định metadata `component_type` lạ thành `model`; invalid public component type/action bị loại fail-closed.
- mount của V8 control-plane có idempotence guard.
- `REFERENCE_EXISTING` hiển thị `Use Existing`, `MANUAL_INSTALL` hiển thị `Manual Install` thay vì fallback `Manual Review`.

Những sửa này chỉ là source correctness; WebView2/5-language/focus/keyboard acceptance vẫn phải chạy trên Windows thật.

## Không được làm trong source preflight

- không merge `main`;
- không tạo/move tag;
- không bump product version;
- không sửa historical release manifest thành V8 giả;
- không build/publish installer;
- không chạy provider request, model download, GPU inference hoặc FFmpeg workload;
- không commit machine-local acceptance report/evidence.

Chi tiết phần local chưa thể xử lý từ GitHub nằm trong `Plan_Miss.md`.
