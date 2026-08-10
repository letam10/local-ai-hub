# MILESTONE 6A — Image & Mask Studio không phá hủy

## Mục đích và ranh giới

M6A bổ sung một workspace **Image & Mask Studio** trong Image AI để người dùng
tổ chức thao tác chỉnh sửa ảnh theo cách có thể khôi phục và tái lập ở mức
metadata:

```text
Artifact ảnh Hub → session Studio → layer / mask vector / adjustment
                → autosave + undo/redo + snapshot + preset
                → compare metadata → Project provenance
```

Studio không phải renderer ảnh, backend inpaint/outpaint, SAM2 runtime hay job
queue. Nó không decode/rasterize pixel, không tạo mask PNG, không ghi đè ảnh
nguồn, không copy/xóa media và không tự khởi chạy model/GPU. Mọi pixel thuộc
Artifact Store; session Studio chỉ giữ mô tả layer bounded và opaque ID của
artifact Hub đã tồn tại.

Khi cần một mask PNG hoặc output pixel mới, người dùng phải dùng một công cụ đã
được ủy quyền, để công cụ đó đăng ký/tải output qua Artifact Store, sau đó gắn
`artifact_<opaque-id>` kết quả vào một layer Studio. Sự tồn tại của session,
mask metadata hay preset không phải bằng chứng inference đã chạy.

## Trải nghiệm trong một cửa sổ

Image AI có tab Studio với các trạng thái loading, empty, error/recovery và
preflight dùng cùng visual language của Hub. Luồng thao tác bình thường:

1. Chọn một **image artifact** đã có từ Asset Library hoặc upload ảnh qua
   Artifact Store streaming. Studio nhận ID opaque sau khi upload hoàn tất,
   không đọc path máy từ trình duyệt.
2. Tạo session không phá hủy; source layer tham chiếu ảnh gốc. Có thể chọn
   Project ngay lúc tạo hoặc liên kết sau.
3. Thêm mask, adjustment hoặc generated layer đã đăng ký. Cập nhật tên, độ mờ,
   visibility và thứ tự stack; gỡ layer chỉ xóa reference của session.
4. Với mask layer, vẽ brush `add`/`subtract` trên canvas. Canvas chuyển pointer
   thành tọa độ chuẩn hóa 0–1, compact/bound số điểm rồi gửi JSON qua loopback.
   `Esc` hủy nét đang vẽ; nét hủy không tạo mutation.
5. Dùng `invert`, `feather`, `grow`, `shrink`, undo/redo, explicit save,
   snapshot, compare trước/sau hoặc preset. Những thao tác này lưu metadata,
   không sửa artifact nguồn.
6. Export/import manifest mask đã validate, hoặc liên kết artifact thuộc session
   vào Project để giữ lineage/provenance tái lập.

Form/canvas có focus/ARIA status; UI phải hiển thị reason/action từ preflight
thay vì bật một nút runtime chưa được chứng minh.

## Ownership và luồng dữ liệu

```text
Browser UI
    │  opaque IDs + JSON bounded
    ▼
API loopback /api/image-mask-studio/*
    │
    ├── ImageMaskStudioManager ── state/snapshot/preset local
    │          │
    │          ├── Artifact Store ── public image record / artifact URL
    │          └── Project Manager ── idempotent asset reference + provenance
    │
    └── không có shell, path máy, pixel buffer hay job/model call
```

| Thành phần | Sở hữu | Không sở hữu |
| --- | --- | --- |
| `src/services/image_mask_studio/schemas.py` | Opaque ID, layer/mask/adjustment allowlist, validation và contract version | Path, data URL, pixel bytes, executable, command |
| `src/services/image_mask_studio/manager.py` | Session, local autosave, history, snapshot, preset, compare và recovery | File media, renderer, model hoặc queue job |
| `src/services/image_mask_studio/config.py` | Policy/limits local nhỏ và trạng thái preflight SAM2 bảo thủ | Model path/name, backend URL, secret hoặc cờ ép `operational` |
| `src/services/artifact_store.py` | Artifact file/index canonical, upload và public opaque record | Layer stack, brush history, Studio preset |
| `src/services/project_manager/manager.py` | Reference Project/Asset và provenance M6A idempotent | Canvas/state đầy đủ, pixel hoặc manifest tự do |
| `src/services/api/api_server.py` | Mapping HTTP path-safe, conflict và bridge hai bước Studio → Project | Shell/direct filesystem hoặc logic UI |
| `src/ui/image_mask_studio.js` | Chuẩn hóa/compact pointer strokes và lifecycle listener | Vẽ/rasterize/upload pixel |

