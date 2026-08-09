@echo off
setlocal
set "LOCALAIHUB_ROOT=%~dp0.."
for %%I in ("%LOCALAIHUB_ROOT%") do set "LOCALAIHUB_ROOT=%%~fI"
if exist "%LOCALAIHUB_ROOT%\Config\local.env.cmd" call "%LOCALAIHUB_ROOT%\Config\local.env.cmd"
if not defined LOCALAIHUB_PYTHON set "LOCALAIHUB_PYTHON=python"
if exist "%LOCALAIHUB_ROOT%\Environments\hub\Scripts\python.exe" set "LOCALAIHUB_PYTHON=%LOCALAIHUB_ROOT%\Environments\hub\Scripts\python.exe"
set "PYTHONPATH=%LOCALAIHUB_ROOT%"
"%LOCALAIHUB_PYTHON%" "%LOCALAIHUB_ROOT%\scripts\ensure_api.py"
if errorlevel 1 exit /b %errorlevel%
start "Local AI Hub Web UI" "http://127.0.0.1:8765/ui/"
endlocal
