[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (Test-Path -LiteralPath (Join-Path $root 'Config\local.env.cmd')) {
    # The .cmd file is intentionally not dot-sourced; use the existing process
    # environment or the Hub venv selected below.
}
$python = if ($env:LOCALAIHUB_PYTHON) { $env:LOCALAIHUB_PYTHON } else { 'python' }
$hubPython = Join-Path $root 'Environments\hub\Scripts\python.exe'
if (Test-Path -LiteralPath $hubPython) { $python = $hubPython }
$env:PYTHONPATH = $root
& $python (Join-Path $root 'scripts\ensure_api.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Start-Process 'http://127.0.0.1:8765/ui/'
