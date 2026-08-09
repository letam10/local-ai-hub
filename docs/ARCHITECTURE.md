# Kiến trúc và bản đồ thư mục

Local AI Hub là bộ điều phối chạy cục bộ trên Windows. Mã nguồn chính nằm dưới
`src/`; runtime nặng, model, cache, output và dữ liệu cá nhân nằm ngoài Git.
Các thư mục tương thích cũ `Hub/`, `MCP/` và `Adapters/` chỉ là shim, không phải
source canonical.

## Ownership theo tầng

- `src/app/`: desktop shell, WebView, bootstrap và vòng đời cửa sổ.
- `src/ui/`: navigation, workspace, Node Studio LiteGraph, queue/job panel và
  design-system CSS. UI chỉ gọi API loopback, không chạy shell command.
- `src/services/api/`: route loopback, upload/artifact, validate, submit graph,
  dashboard và public response contract.
- `src/services/node_studio/`: registry node typed, schema validator, DAG
  engine và trạng thái run/provenance trong bộ nhớ.
- `src/services/job_manager/`: queue, progress, cancel/resume và GPU slot;
  chỉ quản lý tiến trình do Hub tạo.
- `src/modules/`: adapter cho ComfyUI, FFmpeg, SAM2, AnimeSR, Whisper và các
  backend local đã cài; không vendor repository upstream và không tự tải model.
- `src/shared/`: kiểu dữ liệu và tiện ích dùng chung.
- `scripts/`: launcher, audit và kiểm tra bounded; `tests/`: unit/smoke tests.
- `workflows/`: graph JSON mẫu được theo dõi trong Git, không chứa model,
  output, đường dẫn máy hay secret.
- `Config/`: chỉ cấu hình mẫu `*.example.json`; cấu hình máy local bị ignore.

Các thư mục `runtime/`, `Models/`, `Environments/`, `Cache/`, `Output/`,
`Temp/`, `Logs/` và `Reports/` chứa cài đặt hoặc dữ liệu runtime local, không
được commit. Không tự ý di chuyển hay xoá các cài đặt AI hiện có.

## Luồng Node Image Studio

```text
WebView UI → /api/node-studio/registry + /availability
          → validate graph → job.v2 queue → node-run.v2 DAG engine
          → adapter (ComfyUI / FFmpeg) → artifact ID + provenance → UI preview
```

Luồng ảnh chuẩn là `Prompt/Image artifact → Generate → Image Edit (tuỳ chọn) →
Upscale → Preview → Save/Export`. Socket được kiểm tra theo kiểu; validator
từ chối input bắt buộc thiếu, nối sai kiểu, cạnh trùng và cycle. Registry dùng
`node-studio.v2`; run snapshot công bố `contract_version=node-run.v2`, trạng
thái từng node, `next_action` và provenance an toàn; public job dùng
`contract_version=job.v2` và không trả raw input hoặc workstation path.

`operational` chỉ dành cho adapter đã có bounded smoke. `partial` hoặc
`unavailable` luôn có `reason` và `action`, được hiển thị ở palette, Inspector,
queue và lỗi job; không được giả nhận backend là hoạt động.

## API và kiểm thử

Các route Node Studio chính:

- `GET /api/node-studio/registry?scope=image`
- `GET /api/node-studio/availability?scope=image`
- `GET /api/node-studio/presets`
- `POST /api/node-studio/validate` và `POST /api/node-studio/run`
- `GET /api/node-studio/runs/{job_id}`

Kiểm tra mục tiêu trong lúc phát triển:

```powershell
& .\Environments\hub\Scripts\python.exe -m unittest -q `
  tests/test_v4_node_studio.py tests/test_unified_ui.py
```

Ở cuối milestone mới chạy một lượt full bounded suite, HTTP/UI smoke và image
workflow smoke nếu backend local đã sẵn sàng. Không benchmark, không chạy
inference lặp, không tải model và không thay đổi CUDA/NVIDIA driver.
