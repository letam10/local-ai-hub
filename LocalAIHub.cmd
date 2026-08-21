@echo off
rem FILE NOTE
rem - Mục đích: Command-line launcher cho Local AI Hub (hỗ trợ dynamic pythonw resolution)
rem - Liên kết trực tiếp: src/app/launcher.py, LocalAIHub.vbs
rem - Vùng ảnh hưởng khi sửa: Khởi động ứng dụng từ cmd/PowerShell
setlocal
cd /d "%~dp0"
set "PYTHONW="
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\resolve_core_runtime.ps1" -Mode Pythonw`) do if not defined PYTHONW set "PYTHONW=%%P"
if defined PYTHONW (
    start "" "%PYTHONW%" -m src.app.launcher %*
) else (
    echo Local AI Hub Core runtime is unavailable. Run scripts\bootstrap_core.ps1 first.
    exit /b 2
)
