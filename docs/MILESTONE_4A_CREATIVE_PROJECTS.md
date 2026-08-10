# MILESTONE 4A — Creative Projects, Asset Library & Reproducible Recipes

## Mục đích và ranh giới

Milestone 4A đưa một lớp tổ chức sáng tạo có thể tái lập vào Local AI Hub:

```text
Project → asset đã đăng ký → prompt/recipe versioned → workflow template
        → Compare Board → selection/favorite → recipe có thể áp dụng lại
```

Đây là một lớp metadata cục bộ, không phải một runtime inference. Nó không
copy, nén, encode, transform, upscale, generate hoặc xóa media. File artifact
vẫn do Artifact Store sở hữu; model, environment, cache, output cá nhân và
secret nằm ngoài Git và ngoài manifest M4A.

Trong resource-safety override, không chạy FFmpeg/NVENC, video generation,
transform/upscale/interpolation/encode, AnimeSR/RIFE hoặc ComfyUI video. Bằng
chứng functional video giữ trạng thái `deferred due GPU/resource contention`.

## Người dùng thấy gì trong một cửa sổ

Workspace **Projects & Recipes** có năm phần đồng nhất về loading, empty,
error/recovery state, keyboard focus và responsive layout:

1. **Projects** — tạo/mở/đổi tên/archive/khôi phục; Recent tối đa 12 project;
   export/import manifest có conflict policy rõ ràng.
2. **Asset Library** — contact sheet image/placeholder media, search/tag,
   favorite, collection, lineage và tham chiếu artifact có sẵn vào project.
3. **Prompts & Recipes** — prompt variable, style/negative block, seed/model/
   settings, version, Recipe Pack import/export và apply đến Quick/Hub Nodes.
4. **Compare Board** — tối đa 8 artifact của một project, metadata/settings/
   provenance diff, chọn kết quả, favorite và quay lại recipe liên quan.
5. **Workflow Gallery** — card/category/search/preflight. Template được nạp
   vào Node Studio nhưng không tự chạy graph hoặc backend.

Mọi screen đều nói đúng trạng thái backend. Một template tồn tại không đồng
nghĩa với runtime hoạt động: `partial` hoặc `unavailable` luôn hiển thị
reason/action từ registry.

## Local state, recovery và ownership

### Nơi lưu

| Dữ liệu | Vị trí | Git |
| --- | --- | --- |
| Creative workspace thực tế | `Config/creative_workspace.json` | Ignore |
| Mẫu contract rỗng | `Config/creative_workspace.example.json` | Track |
| Artifact file/index canonical | Vùng Artifact Store/Temp/Output local | Ignore |
| Workflow source template | `workflows/*.json` | Track |
| Workflow cá nhân Node Studio | WebView `localStorage` hoặc `*.local.json` | Ignore |

`CreativeProjectManager` dùng `creative-workspace.v1`, `schema_version: 1` và
atomic replacement qua temporary file của chính state. Không có cache, model,
media hay API key nào được đặt vào workspace.

### Safe recovery

- File không đọc được hoặc top-level không phải object: trả
  `recovery_required`, **không ghi đè**, mọi mutation trả lỗi có action.
- Một record con bất hợp lệ: record hợp lệ còn lại được nạp, trả
  `partial_recovery` cùng số record bị bỏ qua và hướng dẫn export/import.
- Import có ba conflict mode: `copy` (mặc định, non-destructive), `skip`,
  `replace`. Không có merge suy đoán hoặc overwrite im lặng.
- Project import chỉ giữ opaque artifact reference. Artifact không tồn tại ở
  máy đích vẫn là reference thiếu; user cần đưa artifact vào Hub hợp lệ rồi
  gắn lại, thay vì manifest nhận raw path.

## Contract và validation

| Contract | Nội dung chính |
| --- | --- |
| `creative-project.v1` | title/description/tags/status, opaque asset/recipe refs, selected refs, workflow preset |
| `creative-asset.v1` | Artifact Store public record + tag/favorite/collection/lineage/provenance/recipe |
| `creative-recipe.v1` | title, version, template + variables, style/negative, seed/model/settings/tags/preset |
| `creative-compare.v1` | project board, tối đa 8 artifact, selection và computed diff |
| `creative-project-export.v1` | portable metadata/project/recipe/assets/compare export |
| `creative-recipe-pack.v1` | portable recipe pack export/import |

ID đều opaque và validate chặt: `artifact_`, `project_`, `recipe_`,
`collection_`, `compare_` + 32 ký tự hex. `safe_json` giới hạn depth/size,
từ chối key/path/secret/token/credential/password và chuỗi có Windows drive
hoặc UNC path. Public response không chứa raw source path, input payload,
resume data, callable runner hoặc secret.

## Luồng thao tác

### Project → asset → compare

