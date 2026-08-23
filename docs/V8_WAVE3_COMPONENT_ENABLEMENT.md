# V8 Wave 3 — Component Enablement Control Plane

Status: **SOURCE/CONTROL-PLANE IMPLEMENTED; REAL WINDOWS LIFECYCLE ACCEPTANCE PENDING**.

Wave 3 không tuyên bố model/runtime đã cài được trên máy thật. Mục tiêu của wave này là khóa contract trước khi cho phép bất kỳ real download/install nào.

## Wave 2 re-audit closure

`src/services/artifact_access_v8.py` hiện yêu cầu hai quyền độc lập trước khi publish worker output:

1. V8 reservation phải tồn tại cho đúng `job_id`.
2. V7 bounded output scope phải chứng minh chính candidate batch là `owned` ngay tại publication boundary.

Reservation không còn được hiểu nhầm là ownership proof. Candidate đã tồn tại trước snapshot, snapshot không đầy đủ hoặc candidate không hợp lệ đều bị từ chối publication; ambiguous file không bị xóa.

Re-audit cũng sửa compatibility byte/atomic publication: public artifact giữ đúng caller-facing `name` và explicit `media_type`; private producer filename chỉ là implementation detail và không đi vào public metadata.

## Durable import and bundle operations

`ComponentLifecycleCoordinator` journal hóa thêm hai action:

- `import`: bọc V7 native/manual-import plan bằng opaque V8 `operation_id`.
- `bundle`: bọc V7 composite bundle plan bằng opaque V8 `operation_id`.

Mỗi action vẫn cần explicit confirmation, CAS state transition và terminal operation không chạy lại. `plan_fingerprint` được dùng làm durable binding fingerprint khi V7 plan không có `expected_state_fingerprint` riêng.

Giới hạn có chủ ý:

- native `selection_id` và selected local path vẫn process-local; restart làm mất V7 plan và V8 operation fail closed bằng `plan_session_lost`;
- bundle execution hiện vẫn là ordered V7 child execution, chưa có atomic multi-component rollback;
- planned operation có thể cancel durable; operation đang executing không giả lập cancellation nếu process-local executor không chứng minh được ownership/cancel handle.

## Direct operation API

Wave 3 bổ sung các route path-free:

- `GET /api/components/operations`
- `GET /api/components/operations/{operation_id}`
- `POST /api/components/operations/{operation_id}/confirm`
- `POST /api/components/operations/{operation_id}/cancel`

Confirmation body chỉ chấp nhận `{"confirmed": true|false}`. Cancel chỉ nhận empty JSON body và chỉ cancel operation còn ở `planned`.

## Trusted-source acceptance

`src/services/component_enablement_v8.py` đọc production catalog đã tracked và tạo finite acceptance state dựa trên:

- catalog disposition;
- primary source có/không;
- verified HTTPS source metadata;
- authentication state;
- license state;
- model/runtime integrity metadata;
- estimated download/disk size.

Public result không chứa local filesystem path, command hoặc credential. Service không gọi provider, không download, không tự chấp nhận license và không sửa catalog.

`auto_install_eligible=true` chỉ khi **tất cả** gate đều đạt và catalog đã explicit `AUTO_INSTALL_READY`. Vì production catalog hiện cố ý không cho phép model AUTO_INSTALL_READY, Wave 3 không tự nâng bất kỳ model nào.

Routes:

- `GET /api/components/source-acceptance`
- `GET /api/components/{component_id}/source-acceptance`

## Validation

GitHub Actions Repository validation run #647:

- `python scripts/ci_validate.py`: PASS; 721 tracked files; không forbidden artifact/unmasked secret.
- `python -m unittest discover -s tests -p "test_v8_*.py"`: 23/23 PASS.
- `git diff --check` với PR base: PASS.

Các test mới bao phủ:

- Wave 2 publication boundary từ chối candidate tồn tại trước reservation/snapshot;
- Wave 2 atomic byte publication giữ đúng public name/media type;
- durable import operation + no double execution;
- durable bundle operation;
- planned operation cancellation;
- tracked V2 catalog không tự promote model;
- SAM2 có verified source/integrity/size nhưng vẫn manual vì disposition chưa cho auto;
- FLUX vẫn `AUTH_REQUIRED`;
- direct V8 operation/source routes được đăng ký.

## Deferred gate

Mọi acceptance phụ thuộc Windows/local state, network/provider review, real component execution, Desktop UI, rollback và runtime/model smoke được giữ trong `Plan_Miss.md`. Không được đổi trạng thái Wave 3 thành production-accepted chỉ dựa trên Linux CI.
