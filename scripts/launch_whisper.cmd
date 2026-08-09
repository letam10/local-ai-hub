@echo off
setlocal
set "LOCALAIHUB_ROOT=%~dp0.."
for %%I in ("%LOCALAIHUB_ROOT%") do set "LOCALAIHUB_ROOT=%%~fI"
if exist "%LOCALAIHUB_ROOT%\Config\local.env.cmd" call "%LOCALAIHUB_ROOT%\Config\local.env.cmd"
if not defined WHISPER_HOME (
  echo WHISPER_HOME is not configured. Copy Config\local.env.example.cmd to Config\local.env.cmd and set it locally.
  exit /b 1
)
if not defined WHISPER_PYTHON set "WHISPER_PYTHON=%WHISPER_HOME%\Scripts\python.exe"
if not exist "%WHISPER_PYTHON%" (
  echo Whisper Python was not found: %WHISPER_PYTHON%
  exit /b 1
)
start "Whisper ASR existing scripts" /D "%WHISPER_HOME%" "%WHISPER_PYTHON%" "%WHISPER_HOME%\transcribe_japanese.py"
endlocal
