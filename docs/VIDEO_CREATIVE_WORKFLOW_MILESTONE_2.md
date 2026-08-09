# MILESTONE 2 — Video Creative Workflow

Milestone 2 mở rộng Node Studio cho video nhưng giữ trạng thái backend trung
thực. Luồng đã có bounded contract là:

```text
Video artifact + prompt tuỳ chọn
        ↓
Video Transform (FFmpeg allowlist)
        ↓
Video Upscale (FFmpeg fallback hoặc AnimeSR partial)
        ↓
Frame Interpolation → Encode → Preview → Save → Export
```

## Template và contract

- `workflows/video_creative_pipeline.json`: template thực thi cho video artifact;
  cần upload một video trước khi Run Graph.
- `workflows/video_generation_unavailable.json`: template prompt-to-video để
  kiểm tra contract; node `video_generate` trả `unavailable` có `reason` và
  `action` vì Hub chưa có adapter video generation local đã smoke.

Node mới:

- `video_transform`: nhận `VIDEO` và prompt `TEXT` tuỳ chọn, chỉ cho phép
  `resize`, `crop`, `rotate`, `fps`, `transcode` qua FFmpeg allowlist.
- Với `resize`, `height=-2` được giữ nguyên như sentinel bảo toàn tỷ lệ của
  FFmpeg; template vì vậy không làm méo tỷ lệ khung hình.
- `video_upscale`: nhận `VIDEO`, chọn `ffmpeg_scale` hoặc `animesr`; fallback
  FFmpeg được gắn metadata `ai_upscaler=false`, không gọi nhầm là AI upscale.
- `video_generate`: prompt `TEXT` bắt buộc, status `unavailable` cho đến khi
  backend generation có bounded smoke.

Run snapshot dùng `node-run.v2`, public job dùng `job.v2`. Mỗi node cập nhật
progress, error và `next_action`; provenance chỉ chứa artifact ID, URL loopback,
media type và node tạo output. State boundary còn scrub raw path nếu caller nội
bộ truyền output chưa publicize.

## Backend availability

| Backend | Status | Ghi chú |
| --- | --- | --- |
| FFmpeg transform | `partial` | Có allowlist; cần video smoke trên máy đích. |
| FFmpeg video scale fallback | `partial` | Có contract, không phải AI upscaler. |
| AnimeSR direct worker | `partial` | Giữ installation hiện có; không tải/migrate model. |
| Video generation | `unavailable` | Chưa có adapter local đã bounded smoke. |
| Practical-RIFE | `partial` | Fallback `ffmpeg_minterpolate` hiển thị riêng. |

## Kiểm thử bounded

Trong lúc phát triển chỉ chạy targeted registry/schema/engine/UI tests. Cuối
milestone chạy một lượt full bounded suite, HTTP/UI smoke và một video fallback
smoke với input nhỏ nếu FFmpeg canonical sẵn sàng. Không benchmark, không chạy
GPU nặng song song, không đổi CUDA/NVIDIA driver và không commit model/output.