## State, policy và recovery

`limits.max_state_bytes` mặc định 8 MiB và được normalize tối đa 64 MiB. Đây là
byte ceiling trước parse: manager kiểm tra regular-file/identity/size/mtime,
đọc tối đa trần + 1 byte, rồi fail-closed khi file quá lớn, bị thay thế hoặc
thay đổi trong lúc đọc. `NaN`, `Infinity` và `-Infinity` bị từ chối ở API,
metadata và state; file lỗi vẫn nguyên vẹn để người dùng recovery an toàn.
Trước atomic replace, writer đếm byte từ `JSONEncoder.iterencode`; payload vượt
trần bị từ chối và file state cũ vẫn được giữ nguyên.

Ba tệp có ownership khác nhau; chúng không được dùng thay cho nhau:

| Tệp | Contract/nội dung | Vòng đời | Git |
| --- | --- | --- | --- |
| `Config/image_mask_studio.example.json` | `image-mask-studio-config.v1`, default limits và `sam2_assist.configured: false` | Mẫu được track để setup máy | Track |
| `Config/image_mask_studio.json` | Policy máy local tùy chọn, copy từ example khi cần đổi giới hạn | Người dùng/quản trị viên quản lý | Ignore |
| `Config/image_mask_studio_state.json` | `image-mask-studio-state.v1`, sessions, presets, recent IDs, history/snapshots | Hub tạo và autosave runtime | Ignore |

Không commit bất kỳ tệp local nào, model, output, pixel, prompt riêng, secret
hay path cá nhân. `image_mask_studio.json` chỉ là policy; nó **không** được dùng
để lưu bản nháp. `image_mask_studio_state.json` là bản nháp; không sửa thủ công
khi Hub đang chạy.

State được ghi vào tệp tạm sibling rồi replace nguyên tử. Nếu JSON/top-level
contract không đọc được, manager trả `recovery_required`, chặn mutation và
không ghi đè file lỗi. Nếu session/preset/history/snapshot vượt contract hoặc
giới hạn, response trả `recovered_partial` ở chế độ **chỉ đọc** để không tự xóa
phần state cũ; kiểm tra snapshot/export rồi tạo workspace local mới hoặc nhờ
quản trị viên phục hồi.

Mỗi chỉnh sửa làm thay đổi document Studio dùng `revision` tăng dần. Client có
thể gửi `base_revision`; nếu revision đã cũ, API trả `409` cùng
`current_revision` và hướng dẫn tải lại hoặc kiểm tra bản nháp đã autosave trước
khi tiếp tục. Điều này tránh một cửa sổ ghi đè bản nháp của cửa sổ khác. Lưu
preset và hoàn tất pending Project-link không thay document nên không tăng
revision; explicit save đánh dấu bản nháp đã lưu và tạo snapshot.

## Contract, layer và giới hạn

| Contract | Mục đích |
| --- | --- |
| `image-mask-studio.v1` | Session public, layer stack, revision, history/snapshot summary và provenance |
| `image-mask-studio-state.v1` | State local persisted của sessions/presets/recent index |
| `image-mask-export.v1` | Manifest mask export/import đã validate |
| `image-mask-preset.v1` | Preset layer summary có thể áp dụng lại |
| `image-mask-studio-config.v1` | Policy local được normalize/bounded |

ID công khai được validate theo prefix/32 hex: `artifact_`, `project_`,
`studio_`, `layer_`, `snapshot_`, `maskpreset_`. Layer allowlist là `source`,
`mask`, `adjustment`, `generated`:

- `source` và `generated` tham chiếu image artifact hiện có; generated layer có
  thể giữ `parent_layer_id` và provenance JSON an toàn.
- `mask` có optional image artifact và danh sách operation; nó không mặc định là
  một file mask raster.
- `adjustment` chỉ nhận `brightness`, `contrast`, `saturation`, `exposure`,
  `temperature`, `crop` cùng settings JSON an toàn.
