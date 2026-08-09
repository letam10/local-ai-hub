# Node Studio V5

Node Studio V5 dùng LiteGraph.js được pin offline trong src/ui/vendor thay cho
editor tự viết theo kiểu chọn output rồi chọn input. LiteGraph cung cấp kéo dây
giữa socket, pan, zoom, đa chọn, undo, redo và canvas interaction. Hub giữ
backend DAG, validator schema, cache theo content hash, Job Manager và artifact
ID hiện có.

Các socket được kiểm tra type trước khi chạy. Những type chính gồm IMAGE, MASK,
VIDEO, AUDIO, TEXT, NUMBER, BOOLEAN, MODEL và METADATA. Kết nối sai type hoặc
chu trình bị API từ chối. Minimap và inspector preview hiển thị ngay trong Hub;
workflow cá nhân autosave trong localStorage và không vào Git.

Image AI có ba workspace:

1. Quick: FLUX hoặc Qwen được compile thành ComfyUI API workflow.
2. Hub Nodes: graph Hub có typed socket để nối SAM2, FFmpeg và node Image.
3. ComfyUI Advanced: frontend ComfyUI gốc được nhúng trong cùng WebView của
   cửa sổ Local AI Hub. Hub không sao chép hoặc triển khai lại frontend ComfyUI.

Advanced chỉ dùng URL loopback 127.0.0.1 và iframe sandbox không có quyền popup
hoặc top navigation. Nếu ComfyUI chưa chạy, nút khởi động yêu cầu backend ẩn
Hub-owned hoặc hiển thị trạng thái unavailable. Không mở Chrome hoặc Edge ngoài
cho đường đi bình thường.

Bridge workflow có schema version 1 và nằm tại workflows/comfyui khi được track,
hoặc workflows/local/comfyui khi người dùng lưu cục bộ. Node ComfyUI Workflow
nhận TEXT, IMAGE, MASK, VIDEO, AUDIO và METADATA từ graph Hub. Raw bridge không
nhận đường dẫn máy; IMAGE và MASK được upload nội bộ qua artifact Hub. VIDEO và
AUDIO chưa có uploader an toàn nên trả unavailable thay vì tuyên bố đã chạy.
