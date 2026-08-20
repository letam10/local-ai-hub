@echo off
rem FILE NOTE
rem - Mục đích: Command-line launcher cho Local AI Hub (hỗ trợ dynamic pythonw resolution)
rem - Liên kết trực tiếp: src/app/launcher.py, LocalAIHub.vbs
rem - Vùng ảnh hưởng khi sửa: Khởi động ứng dụng từ cmd/PowerShell
setlocal
cd /d "%~dp0"
if exist "%~dp0Environments\hub\Scripts\pythonw.exe" (
    start "" "%~dp0Environments\hub\Scripts\pythonw.exe" -m src.app.launcher %*
) else if exist "%~dp0runtime\bootstrap-python\pythonw.exe" (
    start "" "%~dp0runtime\bootstrap-python\pythonw.exe" -m src.app.launcher %*
) else (
    start "" pythonw -m src.app.launcher %*
)
