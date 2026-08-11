[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('sam2', 'animesr')]
    [string]$Component,
    [switch]$Apply,
    [switch]$Verify,
    [switch]$RecordFunctionalSmoke,
    [string]$SmokeEvidence,
    [switch]$RemoveLegacy
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$reports = Join-Path $root 'Reports'
$statePath = Join-Path $root 'Config\environment_migration_v3.local.json'
$maps = @{
    sam2 = @{ source = 'D:\AI_4K_TEMP\sam2_venv'; destination = (Join-Path $root 'Environments\sam2'); imports = @('torch', 'sam2'); runtime = (Join-Path $root 'runtime\engines\vision\SAM2'); pytorch_index = 'https://download.pytorch.org/whl/cu124' }
    animesr = @{ source = 'D:\AI_4K_TEMP\animesr_venv'; destination = (Join-Path $root 'Environments\animesr'); imports = @('torch', 'animesr'); runtime = (Join-Path $root 'runtime\engines\video\AnimeSR'); pytorch_index = 'https://download.pytorch.org/whl/cu118' }
}
$map = $maps[$Component]
$source = [IO.Path]::GetFullPath($map.source)
$destination = [IO.Path]::GetFullPath($map.destination)
$sourcePython = Join-Path $source 'Scripts\python.exe'
$destinationPython = Join-Path $destination 'Scripts\python.exe'

if (Test-Path -LiteralPath $statePath -PathType Leaf) {
    try { $completedState = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json } catch { $completedState = $null }
    if ($completedState -and $completedState.$Component -and $completedState.$Component.status -eq 'legacy_removed') {
        Write-Output "V3 environment migration is historical for $Component; the canonical replacement is already recorded and the legacy environment remains absent."
        exit 0
    }
}

function Get-ProcessReference([string]$path) {
    $needle = [IO.Path]::GetFullPath($path).TrimEnd('\')
    try {
        return @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            ([string]$_.ExecutablePath).IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ([string]$_.CommandLine).IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } | Select-Object ProcessId, Name, ExecutablePath, CommandLine)
    } catch { return @() }
}

function Save-State([string]$status, [string]$note) {
    $existing = @{}
    if (Test-Path -LiteralPath $statePath -PathType Leaf) {
        try { $existing = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable } catch { $existing = @{} }
    }
    $existing[$Component] = @{ source = $source; destination = $destination; status = $status; note = $note; updated_at = (Get-Date).ToUniversalTime().ToString('o') }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($statePath, ($existing | ConvertTo-Json -Depth 8) + [Environment]::NewLine, $utf8)
}