- Mask operation chỉ nhận `brush`, `invert`, `feather`, `grow`, `shrink`.
  Brush chỉ `add`/`subtract`; x/y chuẩn hóa trong khoảng 0–1.

Default policy giới hạn 120 session, 48 layer/session, 256 mask operation,
160 point/stroke, 32 undo entry, 24 snapshot và 120 preset. Policy normalize
chúng vào các biên an toàn, lần lượt tối đa 500/128/1024/512/96/96/500. Vì vậy input rất lớn,
đường dẫn, command, backend URL, model name hay switch giả nhận runtime hoạt
động không phải là cấu hình M6A hợp lệ.

Undo/redo và snapshot lưu bản sao JSON của document layer, không copy media.
Recent session bị giới hạn 12; preset bị giới hạn policy và overview chỉ công bố
projection bounded. Parent của generated layer phải thuộc stack acyclic; Hub
không gỡ một parent khi generated child còn tham chiếu tới nó.

## Artifact, Project và provenance

Studio chỉ chấp nhận source/mask/generated **image artifact đang tồn tại** trong
Artifact Store. Public response chỉ trả record đã publicize (opaque ID, tên,
media type, kích thước, URL Hub nếu khả dụng); raw filesystem path không bao giờ
đi vào request hoặc response.

Liên kết Project dùng transaction logic theo thứ tự an toàn:

1. Studio validate `project_id` và optional artifact IDs; tất cả phải thuộc
   session hiện tại và còn là image artifact hợp lệ.
2. Studio lưu `pending_project_attach` durable có token/revision, đồng thời khóa
   mutation của session cho đến khi bridge hoàn tất hoặc retry cùng intent.
3. Project Manager thêm reference idempotent về media/reference và lưu record
   provenance riêng theo từng Project: Studio ID/revision, source, tập artifact
   và mask artifact IDs. Source-only link vẫn có record Project; lineage global
   của artifact dùng chung không bị ghi đè.
4. Studio chỉ đánh dấu intent completed khi token/revision vẫn khớp và giữ một
   fingerprint hoàn tất cho đúng Project/artifact/revision; retry khớp trả
   completed mà không tạo revision mới. Nếu bước Project thất bại, intent còn đó để
   user retry/recover có chủ đích; Hub không âm thầm bỏ bản nháp hoặc copy file.

Project vẫn là metadata layer của M4A. Artifact Store là chủ sở hữu duy nhất của
file; M6A không đổi tag/lineage global của source chung. Derived/mask artifact
giữ parent immutable đầu tiên và một danh sách Studio lineage bounded khi cùng
source được tái dùng; Hub từ chối claim từ source khác thay vì ghi đè provenance.

## API loopback

Các endpoint đều nằm trên loopback Hub, nhận JSON bounded và chỉ xử lý opaque
ID hợp lệ. Đây là surface chính:

| Method | Route | Ý nghĩa |
| --- | --- | --- |
| `GET` | `/api/image-mask-studio/overview?project={opaque-id}` | Danh sách/recent session, preset, recovery và preflight |
| `GET` | `/api/image-mask-studio/preflight` | Capability `status`/`reason`/`action` |
| `GET` | `/api/image-mask-studio/sessions/{studio}` | Chi tiết một session/layer stack |
| `POST` | `/api/image-mask-studio/sessions` | Tạo session từ `source_artifact_id` image |
| `PUT` | `/api/image-mask-studio/sessions/{studio}` | Đổi title/project với revision guard |
| `POST` | `/sessions/{studio}/layers` | Thêm mask/adjustment/generated layer |
| `PUT` | `/sessions/{studio}/layers/{layer}` | Sửa metadata layer đã allowlist |
| `POST` | `/sessions/{studio}/layers/{layer}/operations` | Ghi operation mask non-destructive |
| `POST` | `/sessions/{studio}/layers/{layer}/move` hoặc `/remove` | Di chuyển/gỡ reference layer |
| `POST` | `/sessions/{studio}/undo`, `/redo`, `/save` | History và explicit local save |
| `GET` | `/sessions/{studio}/compare` | So sánh snapshot metadata |
| `POST` | `/sessions/{studio}/snapshots/{snapshot}/restore` | Khôi phục snapshot có revision guard |
| `GET`/`POST` | `/layers/{layer}/export`, `/masks/import` | Export/import mask manifest safe |
| `POST` | `/sessions/{studio}/presets`, `/presets/{preset}/apply` | Lưu/áp dụng preset |
| `POST` | `/sessions/{studio}/link-project` | Bridge durable sang Project Manager |

