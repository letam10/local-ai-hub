# MILESTONE 4A — Reliability & Large Media Hardening

Tài liệu này mô tả batch hardening đi kèm Creative Projects (M4A). Mục tiêu là
giữ Local AI Hub đáng tin cậy khi có job kéo dài hoặc artifact lớn, nhưng không
thay đổi model, driver, CUDA, runtime AI hay dữ liệu người dùng.

## Trạng thái capability và an toàn tài nguyên

- Product version công khai là `4.0.0`. Version này dùng cho `/health`, HTTP
  server header và các component example của Hub. Nó **không** thay thế các
  contract độc lập như `job.v2`, `node-run.v2` hay creative contracts `*.v1`.
- Trong resource override hiện tại không chạy FFmpeg/NVENC, video
  generate/transform/upscale/interpolate/encode, AnimeSR, RIFE hoặc ComfyUI
  runtime/video. Video functional re-smoke vẫn là `deferred due GPU/resource
  contention`.
- Artifact, upload và job tests chỉ tạo byte nhỏ/JSON/worker giả. Không có
  model, output media thực, secret hay path máy được đưa vào Git.

## Desktop close contract

`src/app/main.py` chỉ dọn API/backends khi chính desktop đã sở hữu API và người
dùng đã chọn một đường thoát an toàn. Các status active là `queued`, `starting`,
`running` và `cancelling`.

| Điều kiện | Hành vi bắt buộc |
| --- | --- |
| Không có active job | Đóng bình thường, yêu cầu cleanup cho API/backends **owned** |
| Có active job, `Quay lại Hub` | Hủy yêu cầu đóng; job tiếp tục |
| Có active job, `Hủy jobs và thoát` | Gửi cancel cho mọi job Hub, chờ bounded đến terminal; timeout giữ cửa sổ mở và hiện action, không force-kill |
| Có active job, `Giữ chạy nền vào khay` | Chỉ hide sau khi Windows notification tray tạo thành công; tray có **Khôi phục** và **Thoát** |
| API do shell/service khác sở hữu | Active job vẫn veto close và hiện ba lựa chọn; desktop không shutdown, idle-cleanup hoặc global-cancel API đó |

Native tray là implementation Windows không thêm dependency (`ctypes` + shell
notification area). Nếu không tạo được tray, background bị từ chối có thông báo
và Hub vẫn hiển thị; không tồn tại cửa sổ hidden không thể khôi phục. Trong
loading/transition, bridge thử gửi close prompt ba lần rồi dùng local fallback
page; nếu WebView vẫn không nhận được, close bị veto và cửa sổ được giữ mở.

Với API do desktop sở hữu, `POST /api/lifecycle/prepare-close` khóa admission
trong server, recheck active record và chỉ cho cleanup sau khi atomically thấy
queue trống. Nếu thấy job mới, server mở admission lại và decision gate tiếp
tục hiển thị.

## Job persistence và recovery

`src/services/api/jobs.py` phân tách state bền và retry state của process:

- progress-only update được coalesce khoảng 500 ms; create, status transition
  và terminal transition flush ngay; API graceful shutdown gọi `flush()`.
- `Config/jobs.json` (ignored) giữ mọi active record và tối đa 500 terminal
  record gần nhất. Terminal cũ được ghi JSONL vào `Archive/Jobs/` (ignored),
  rotate ở 16 MiB/file và giữ tối đa 30 file.
- callable runner, input/resume payload, private path và session
  `resume_available` không được persist.
- Khi API mới load record cũ ở status active, record trở thành `interrupted`
  cùng error/next action tạo lại tác vụ. Chỉ `failed`, `cancelled` và
  `unavailable` có runner trong phiên hiện tại mới hiện retry; `completed` và
  `interrupted` không thể quảng bá retry sai sự thật.
- Runner metadata retry (device/heavy) chỉ tồn tại trong `HubJobManager` của
  phiên hiện tại và vẫn bị giới hạn 64 specs.

## Artifact download và upload

### Download

`GET /api/artifacts/{opaque-id}` stream file từ owned root theo chunk 1 MiB;
không gọi `Path.read_bytes()` cho artifact. Nó hỗ trợ một byte range RFC-style:

| Request | Response |
| --- | --- |
| Không có `Range` | `200`, `Accept-Ranges: bytes`, exact `Content-Length` |
| `bytes=start-end`, `bytes=start-`, `bytes=-suffix` | `206`, `Content-Range`, exact bytes |
| Multi-range, malformed hoặc unsatisfiable | `416`, `Content-Range: bytes */<size>` |
| `HEAD` artifact | Header range/length tương ứng, không có body |

