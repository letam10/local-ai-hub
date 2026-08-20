# Milestone 3 — Unified Creative UX, Workflow Productivity & Source Architecture

Tài liệu này ghi nhận contract của Milestone 3 trên `feature/local-ai-hub-v4`. Mục tiêu là
đưa Image AI, Media/Video, SAM2 và AnimeSR vào cùng một ngôn ngữ giao diện và cùng một
vòng đời workflow, nhưng vẫn giữ trạng thái backend trung thực.

## Phạm vi UX thống nhất

- Navigation, workspace header, status pill và `workspace-state` dùng chung cho các
  module sáng tạo.
- Mỗi workspace có đủ trạng thái onboarding/empty, loading, error và
  `partial`/`unavailable`. Reason và action được lấy từ capability contract; UI không
  tự suy đoán backend đã hoạt động.
- Node palette, inspector, workflow rail, job progress/cancel và artifact preview
  dùng cùng visual language. Preview media ở trong Hub; artifact có nút `Lưu/Xuất`
  và `Mở` qua allowlist của Hub, không mở tab trình duyệt ngoài cho luồng chính.
- Layout co giãn ở viewport nhỏ: navigation chuyển thành drawer, card chuyển một cột,
  canvas giữ vùng cuộn riêng. Nút và tab có trạng thái focus/ARIA cơ bản; tôn trọng
  `prefers-reduced-motion`.

## Workflow productivity contract

Node Studio lưu dữ liệu cá nhân trong `localStorage` của WebView, không tạo file source
hoặc copy model:

| Khả năng | Contract |
| --- | --- |
| Tạo từ template | `GET /api/node-studio/presets/{id}` → schema validation trước khi hydrate canvas |
| Rename | Chỉ cập nhật `graph.title`, giới hạn 160 ký tự và loại bỏ dữ liệu không cần thiết |
| Duplicate | Tạo ID local mới, giữ graph đã validate, đánh dấu bản sao là chưa lưu |
| Recent | Index `local-ai-hub-workflows-v1:index:{scope}`, tối đa 12 mục; graph nằm ở key opaque theo scope/ID |
| Autosave/recovery | Key `local-ai-hub-graph-v5:{scope}` được cập nhật sau thay đổi; lần mở lại hiển thị “Đã khôi phục autosave” |
| Unsaved warning | So sánh fingerprint với lần `Lưu local`; `beforeunload` cảnh báo khi còn thay đổi chưa xác nhận |
| Import/export | Import gọi `POST /api/node-studio/validate` trước khi hydrate; export chỉ tạo JSON tải xuống từ graph đã public |
| Undo/redo | Giữ lịch sử trong phiên WebView, không gửi raw path hay secret lên API |

API Node Studio tiếp tục dùng `node-studio.v2`; graph run dùng `node-run.v2`; job công
khai dùng `job.v2`. `next_action`, `reason`, `action` và provenance chỉ chứa metadata
an toàn, artifact ID opaque và URL loopback.

## Queue, history và artifact

`Jobs` là nơi duy nhất hiển thị queue/history. Filter `Tất cả`, `Đang chạy`, `Cần chú ý`
và `Hoàn tất` chỉ là lớp UI trên snapshot `GET /api/jobs`. Cancel gọi route dành cho
job do Hub sở hữu; `Tiếp tục/Thử lại` chỉ được bật khi job có `resume_data` an toàn và
runner còn trong phiên. `unavailable` có thể thử lại nhưng vẫn giữ status truthful.

Result của graph có `provenance` theo node và artifact. UI hiển thị provenance cùng
`next_action`, progress và artifact preview/save/export. Public boundary vẫn loại bỏ
input/resume raw và workstation path.

## Creative capability matrix

- Image: Quick FLUX/Qwen, Image Edit và Image Upscale đi qua form/Hub Nodes; backend
  chưa smoke vẫn là `partial` với action hướng dẫn.
- Video/Media: Transform, Upscale, Interpolate, Encode, Preview, Save và Export dùng
  graph/allowlist hiện có. Video generation vẫn `unavailable` nếu chưa có adapter local
  đã smoke.
- SAM2: point/box/text/track dùng cùng workspace và hiển thị reason/action riêng.
- AnimeSR: queue/progress/cancel/retry dùng worker contract hiện có; RIFE và
  Real-ESRGAN không được nâng trạng thái khi chưa có smoke.

Trong Milestone 3, resource-safety override cấm khởi chạy FFmpeg/NVENC, video
generation/transform/upscale/interpolation/encode, AnimeSR, RIFE hoặc ComfyUI video.
Functional video re-smoke vì vậy được ghi là `deferred due GPU/resource contention`;
đây không phải lý do để giả nhận capability operational.

## Ownership và extension points

- `src/ui/pages.js`: composition của navigation page, state card, workflow rail, job
  history và artifact view.
- `src/ui/app.js`: state snapshot, route/event delegation, responsive drawer,
  preview modal và API lifecycle.
- `src/ui/features/node_studio/studio.js`: editor canvas, template/recent/autosave/import/export và
  local unsaved guard; không sở hữu worker hay filesystem runtime.
- `src/services/api/core.py`: tool catalog, truthful readiness và action contract.
- `src/services/api/api_server.py`: loopback routes; không nhận shell command/path từ UI.
- `src/services/node_studio/`: registry, schema, DAG execution và public provenance.
- `src/services/job_manager/`: queue, progress, cancel/retry và ownership process.
- `workflows/`: preset graph được track trong Git; workflow cá nhân chỉ ở WebView local.

Extension point mới phải giữ typed socket/schema validation, opaque artifact ID, public
path scrubbing và trạng thái `partial`/`unavailable` cho tới khi có bounded smoke.

## Kiểm tra bounded

Không benchmark và không chạy workload video/GPU trong milestone này. Kiểm tra được phép:

```powershell
node --check src/ui/app.js
node --check src/ui/pages.js
node --check src/ui/features/node_studio/studio.js
python -m unittest -q tests/test_milestone3_contracts.py tests/test_unified_ui.py tests/test_v4_node_studio.py
python scripts/ci_validate.py
git diff --check
```

HTTP/UI smoke chỉ kiểm tra loopback route, HTML/JS tải được, navigation, status card,
Recent/duplicate/rename và filter Jobs; không đặt `LOCALAIHUB_RUN_VIDEO_SMOKE=1`.
