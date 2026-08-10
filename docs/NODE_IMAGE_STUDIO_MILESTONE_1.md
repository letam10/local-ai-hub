# MILESTONE 1 — Node Image Studio và UX Foundation

Tài liệu này mô tả snapshot tích hợp của Image AI sau MILESTONE 1. Mục tiêu là
giúp người mới đi từ prompt hoặc ảnh input đến output có preview, provenance và
trạng thái job rõ ràng trong cùng một cửa sổ Local AI Hub.

## Luồng sản phẩm

```text
Prompt / Image artifact
        ↓
Generate (FLUX hoặc Qwen)  →  Image Edit / Image-to-Image (tuỳ chọn)
        ↓                                  ↓
        └──────────────→ Upscale Image (FFmpeg fallback)
                                      ↓
                         Preview → Save / Export artifact
```

Hub Nodes dùng canvas LiteGraph được pin trong `src/ui/vendor`. Socket có kiểu
`IMAGE`, `MASK`, `VIDEO`, `AUDIO`, `TEXT`, `NUMBER`, `BOOLEAN`, `MODEL` hoặc
`METADATA`; validator từ chối nối sai kiểu, node bắt buộc còn thiếu input, ID
artifact không hợp lệ, duplicate edge và cycle.

## Template được theo dõi

- `workflows/image_create_upscale.json`: Prompt → FLUX Generate → Upscale →
  Preview → Save.
- `workflows/image_edit_upscale.json`: Image artifact + Prompt → Qwen
  Image-to-Image → Upscale → Preview → Save. Template này yêu cầu người dùng
  upload một image artifact trước khi chạy.
- `workflows/image_draft.json`: template FLUX tối giản tương thích V4.

Template chỉ là graph JSON, không chứa model, output, local path hoặc secret.
Workflow cá nhân được lưu trong `localStorage` hoặc file `.local.json` bị Git
ignore.

## Contract API

| Route | Mục đích |
| --- | --- |
| `GET /api/node-studio/registry?scope=image` | Node typed, property, status và availability action |
| `GET /api/node-studio/availability?scope=image` | Ma trận `operational` / `partial` / `unavailable` nhỏ gọn cho UX |
| `GET /api/node-studio/presets` | Danh sách template, mô tả, stage và yêu cầu |
| `POST /api/node-studio/validate` | Validate graph để lưu hoặc chạy |
| `POST /api/node-studio/run` | Queue graph job; trả `contract_version=node-run.v2`, graph ID và thứ tự DAG |
| `GET /api/node-studio/runs/{job_id}` | Progress từng node, next action và provenance artifact |

Job public dùng `contract_version=job.v2`, không trả input raw hoặc workstation
path. Output graph chỉ công bố artifact ID, URL loopback, media type, node tạo
ra artifact và đường dẫn output logic (không phải đường dẫn máy).

## Availability trung thực

`operational` chỉ dùng cho adapter đã có bounded smoke phù hợp. `partial` nghĩa
là contract và UI đã có nhưng cần cấu hình hoặc smoke bổ sung. `unavailable` nghĩa
là backend bắt buộc không có. Image generation FLUX/Qwen và Image-to-Image hiện
được đánh dấu `partial` vì cần ComfyUI/model workflow; Upscale Image dùng
FFmpeg scale fallback và không được gọi là AI upscaler. Real-ESRGAN vẫn
`partial/unavailable` cho tới khi CLI contract được xác minh.

Mỗi trạng thái có `reason` và `action`. UI hiển thị chúng trong palette,
Inspector, Job và error state; không giả lập kết quả khi backend chưa sẵn sàng.

## Ownership và folder map

- `src/ui/`: shell WebView, navigation, Node Studio canvas và design-system CSS.
- `src/services/api/`: loopback routes, bootstrap, job submission và public
  contracts.
- `src/services/node_studio/`: registry, schema validator, DAG engine và
  in-memory run/provenance state.
- `src/services/job_manager/`: queue, progress, cancel/resume và GPU slot.
- `src/modules/image_generation/`: ComfyUI bridge/adapters; không vendor
  frontend upstream.
- `src/modules/media_editor/`: FFmpeg/FFprobe allowlist, gồm image resize và
  fallback upscale.
- `workflows/`: preset graph JSON được theo dõi; dữ liệu người dùng nằm local.
- `Config/`: chỉ các file `*.example.json` và ví dụ cấu hình an toàn.
- `Models/`, `Environments/`, `runtime/`, `Cache/`, `Output/`, `Temp/`,
  `Logs/`, `Reports/`: dữ liệu cài đặt/runtime máy local, không commit.

## Kiểm thử bounded

Từ thư mục repository, dùng environment Hub:

```powershell
& .\Environments\hub\Scripts\python.exe -m unittest -q `
  tests/test_v4_node_studio.py `
  tests/test_unified_ui.py
```

Integration handoff cuối milestone chạy thêm full bounded suite, HTTP `/health`,
`/api/dashboard`, `/api/node-studio/registry`, `/api/node-studio/availability`,
`/api/node-studio/presets`, `/ui/` và một image workflow smoke duy nhất khi
backend local đã sẵn sàng. Không benchmark, không loop inference, không tải
model và không thay đổi CUDA/NVIDIA driver.