Artifact vẫn chỉ dùng opaque ID, owned-root validation, filename header đã
sanitize và không trả local path. Client disconnect được xem là request kết
thúc bình thường, không làm chết loopback server.

### Upload

`POST /api/uploads` bắt buộc `Content-Length`. Handler stream tối đa 4 MiB mỗi
lần vào `Temp/uploads/<opaque>.part`, tính SHA-256 trong lúc ghi rồi atomic
rename/register khi size chính xác. Default giới hạn là 8 GiB;
`upload_max_bytes` và `upload_disk_safety_bytes` được mô tả trong
[`Config/hub_config.example.json`](../Config/hub_config.example.json). Hub
preflight free disk trước khi ghi.

Short body, disconnect, digest/write/register failure hoặc length không hợp lệ
chỉ xóa `.part`/final token do request đó tạo; không đụng source, output hoặc
upload của request khác. Public response chỉ trả opaque artifact metadata, size,
media type và SHA-256, không trả filesystem path.

## Bounded Node Studio state

`NodeCache` là LRU thực sự, tối đa 256 entry; `get` làm mới recency và entry có
artifact không còn hợp lệ bị loại. Eviction chỉ bỏ reference cache, không xóa
artifact file. `GraphRunRegistry` giữ toàn bộ run active và 100 terminal run
mới nhất; snapshot/provenance tiếp tục path-safe.

Node Studio bật `multi_select=true`: plain click có thể cộng dồn selection;
Ctrl/Shift vẫn cộng dồn theo LiteGraph. Bấm canvas trống, `Esc` hoặc **Bỏ chọn**
luôn xóa selection; group drag và Delete áp dụng trên toàn bộ nhóm được chọn.

## ComfyUI Advanced: acceptance vẫn partial

ComfyUI Advanced không được tuyên bố operational chỉ vì API/iframe tồn tại. Nó
giữ `partial` cho đến khi một session Windows WebView thật pass toàn bộ checklist
sau. Checklist này **deferred** trong resource override hiện tại: không launch
hoặc inspect ComfyUI/GPU để tick hộ.

| Hạng mục manual acceptance | Evidence cần lưu trong QA record |
| --- | --- |
| Iframe canvas render trong single window | Screenshot/redacted note và build SHA |
| WebSocket connect/reconnect | Console/network result không chứa secret/path |
| Load workflow + drag socket + shortcut | Từng thao tác pass/fail |
| Queue một tác vụ an toàn đã được cho phép | Job state/provenance; không dùng video/GPU khi override còn hiệu lực |
| File upload + sandbox restrictions | Opaque artifact contract và iframe sandbox result |
| Back to Hub | Navigation/UI còn responsive, không mở browser ngoài |

## Windows lifecycle smoke (opt-in)

`tests/windows_lifecycle_smoke.py --run` khởi động child `pythonw` không console,
HTTP fixture loopback và CPU dummy do chính test sở hữu. Nó không import API
production, không gọi `/api/bootstrap`, không probe GPU và không chạy model,
FFmpeg hay media worker. Test chỉ chạy nếu port `8765` trống; nếu port đang có
owner khác, nó trả `deferred` thay vì kiểm tra hay can thiệp owner đó.

Mỗi lượt pass thực hiện Windows WebView thật và kiểm tra: cold start; desktop
instance thứ hai nhìn API là external; close zero-active; active-close bị veto;
background chỉ sau tray registration, Restore; cancel-and-exit làm CPU worker
tự kết thúc trước cleanup; tray đăng ký → gỡ → đăng ký lại; và listener fixture
đã được giải phóng. Kết quả JSON không chứa path, PID, username, secret, model,
media hay GPU telemetry.

Quan sát trực quan "không console flash" vẫn nên ghi một lần trong QA record
nếu môi trường Windows cho phép: child của harness dùng `pythonw` cùng hidden
process policy, nhưng ảnh chụp/ghi chú manual là bằng chứng trực quan bổ sung,
không phải bằng chứng capability ComfyUI.

## Bounded verification

```powershell
cd D:\LocalAIHub
python -m unittest -v tests\test_reliability_hardening.py tests\test_startup_lifecycle.py
node --check src\ui\app.js
node --check src\ui\node_studio.js
& 'D:\LocalAIHub\Environments\hub\Scripts\python.exe' tests\windows_lifecycle_smoke.py --run
python scripts\ci_validate.py
git diff --check
```

Những lệnh này không benchmark và không khởi chạy video/GPU workload. HTTP/UI
smoke sau cùng chỉ dùng loopback + artifact byte tổng hợp nhỏ hoặc CPU dummy
worker đã xác định ownership.
