# V8 Wave 4 — Windows Real Lifecycle & Product UX

Status: **SOURCE-SIDE PRODUCT UX IMPLEMENTED; WINDOWS REAL-LIFECYCLE ACCEPTANCE PENDING**.

Wave 4 có hai nửa khác nhau và không được đánh đồng:

1. **Product UX / API composition** có thể triển khai và kiểm tra từ source repository.
2. **Windows Real Lifecycle** phụ thuộc NTFS, local machine state, pywebview/WebView2, installer, runtime/model thật và phải nghiệm thu trên máy Windows kiểm soát.

Linux GitHub Actions chỉ chứng minh nửa thứ nhất.

## 1. Wave 3 re-audit closure

Re-audit Wave 3 phát hiện và sửa hai contract source-level.

### 1.1 `REFERENCE_EXISTING` là disposition có thẩm quyền

Một runtime `REFERENCE_EXISTING` không phải là yêu cầu Hub tải/cài lại runtime đó. Vì vậy source acceptance không được xếp nó thành `LICENSE_REVIEW_REQUIRED`, `SOURCE_UNAVAILABLE`, `INTEGRITY_INCOMPLETE` hoặc `SIZE_UNKNOWN` chỉ vì metadata download không tồn tại.

`ComponentEnablementService` hiện xử lý thứ tự:

1. unsupported source;
2. `REFERENCE_EXISTING`;
3. auth/license/source/integrity/size gates cho các disposition thực sự có installation flow;
4. `AUTO_INSTALL_READY` chỉ khi catalog đã explicit cho phép và toàn bộ gate đều đạt.

Không model nào được auto-promote bằng suy diễn.

### 1.2 Route adapter không được tự tạo domain manager

Các route V8 Components trước đây gọi trực tiếp module-level `component_api` singleton. Wave 4 chuyển toàn bộ operation/source-acceptance route sang `ApiContext.call(...)`, đúng dependency direction của API architecture:

`HTTP route -> ApiContext -> application-owned service binding`.

Route list operation cũng chỉ chấp nhận `limit=1..500`. Confirmation mất process-local plan trả conflict HTTP 409 thay vì giả thành công.

## 2. Source-side Components Product UX

Components page hiện có V8 control-plane surface riêng nhưng vẫn giữ legacy plan UI để compatibility.

### Operation journal

UI đọc bounded recent operations qua:

- `GET /api/components/operations?limit=12`
- opaque `compop_*` ID;
- finite state: `planned`, `executing`, `verifying`, `committed`, `failed`, `blocked`, `cancelled`.

Chỉ operation `planned` mới hiển thị action confirm/cancel. Confirm vẫn cần user interaction và server vẫn quyết định plan có còn hợp lệ hay không.

### Source acceptance

Mỗi component hiển thị:

- acceptance state;
- catalog disposition;
- auto-install eligibility;
- source/auth/license/integrity/size requirement summary;
- server-provided next action.

UI không nhận hoặc hiển thị local filesystem path, command, executable hay credential.

### Refresh behavior

Control plane **không polling**. Refresh chỉ xảy ra khi Components page được render, navigation quay lại Components hoặc người dùng bấm refresh. Không có `setInterval`.

## 3. Những gì Wave 4 source-side KHÔNG tuyên bố

Source changes này không chứng minh:

- native picker an toàn trên NTFS thật;
- WebView2 thực sự render/focus/keyboard đúng trên máy người dùng;
- loopback API chạy ổn định qua restart/concurrency;
- installer/shortcut/upgrade preservation;
- component download/install/update/rollback/repair/uninstall thật;
- bundle rollback atomic;
- executing-operation cancellation;
- SQLite backup/restore dưới transaction/crash;
- runtime/model/GPU inference.

Các gate này được giữ trong `Plan_Miss.md` để Codex local thực hiện.

## 4. Validation source-side

GitHub Actions Repository validation run **#667**:

- `python scripts/ci_validate.py`: **PASS**, 723 tracked files, không forbidden artifacts hoặc unmasked secrets;
- `python -m unittest discover -s tests -p "test_v8_*.py"`: **27/27 PASS**;
- `git diff --check` với PR base: **PASS**.

Wave 4 tests mới chứng minh:

- `REFERENCE_EXISTING` không bị biến thành download/license gate;
- operation list route dùng injected `ApiContext` và bounded limit;
- `plan_session_lost` ở direct operation confirm trả HTTP 409;
- V8 Components module được mount;
- product UI có operation/source-acceptance surfaces;
- source-side V8 UX không polling và không chứa các raw-path field đã bị cấm.

## 5. Acceptance gate tiếp theo

Wave 4 chỉ chuyển sang **Windows real-lifecycle accepted** sau khi Codex/local QA hoàn thành toàn bộ các mục tương ứng trong `Plan_Miss.md` và lưu evidence phù hợp. Không được đổi trạng thái chỉ vì Linux CI PASS.

Không merge `main`, không tạo/move tag, không release và không chạy model/GPU/download lớn như một hệ quả tự động của Wave 4 source work.
