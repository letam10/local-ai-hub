[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$DataRoot
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $root 'Environments\hub\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($candidate) { $python = $candidate.Source }
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Output 'MISSING_RUNTIME Local AI Hub requires a Hub-capable Python interpreter.'
    exit 2
}
$arguments = @('-B', (Join-Path $root 'scripts\setup_local_ai_hub.py'))
if ($Apply) { $arguments += '--apply' }
if ($DataRoot) { $arguments += @('--data-root', $DataRoot) }
& $python @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Apply) {
    & (Join-Path $root 'scripts\update_managed_shortcuts.ps1') -Apply
}
