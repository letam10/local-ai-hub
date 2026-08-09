# Phân phối Core và Module V5

Kho Git giữ mã nguồn nhỏ, không phải bộ cài đặt đầy đủ. Asset phát hành dự kiến
có tên LocalAIHub-Core-Win64.zip và chỉ chứa launcher, frontend, API, runtime
bootstrap nhỏ, wheelhouse nhỏ, checksum và license. Mục tiêu của asset Core là
50 đến 200 MiB.

Core tuyệt đối không chứa Torch, Paddle, CUDA, model, environment, cache,
output, media cá nhân hoặc bí mật. Không được thêm dữ liệu đệm để làm đủ kích
thước mục tiêu. Nếu runtime bootstrap và wheelhouse đã review chưa sẵn sàng,
không tạo asset phát hành; báo cáo dry-run sẽ cho thấy kích thước input thực tế.

Core manifest nằm tại distribution/core.manifest.json. Lệnh dưới đây chỉ
kiểm tra nguồn và in ước lượng; nó không tạo zip:

    python scripts/build_core_release.py

Một build thật bắt buộc cung cấp rõ cả runtime và wheelhouse staging đã review:

    python scripts/build_core_release.py --runtime-dir D:\ReleaseStaging\bootstrap-python --wheelhouse-dir D:\ReleaseStaging\wheelhouse --build

Build sẽ từ chối payload có model, CUDA, Torch, Paddle, environment hoặc cache;
cũng từ chối archive nằm ngoài khoảng 50 đến 200 MiB. Nó không ghi đè output
nếu không có cờ overwrite rõ ràng.

Module SAM2, Vision, Voice, Image và AnimeSR là on-demand. Manifest
distribution/modules.manifest.json bắt buộc URL HTTPS và SHA-256 64 ký tự trước
khi một module được phép tải. Hiện các module chưa phát hành có URL và SHA-256
đã xác minh nên installer sẽ báo unavailable, không đoán URL và không tải gì.

Installer mặc định chỉ lập kế hoạch:

    python scripts/bootstrap_installer.py --mode minimal
    python scripts/bootstrap_installer.py --mode module --module sam2
    python scripts/bootstrap_installer.py --mode full

Cờ apply mới cho phép tải module đã có URL và SHA-256 hợp lệ. Chế độ Full chỉ
chọn mọi module; nó không cho phép tự tải model. Model vẫn cần một manifest đã
review và xác nhận rõ của người dùng. Module cài vào thư mục Modules bị Git bỏ
qua; không làm thay đổi cài đặt legacy hiện có.
