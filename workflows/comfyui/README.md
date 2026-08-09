# ComfyUI bridge workflows

`*.bridge.json` là metadata first-party rất nhỏ. Chúng không sao chép hoặc
vendor giao diện ComfyUI, model, node package hay workflow cá nhân.

- `quick_api` chọn một workflow API FLUX/Qwen đã được cài cục bộ. Node Hub
  `ComfyUI Workflow` biên dịch typed `TEXT` và `IMAGE` artifact vào request.
- `raw_comfy_api` dành cho workflow JSON người dùng lưu local. Binding chỉ rõ
  node/input nào nhận `text`, `image`, `mask` hoặc metadata Hub. Đường dẫn cục
  bộ bị từ chối để không lọt vào API hay Git.
- Lệnh Save từ Advanced lưu dưới `workflows/local/comfyui/`, là user data bị
  `.gitignore`; chỉ hai preset Quick và schema này được track.

Video/audio chưa có uploader bridge an toàn chung cho ComfyUI. Khi một graph
cần chúng, Hub báo `unavailable` thay vì giả vờ workflow đã chạy.
