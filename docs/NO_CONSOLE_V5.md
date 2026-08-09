# Launcher và console V5

Shortcut Local AI Hub chuẩn ở Desktop và thư mục gốc phải gọi
Environments\hub\Scripts\pythonw.exe với tham số -m src.app.main. Đây là route
giao diện duy nhất; không dùng launcher CMD cũ hoặc python.exe console.

Cập nhật shortcut chuẩn:

    pwsh -File scripts/update_managed_shortcuts.ps1 -Apply

Kiểm tra shortcut, Registry Run và script entrypoint:

    pwsh -File scripts/audit_local_ai_hub_entrypoints.ps1 -IncludeDDrive

Audit phân biệt shortcut Hub chuẩn và shortcut Studio legacy. Shortcut legacy
được báo warning nếu còn gọi CMD, nhưng không được tự đổi target, di chuyển hay
xóa trong công việc Hub normal route.

Mọi subprocess do Hub sở hữu đi qua helper Windows chung với CREATE_NO_WINDOW,
SW_HIDE, shell false và stdin vô hiệu hóa. Chính sách này áp dụng cho API,
tasklist, nvidia-smi, FFmpeg, worker, ComfyUI và taskkill. SW_MINIMIZE chỉ là
fallback được ghi rõ cho third-party đã chứng minh không thể ẩn; hiện không có
backend Hub nào dùng fallback đó.

Đây là chính sách cửa sổ cho tiến trình con do Hub sở hữu, không phải bản sửa
cho PowerShell hoặc Windows Terminal. Không được báo “đã sửa PowerShell” chỉ
vì đã thêm `CREATE_NO_WINDOW` hoặc `SW_HIDE`. Khi còn hiện tượng console nháy,
phải kiểm tra runtime theo PID, ParentPID, executable, command line và HWND để
phân biệt cây Hub với tiến trình ngoài Hub.

Route `/health` chỉ kiểm tra readiness, không gọi `nvidia-smi` mỗi lần UI polling. Snapshot GPU
chỉ được probe khi bootstrap/refresh cần nó, nên không tạo chuỗi process console lặp lại trong lúc
khởi động. API loopback dùng listener exclusive và mutex startup để tránh nhiều instance cùng
nhận cổng `127.0.0.1:8765`.

Đóng cửa sổ Hub không yêu cầu xác nhận. Nếu desktop shell tự khởi tạo API, nó chỉ dừng
cây API đó bằng Windows API native, không gọi `taskkill`/PowerShell và không tác động tiến
trình người dùng bên ngoài Hub.

Launcher diagnose_local_ai_hub.cmd là chẩn đoán có chủ đích và có thể giữ
console mở để đọc lỗi. Nó không phải normal-use entrypoint.
