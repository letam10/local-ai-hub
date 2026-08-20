[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$RemoveObsoleteJunctions,
    [switch]$RemoveVerifiedEmptyFolders,
    [string[]]$ApprovedJunctionPath = @()
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$statePath = Join-Path $root 'Config\legacy_cleanup_v3.local.json'

if (-not $RemoveObsoleteJunctions -and -not $RemoveVerifiedEmptyFolders) {
    Write-Output 'DRYRUN ONLY: pass -RemoveObsoleteJunctions and/or -RemoveVerifiedEmptyFolders after inventory review.'
    exit 0
}
if ($Apply -and $RemoveObsoleteJunctions -and $ApprovedJunctionPath.Count -eq 0) {
    throw 'Applying a junction removal requires one or more exact -ApprovedJunctionPath values from the reviewed inventory.'
}
if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    throw "Missing V3 inventory state: $statePath. Run scripts\inventory_legacy_v3.py first."
}

$state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
if ($state.historical_snapshot -eq $true -or $state.not_runtime_configuration -eq $true) {
    throw 'V3 cleanup is historical on this completed host; no legacy path will be recreated or deleted by this helper.'
}
$junctions = @($state.records | Where-Object {
    $_.type -in @('JUNCTION', 'SYMLINK') -and
    $_.cleanup_state -eq 'JUNCTION_REFERENCE_AUDIT_REQUIRED' -and
    -not $_.active_process -and
    @($_.current_references).Count -eq 0
})
$emptyFolders = @($state.records | Where-Object {
    $_.type -eq 'REAL_DIRECTORY' -and
    $_.cleanup_state -eq 'EMPTY_LEGACY_FOLDER_SAFE_TO_DELETE' -and
    -not $_.active_process -and
    @($_.current_references).Count -eq 0
})
$approvedJunctions = @{}
foreach ($approved in $ApprovedJunctionPath) {
    if ([string]::IsNullOrWhiteSpace($approved)) { continue }
    $approvedJunctions[[IO.Path]::GetFullPath($approved)] = $true
}

if ($junctions.Count -eq 0 -and $emptyFolders.Count -eq 0) {
    Write-Output 'No verified empty folder or zero-reference obsolete junction candidate was found. No action taken.'
    exit 0
}

foreach ($candidate in $junctions) {
    if (-not $RemoveObsoleteJunctions) { continue }
    $path = [IO.Path]::GetFullPath([string]$candidate.path)
    $allowedRoots = @('D:\AI\', 'D:\AI_4K_TEMP\', ('D:\' + [char]0x1EA2 + 'NH VIDEO - AI\'))
    if (-not ($allowedRoots | Where-Object { $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })) {
        Write-Output "SKIP outside reviewed legacy roots: $path"
        continue
    }
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Output "SKIP missing: $path"
        continue
    }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        throw "Refusing to delete a real directory: $path"
    }
    if (-not $Apply) {
        Write-Output "DRYRUN REMOVE JUNCTION: $path"
        continue
    }
    if (-not $approvedJunctions.ContainsKey($path)) {
        throw "Refusing junction removal not listed in -ApprovedJunctionPath: $path"
    }
    Remove-Item -LiteralPath $path -Force
    Write-Output "REMOVED OBSOLETE JUNCTION: $path"
}

foreach ($candidate in $emptyFolders) {
    if (-not $RemoveVerifiedEmptyFolders) { continue }
    $path = [IO.Path]::GetFullPath([string]$candidate.path)
    $allowedRoots = @('D:\AI\', 'D:\AI_4K_TEMP\', ('D:\' + [char]0x1EA2 + 'NH VIDEO - AI\'))
    if (-not ($allowedRoots | Where-Object { $path.StartsWith($_, [StringComparison]::OrdinalIgnoreCase) })) {
        Write-Output "SKIP outside reviewed legacy roots: $path"
        continue
    }
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $item = Get-Item -LiteralPath $path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a junction as an empty real folder: $path"
    }
    if (@(Get-ChildItem -LiteralPath $path -Force).Count -ne 0) {
        throw "Refusing to remove a non-empty folder: $path"
    }
    if (-not $Apply) {
        Write-Output "DRYRUN REMOVE EMPTY LEGACY FOLDER: $path"
        continue
    }
    Remove-Item -LiteralPath $path -Force
    Write-Output "REMOVED EMPTY LEGACY FOLDER: $path"
}
