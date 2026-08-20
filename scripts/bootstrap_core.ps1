param(
    [switch]$NoConfig
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Write-Output '{"status":"unavailable","reason":"python_missing","execution":"not_run"}'
    exit 2
}
$env:LOCALAIHUB_APP_ROOT = $root.Path
if ($NoConfig) {
    & $python.Source -B (Join-Path $PSScriptRoot "bootstrap_core.py") --no-config
} else {
    & $python.Source -B (Join-Path $PSScriptRoot "bootstrap_core.py")
}
exit $LASTEXITCODE