if (-not (Test-Path -LiteralPath $source -PathType Container) -or -not (Test-Path -LiteralPath $sourcePython -PathType Leaf)) {
    throw "Legacy environment is incomplete: $source"
}
$sourceItem = Get-Item -LiteralPath $source -Force
if (($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to migrate a reparse point instead of a real environment: $source"
}
$processes = @(Get-ProcessReference $source)
$size = (Get-ChildItem -LiteralPath $source -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
$free = (Get-PSDrive -Name D).Free

if ($RecordFunctionalSmoke) {
    if ([string]::IsNullOrWhiteSpace($SmokeEvidence)) { throw 'Provide a concise bounded direct-smoke evidence note before recording it.' }
    if (-not (Test-Path -LiteralPath $destinationPython -PathType Leaf)) { throw "Replacement environment is missing: $destination" }
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'No V3 environment verification state exists.' }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($state.$Component.status -notin @('verified', 'functional_smoke_passed')) { throw "Replacement imports are not verified for $Component" }
    Save-State 'functional_smoke_passed' ("Bounded direct smoke recorded: " + $SmokeEvidence)
    Write-Output "RECORDED FUNCTIONAL SMOKE: $destination"
    exit 0
}

if ($RemoveLegacy) {
    if ($processes.Count -gt 0) { throw "Refusing to delete active legacy environment $source" }
    if (-not (Test-Path -LiteralPath $destinationPython -PathType Leaf)) { throw "Replacement environment is missing: $destination" }
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { throw 'No V3 environment verification state exists.' }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
    if ($state.$Component.status -ne 'functional_smoke_passed') { throw "Replacement has no recorded bounded direct smoke: $Component" }
    Write-Output "DRYRUN DELETE LEGACY ENVIRONMENT: $source"
    if ($Apply) {
        Remove-Item -LiteralPath $source -Recurse -Force
        Save-State 'legacy_removed' 'Replacement was verified and old real environment was removed.'
        Write-Output "REMOVED LEGACY ENVIRONMENT: $source"
    }
    exit 0
}

if ($Verify) {
    if (-not (Test-Path -LiteralPath $destinationPython -PathType Leaf)) { throw "Replacement environment is missing: $destination" }
    $importLine = ($map.imports | ForEach-Object { "import $_" }) -join '; '
    & $destinationPython -c $importLine
    if ($LASTEXITCODE -ne 0) { throw "Replacement import verification failed for $Component" }
    Save-State 'verified' 'Python imports passed; direct functional smoke must still be recorded before tool status is operational.'
    Write-Output "VERIFIED REPLACEMENT ENVIRONMENT: $destination"
    exit 0
}

Write-Output "Component: $Component"
Write-Output "Source: $source"
Write-Output "Destination: $destination"
Write-Output "Source bytes: $size"
Write-Output "Free bytes: $free"
if ($processes.Count -gt 0) {
    Write-Output "BLOCKED ACTIVE PROCESSES: $($processes.Count)"
    $processes | ForEach-Object { Write-Output "PID $($_.ProcessId): $($_.Name)" }
    Save-State 'blocked_active_process' 'A process still references the legacy environment; no rebuild or deletion was started.'
    exit 2
}
if (Test-Path -LiteralPath $destination) {
    throw "Replacement destination already exists: $destination"
}
if (($free - [int64]$size) -lt 4GB) {
    throw 'Projected free disk after rebuilding is below the 4 GB safety threshold.'
}
if (-not $Apply) {
    Write-Output "DRYRUN REBUILD: $source -> $destination"
    Write-Output 'No environment has been created, copied, moved or deleted.'
    exit 0
}

New-Item -ItemType Directory -Force -Path $reports | Out-Null
$freezePath = Join-Path $reports ("{0}_environment_freeze_v3.local.txt" -f $Component)
$requirementsPath = Join-Path $reports ("{0}_environment_requirements_v3.local.txt" -f $Component)
& $sourcePython -m pip freeze --all | Set-Content -LiteralPath $freezePath -Encoding utf8
Get-Content -LiteralPath $freezePath -Encoding utf8 | Where-Object {
    $_ -and $_ -notmatch '^(?:-e |.* @ file:|sam-2 @ file:)'
} | Set-Content -LiteralPath $requirementsPath -Encoding utf8

$bootstrap = Join-Path $root 'Environments\hub\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $bootstrap -PathType Leaf)) { $bootstrap = $sourcePython }
& $bootstrap -m venv $destination
if ($LASTEXITCODE -ne 0) { throw "Could not create replacement environment: $destination" }
& $destinationPython -m pip install --disable-pip-version-check --no-cache-dir --extra-index-url $map.pytorch_index -r $requirementsPath
if ($LASTEXITCODE -ne 0) {
    Save-State 'rebuild_failed' 'pip install did not complete; legacy environment remains untouched.'
    throw "Dependency installation failed. The legacy environment remains at $source"
}
& $destinationPython -m pip install --no-deps -e $map.runtime
if ($LASTEXITCODE -ne 0) {
    Save-State 'rebuild_failed' 'Editable canonical runtime install did not complete; legacy environment remains untouched.'
    throw "Canonical runtime install failed. The legacy environment remains at $source"
}
Save-State 'rebuilt_pending_verification' 'Replacement created; run -Verify, direct smoke, then update adapters before deletion.'
Write-Output "REBUILT REPLACEMENT ENVIRONMENT: $destination"
