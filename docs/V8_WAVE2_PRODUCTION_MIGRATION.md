# V8 Wave 2 — Production Callsite Migration

Status: **SOURCE MIGRATION IMPLEMENTED; WINDOWS ACCEPTANCE DEFERRED**.

## Mục tiêu

Wave 2 chuyển quyền publish output mới từ V7 Artifact Store sang V8 Output Authority mà không phá artifact/upload lịch sử. V7 output scope vẫn được giữ tạm thời để chứng minh producer candidate không phải file có sẵn; nó không còn là publication authority cho worker output mới.

## Kiến trúc

`V7 Job Manager → V7 ownership scope → V8 reservation → producer → V8 copy-once object → transaction authorize → final public commit`.

Compatibility read path: `V8 public object first → V7 historical artifact/upload fallback`.

Durable jobs import `V8DurableWorkEngine`; `LegacyDurableWorkEngine` vẫn có tên rõ ràng cho compatibility/forensic tests. Production durable execution phải reserve trước adapter; failure/interruption abort reservation. Managed producer publication và Hub-produced byte publication đều đi qua V8 bridge.

Component API giữ route/plan-id compatibility nhưng install/verify/reuse/repair/update/uninstall được journal bằng opaque `operation_id` trước confirmation. Native manual import và composite bundle vẫn deferred vì selection/bundle contract chưa có durable V8 operation tương ứng.

## Audit Wave 0–1 trong Wave 2

Wave 0 invariants được giữ: fixed roots, reparse refusal, exact transaction/reservation/job binding, staged private, copy-once object, final SQLite public commit, exact-identity cleanup. Audit phát hiện aborted staged row có thể tồn tại sau khi object đã được dọn; production extension đã thêm exact aborted-row cleanup để reconciliation hội tụ thay vì lặp `manual_review`.

Wave 1 invariants được giữ: server-owned V7 plan executor, opaque durable V8 operation, explicit confirmation, finite CAS transitions, no terminal re-execution, restart fail-closed, `committed != OPERATIONAL`. Wave 2 thêm plan-id → operation-id compatibility trong Component API.

## Gate

Source gate:
- `python scripts/ci_validate.py`
- `python -m unittest discover -s tests -p "test_v8_*.py"`
- `git diff --check <PR-base> HEAD`

Production gate chưa được tuyên bố đạt cho đến khi Codex chạy các mục Windows/local trong `Plan_Miss.md`.

Không chạy model/runtime download, GPU inference, FFmpeg workload, browser/server acceptance hoặc release/tag trong Wave 2 source migration.
