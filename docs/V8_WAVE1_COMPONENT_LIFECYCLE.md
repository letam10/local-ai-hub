# Local AI Hub V8 Wave 1 — Durable Component Lifecycle

Wave 1 đặt Component Lifecycle lên transaction journal của Wave 0 mà không viết lại các Model/Runtime/Component Installer đã được kiểm chứng ở V7. Mục tiêu là tạo một authority tuần tự, opaque và có thể audit cho mỗi thao tác component.

## Kiến trúc

```text
UI/API ở wave sau
→ ComponentLifecycleCoordinator
→ opaque operation_id
→ V8TransactionStore
→ existing ComponentInstaller
→ ModelManager / RuntimeManager / Receipts / Verification
```

`src/services/component_lifecycle_v8.py` không nhận raw path, URL, command hay executable. Nó tạo operation từ một server-owned plan đã được ComponentInstaller sinh ra và lưu tuple `operation_id + plan_id + component_id + component_type + action + expected_state_fingerprint` trong SQLite.

## State machine

Operation dùng finite states:

```text
planned
→ executing
→ verifying
→ committed

planned/executing/verifying
→ blocked | failed | cancelled
```

`committed` chỉ có nghĩa lifecycle transaction đã hoàn thành theo executor. Nó **không đồng nghĩa OPERATIONAL**. Model/runtime vẫn phải tuân theo receipt và bounded runtime evidence của V7; kết quả `INSTALLED_UNVERIFIED` được giữ nguyên, không tự nâng thành operational.

## Confirmation và stale authority

Planning không chạy download/install. `confirmed=False` giữ operation ở `planned`. Khi xác nhận, coordinator chỉ dùng exact `plan_id` đã journal; nếu plan in-memory không còn tồn tại sau restart thì operation được chuyển `blocked/plan_session_lost`, không tự tái dựng một plan mới từ dữ liệu cũ.

Một operation terminal không được execute lần hai. Unavailable/conflict từ catalog/source/dependency trở thành `blocked`; executor failure trở thành `failed`.

## Phạm vi lifecycle được bọc

Wave 1 hỗ trợ journal/coordinator cho:
- install;
- verify;
- existing-install reuse;
- repair;
- update;
- uninstall.

Manual Import vẫn dùng native selection/opaque `selection_id` của V7. Việc journal hóa import được defer tới package API/Desktop wiring vì selection token là process-local và cần acceptance trên picker thật.

## Những gì Wave 1 không làm

Wave 1 không thay API routes/UI hiện tại và không tự gọi real install trong acceptance của source package. Không tải model/runtime, không gọi provider, không chạy GPU/inference/FFmpeg, không đổi CUDA/driver, không chạy server/browser và không merge main.

Production activation cần package tiếp theo để:
1. migrate V7 Job/Artifact callsites sang Wave 0 authority;
2. wire ComponentLifecycleCoordinator vào server composition/API;
3. chạy Windows filesystem/reparse acceptance;
4. chạy clean-machine component install/reuse/update/rollback/repair acceptance;
5. sau đó mới bật từng AI component `AUTO_INSTALL_READY` có đủ source/license/hash/runtime evidence.
