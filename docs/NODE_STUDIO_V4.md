# Local AI Hub V4 — Node Studio và runtime không-console

V4 mở rộng ứng dụng một cửa sổ bằng Node Studio chạy hoàn toàn local/offline.
Không sử dụng CDN và không sao chép frontend GPL của ComfyUI. UI Node Studio là
mã nguồn riêng của Hub, được phục vụ từ `src/ui/` trong WebView.

## Khởi động và polling

- Desktop tạo WebView trước với màn hình `Đang khởi động dịch vụ cục bộ…`.
  API được khởi động hidden trên worker thread; khi `/health` trả về thì WebView
  chuyển sang `/ui/`. Vì vậy cửa sổ không chờ vòng timeout 12 giây.
- Mọi subprocess Hub-owned dùng helper
  `src/services/process_manager/windows.py`: `CREATE_NO_WINDOW`,
  `CREATE_NEW_PROCESS_GROUP`, `STARTF_USESHOWWINDOW`, `SW_HIDE`, `stdin=DEVNULL`
  và stdout/stderr pipe hoặc log Hub.
- Snapshot `GET /api/bootstrap` chỉ tải control-plane startup đã cache, không
  scan model hay storage. UI refresh nhanh `/health` + `/api/jobs` mỗi 2,5 giây.
  `nvidia-smi` cache ít nhất 2 giây, `tasklist` cache ít nhất 5 giây; model và
  storage chỉ đọc khi mở trang Models hoặc người dùng bấm Scan. Storage recursive
  scan cache 120 giây.

## Node Studio

Các workspace **Image AI**, **SAM2**, **Media** và **AnimeSR** có tab `Quick`
và `Nodes`. Canvas hỗ trợ pan/zoom, kéo node, typed socket, connect/disconnect,
multi-select, Delete, undo/redo, duplicate, minimap, search/add-node, group,
comment, reset view, autosave local, Save As local, import/export JSON, Run
Graph và Cancel.

Các port được kiểm tra tại backend và không thể nối sai loại:

`IMAGE`, `MASK`, `VIDEO`, `AUDIO`, `TEXT`, `NUMBER`, `BOOLEAN`, `MODEL`,
`METADATA`.

Backend Node Studio có registry, schema, DAG validator, cycle detection,
topological execution, trạng thái/progress/error theo node, artifact ID riêng tư,
job ownership, dirty propagation và content-hash cache. Khi sửa một node, chỉ
node đó và downstream bị dirty; upstream output còn artifact hợp lệ được cache
và tái dùng. Không path cục bộ nào được nhận qua Graph JSON hoặc trả về UI.

`Auto Preview` chỉ có hiệu lực khi người dùng bật. Node GPU nặng mặc định chờ
`Run Graph`; Draft image chủ động giới hạn resolution/steps trước khi gọi FLUX
hoặc Qwen. Final render luôn dùng setting thật trong graph.

## Preset và encoding

Preset mặc định được track trong `workflows/`:

- `image_draft.json`
- `sam2_segment.json`
- `media_encode.json`
- `animesr_pipeline.json`

Workflow người dùng autosave trong `localStorage` của WebView. Có thể Export
JSON để lưu ngoài Git; nếu đặt cạnh preset, dùng hậu tố `.local.json` (bị Git
bỏ qua). Preset tracked không chứa model, media, secret hoặc đường dẫn máy.

Node `Encode` dò `ffmpeg -encoders` và `ffmpeg -h encoder=<name>` một lần rồi
cache capability cục bộ. UI chỉ hiển thị H.264/H.265/AV1, NVENC/software,
pixel format, quality CRF/CQ, VBR/CBR, multipass, audio codec và container mà
FFmpeg hiện tại thực sự công bố. `Frame Interpolation` dùng Practical-RIFE khi
có CLI contract đã xác minh; nếu chưa có, người dùng có thể chọn fallback
FFmpeg `minterpolate`. AnimeSR, RIFE và Real-ESRGAN giữ `partial`/`unavailable`
khi chưa có bounded smoke riêng.

## Audit nguồn ngoài Git

Không có migration hoặc cleanup filesystem trong V4. Lệnh dưới đây chỉ tạo báo
cáo local ignored, phân loại đường dẫn quan trọng ngoài Git thành
`FIRST_PARTY_SOURCE`, `UPSTREAM_CLONE`, `BUILD_ARTIFACT`, `MODEL`, `ENV`,
`CACHE` hoặc `USER_DATA`:

```powershell
python .\scripts\audit_source_v4.py
```

Report nằm tại `Reports\SOURCE_AUDIT_V4.local.md`. Nếu báo cáo phát hiện source
first-party cần đưa vào Git, cần review và sanitize riêng; clone upstream vẫn
không được vendor và phải có repo/commit trong `dependencies.lock.json`.
