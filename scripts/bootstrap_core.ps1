param(
    [switch]$NoConfig
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$resolver = Join-Path $PSScriptRoot 'resolve_core_runtime.ps1'
$pythonPath = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $resolver -Mode Python
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pythonPath)) {
    Write-Output '{"schema_version":"core-bootstrap.v2","status":"unavailable","reason":"core_python_missing","execution":"not_run","next_action":"Install a supported Python 3.10+ runtime or review the managed Core runtime plan."}'
    exit 2
}
$env:LOCALAIHUB_APP_ROOT = $root.Path
$env:LOCALAIHUB_DATA_ROOT = if ($env:LOCALAIHUB_DATA_ROOT) { $env:LOCALAIHUB_DATA_ROOT } else { $root.Path }
if ($NoConfig) {
    & $pythonPath -B (Join-Path $PSScriptRoot "bootstrap_core.py") --no-config
} else {
    & $pythonPath -B (Join-Path $PSScriptRoot "bootstrap_core.py")
}
exit $LASTEXITCODE
