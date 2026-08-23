[CmdletBinding()]
param([string]$DataRoot)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($DataRoot)) { $DataRoot = Join-Path $root 'Temp\dev-data' }
$resolvedData = [IO.Path]::GetFullPath($DataRoot)
if ($resolvedData -notmatch '(?i)\\Temp(?:\\|$)' -or $resolvedData -match '(?i)\\\.git(?:\\|$)') { throw 'DEV_DATA_ROOT_MUST_BE_TASK_TEMP' }
$env:LOCALAIHUB_APP_ROOT = $root
$env:LOCALAIHUB_DATA_ROOT = $resolvedData
$env:PYTHONPATH = $root
& (Get-Command python.exe -ErrorAction Stop).Source -m src.app.launcher
exit $LASTEXITCODE