Trong các hàng rút gọn, `/sessions`, `/layers`, `/masks` và `/presets` có cùng
prefix `/api/image-mask-studio`. API trả `400` cho schema/ID sai, `404` cho
record không có và `409` cho stale revision. Không có route run model, execute
command, đọc/ghi path tùy ý hoặc nhận payload pixel.

## Preflight trung thực và limitation hiện tại

| Capability | Trạng thái hiện tại | Diễn giải |
| --- | --- | --- |
| `local_non_destructive_layers` | `operational` | Chỉ thao tác metadata/vector bounded và artifact ID; không gọi model |
| `sam2_assisted_mask` | `unavailable` mặc định; tối đa `partial` khi policy khai báo | Cần bounded SAM2 runtime smoke được ủy quyền trước khi dùng trợ lý phân vùng |
| `image_inpaint` | `unavailable` | Chưa có adapter masked-inpaint và smoke riêng; Qwen image-to-image không thay thế bằng chứng này |
| `image_outpaint` | `unavailable` | Chưa có descriptor/adapter outpaint an toàn và smoke riêng |

`partial` không có nghĩa là chạy được. Mọi trạng thái không operational đều phải
giữ reason/action trong API và UI. Trong resource-safety override, không chạy,
không inspect, không dừng và không can thiệp SAM2/inpaint/outpaint/ComfyUI,
FFmpeg hoặc bất kỳ workload GPU/video nào. Evidence functional còn thiếu được
ghi `deferred due GPU/resource contention`.

## Security boundary

- Browser/API không nhận hoặc trả raw path, URL backend, shell command, model
  path, data URL, canvas pixel, secret/token/credential hay callable.
- `safe_json` và schema bounded chặn metadata/provenance không an toàn; import
  mask chỉ chấp nhận layer/operation allowlist và opaque artifact reference.
- Artifact URL (nếu có) là endpoint Hub đã publicize trong owned root, không
  phải path máy. Xóa layer/session không đồng nghĩa với xóa artifact.
- Project bridge chỉ nhận artifact thuộc session và là idempotent, tránh client
  lợi dụng Studio để gắn arbitrary filesystem object vào Project.
- Không đưa state local, output/media cá nhân, model, environment hay secrets
  vào Git, manifest hoặc tài liệu/screenshot.

## Kiểm thử bounded và bằng chứng còn thiếu

M6A có test mục tiêu `tests/test_milestone6_image_mask_studio.py` cho manager,
contract/schema, history/snapshot/preset, recovery, stale revision, preflight,
mask import/export, Project provenance, loopback API và canvas/UI contract. Các
fixture dùng artifact tổng hợp nhỏ; không có FFmpeg, video, model hay GPU run.

Lệnh kiểm tra bounded ở Review Gate:

```powershell
cd D:\LocalAIHub
node --check src\ui\image_mask_studio.js
node --check src\ui\api.js
node --check src\ui\app.js
node --check src\ui\pages.js
python -m unittest -v tests\test_milestone6_image_mask_studio.py
python scripts\ci_validate.py
git diff --check
```

Các kiểm tra trên chứng minh contract/UI/API metadata của M6A, không phải smoke
functional cho SAM2, inpaint, outpaint, ComfyUI hoặc GPU/video. Bất kỳ adapter
tương lai nào phải qua contract, preflight và bounded smoke riêng trước khi UI
có thể nâng trạng thái capability.

## Extension point được phép

Một tích hợp render/mask engine về sau phải:

1. Giữ preflight `partial`/`unavailable` cho đến khi có smoke được ủy quyền.
2. Chỉ nhận opaque source/mask artifact IDs và metadata allowlist; không nhận
   path/command tự do từ UI.
3. Đăng ký output qua Artifact Store trước khi tạo generated layer/provenance.
4. Không ghi đè source; giữ lineage tới source/mask/recipe/Studio revision.
5. Thêm test contract + bounded functional smoke riêng; trong thời gian resource
   override, ghi rõ phần runtime là deferred thay vì tự chạy nó.
