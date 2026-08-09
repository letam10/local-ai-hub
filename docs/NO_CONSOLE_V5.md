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

Launcher diagnose_local_ai_hub.cmd là chẩn đoán có chủ đích và có thể giữ
console mở để đọc lỗi. Nó không phải normal-use entrypoint.