1. Tạo một Project và chọn nó làm workspace hiện tại.
2. Trong Asset Library, lọc artifact đã tồn tại theo tên/tag/favorite/
   collection. Thêm artifact vào Project chỉ tạo reference; không copy file.
3. Tùy chọn thêm tag/favorite, đưa vào Collection, hoặc khai báo lineage với
   parent opaque ID/provenance an toàn thông qua API contract.
4. Thêm artifact thuộc Project vào Compare Board, chọn/favorite kết quả.
5. Xem diff chỉ gồm media type, size, tag, favorite, recipe ID, lineage,
   provenance và settings — không render raw private input/path.

### Recipe → Quick hoặc Hub Nodes

1. Tạo Recipe với template `{{variable}}` hoặc `${variable}`, default/required
   values, style block, negative block, seed, model, width/height/steps.
2. Khi áp dụng, backend chỉ render variables và trả application object.
3. **Quick** điền form editable; **Hub Nodes** clone graph rồi điền node
   `prompt_text`, generator, seed/resolution/sampler settings có liên quan.
4. Không có job nào được tạo bởi apply. User kiểm tra/chỉnh tiếp, sau đó mới
   bấm Generate hoặc Run Graph theo contract hiện có.
5. Update Recipe tăng version. Recipe Pack export/import giữ contract/version
   chứ không vận chuyển model weight, output hay runtime state.

### Gallery → Node Studio

Gallery quét workflow JSON tracked và xác định scope/category/node count. Card
đọc capability preflight từ Node Studio registry. Chọn card chỉ nạp preset vào
scope Node Studio tương ứng (Image, Media, SAM2 hoặc AnimeSR); workflow vẫn
chờ người dùng xem validation rồi chủ động Run Graph.

## HTTP surface

| Method/route | Ý nghĩa |
| --- | --- |
| `GET /api/creative/overview` | Một snapshot path-safe cho toàn Creative Workspace |
| `GET, POST /api/projects` | List/create Project |
| `GET, PUT /api/projects/{project_id}` | Đọc/đổi metadata Project |
| `POST /api/projects/{project_id}/archive` | Archive metadata non-destructive |
| `POST /api/projects/{project_id}/restore` | Khôi phục Project |
| `POST /api/projects/{project_id}/assets` | Gắn artifact Hub hiện có vào Project |
| `GET, POST /api/projects/{project_id}/compare` | Read/update Compare Board |
| `GET /api/projects/{project_id}/export` | Export Project Manifest |
| `POST /api/projects/import` | Validate/import Project Manifest |
| `GET /api/assets`, `PUT /api/assets/{artifact_id}` | Filter/read/update Asset metadata |
| `GET, POST /api/collections`, `PUT /api/collections/{id}` | Collection lifecycle |
| `GET, POST /api/recipes`, `PUT /api/recipes/{id}` | Recipe lifecycle/versioning |
| `POST /api/recipes/{id}/apply` | Render safe Quick/Node application, no execution |
| `GET /api/recipes/export-pack`, `POST /api/recipes/import-pack` | Recipe Pack transfer |
| `GET /api/workflow-gallery` | Tracked template discovery + truthful preflight |

Loopback handler chỉ nhận JSON bounded 2 MB. Invalid contract/ID trả `400`;
không tìm thấy public resource trả `404`. API không có route để client cung cấp
manifest source path, shell command, model/output bundle hoặc arbitrary runtime
object.

## Extension points

- Backend/module mới đăng ký output qua Artifact Store trước; sau đó UI hoặc
  API có thể gắn opaque artifact ID vào Project.
- Adapter có thể gửi provenance JSON nhỏ vào asset metadata nếu pass
  `safe_json`. Đừng dùng M4A để chứa raw request, secret hay environment.
- Một template mới cần là `workflows/*.json` tracked, schema Node Studio hợp lệ
  và không chứa path/model/output/secret. Gallery tự nhận card/preflight.
- Recipe settings chỉ cần field mà Quick/Node Studio biết cách fill. Field lạ
  có thể được giữ trong recipe JSON an toàn nhưng không được diễn giải như
  lệnh thực thi.

## Kiểm thử bounded

Trong lúc thay đổi M4A, ưu tiên delta sau:

```powershell
node --check src\ui\api.js
node --check src\ui\app.js
node --check src\ui\pages.js
node --check src\ui\node_studio.js
python -m unittest -v tests\test_milestone4_projects.py
```

`tests/test_milestone4_projects.py` kiểm tra Project/Asset/Recipe/Compare,
collection/filter/lineage, import/export conflict, malformed-state recovery,
gallery unavailable reason/action, recipe clone-fill không mutate graph nguồn,
và loopback public contract không rò path/resume/input. Review Gate tổng hợp
thêm regression suite bounded, HTTP/UI smoke, `scripts/ci_validate.py` và
`git diff --check`.

Không benchmark, không lặp inference, không thay dependency/model/CUDA/driver/
system. Không khởi chạy video/GPU workload chỉ để xác minh M4A.
