@echo off
setlocal
set "LOCALAIHUB_ROOT=%~dp0.."
for %%I in ("%LOCALAIHUB_ROOT%") do set "LOCALAIHUB_ROOT=%%~fI"
if exist "%LOCALAIHUB_ROOT%\Config\local.env.cmd" call "%LOCALAIHUB_ROOT%\Config\local.env.cmd"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_animesr.ps1" -Executable "%ANIMESR_EXECUTABLE%"
endlocal
