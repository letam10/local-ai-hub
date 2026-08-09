[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = Join-Path $root 'Config\layout_migration.local.json'

if (($DryRun -and $Apply) -or ($DryRun -and $Rollback) -or ($Apply -and $Rollback)) {
    throw 'Choose exactly one of -DryRun, -Apply or -Rollback.'
}
if (-not ($DryRun -or $Apply -or $Rollback)) { $DryRun = $true }
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Migration manifest not found: $manifestPath. Create Config/layout_migration.local.json first."
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$entries = @($manifest.entries)
$drive = Get-PSDrive -Name ((Split-Path -Qualifier $root).TrimEnd(':'))

function Get-SizeBytes([string]$path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return 0 }
    if ((Get-Item -LiteralPath $path).PSIsContainer) {
        return [int64]((Get-ChildItem -LiteralPath $path -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum)
    }
    return [int64](Get-Item -LiteralPath $path).Length
}

function Assert-ManagedDestination([string]$path) {
    $resolved = [IO.Path]::GetFullPath($path)
    $rootResolved = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($rootResolved, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Destination is outside the LocalAIHub root: $path"
    }
}

function Assert-ExplicitSource([string]$path) {
    if (-not $path) { return }
    $resolved = [IO.Path]::GetFullPath($path)
    $allowedRoots = @(
        ([IO.Path]::GetFullPath($root).TrimEnd('\') + '\'),
        'D:\AI\',
        'D:\AI_4K_TEMP\',
        'D:\ẢNH VIDEO - AI\'
    )
    foreach ($allowedRoot in $allowedRoots) {
        if ($resolved.StartsWith([string]$allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
            return
        }
    }
    throw "Source is outside the reviewed migration roots: $path"
}

function Write-Manifest {
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
}

function Set-EntryProperty($entry, [string]$name, $value) {
    $entry | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force
}

if ($Rollback) {
    foreach ($entry in ($entries | Where-Object { $_.status -eq 'verified' } | Sort-Object component -Descending)) {
        if (-not $entry.source_path -or -not $entry.destination_path) { continue }
        Assert-ExplicitSource $entry.source_path
        Assert-ManagedDestination $entry.destination_path
        $source = [IO.Path]::GetFullPath($entry.source_path)
        $destination = [IO.Path]::GetFullPath($entry.destination_path)
        if (Test-Path -LiteralPath $source -PathType Container) {
            Write-Output "ROLLBACK SKIP $($entry.component): legacy source already exists"
            continue
        }
        if (-not (Test-Path -LiteralPath $destination)) {
            Write-Output "ROLLBACK SKIP $($entry.component): destination missing"
            continue
        }
        Move-Item -LiteralPath $destination -Destination $source
        $entry.status = 'rolled_back'
        $entry.verified = $false
        Write-Output "ROLLBACK $($entry.component): $destination -> $source"
    }
    Write-Manifest
    exit 0
}

foreach ($entry in $entries) {
    if ($entry.status -notin @('planned', 'needs_review')) {
        Write-Output "SKIP $($entry.component): status=$($entry.status)"
        continue
    }
    if (-not $entry.source_path) {
        Write-Output "SKIP $($entry.component): source path is not installed or not discovered"
        continue
    }
    Assert-ExplicitSource $entry.source_path
    Assert-ManagedDestination $entry.destination_path
    $source = [IO.Path]::GetFullPath($entry.source_path)
    $destination = [IO.Path]::GetFullPath($entry.destination_path)
    $sourceBytes = Get-SizeBytes $source
    $freeBytes = [int64]$drive.Free
    $temporaryBytes = if ($entry.move_strategy -match 'atomic') { 0 } else { $sourceBytes }
    Set-EntryProperty $entry 'size_bytes_observed' $sourceBytes
    Set-EntryProperty $entry 'temporary_required_bytes' $temporaryBytes
    Set-EntryProperty $entry 'projected_free_bytes' ($freeBytes - $temporaryBytes)

    if ($entry.status -eq 'needs_review') {
        Write-Output "REVIEW REQUIRED $($entry.component): manifest marks this operation for human review"
        continue
    }
    if (-not (Test-Path -LiteralPath $source)) {
        $entry.status = 'not_found'
        Write-Output "SKIP $($entry.component): source missing"
        continue
    }
    if (Test-Path -LiteralPath $destination) {
        $entry.status = 'blocked_destination_exists'
        Write-Output "SKIP $($entry.component): destination already exists"
        continue
    }
    if ($temporaryBytes -gt 2GB) {
        $entry.status = 'needs_review'
        Write-Output "REVIEW REQUIRED $($entry.component): duplicate would exceed 2 GB"
        continue
    }
    if ($entry.projected_free_bytes -lt 4GB) {
        $entry.status = 'blocked_low_disk'
        Write-Output "SKIP $($entry.component): projected free space is below 4 GB"
        continue
    }
    if (@($entry.processes_using_path).Count -gt 0) {
        $entry.status = 'blocked_processes'
        Write-Output "SKIP $($entry.component): manifest lists processes using the source"
        continue
    }
    if ($DryRun) {
        Write-Output ("DRYRUN {0}: {1} -> {2}; source={3:N0} bytes; projected_free={4:N0} bytes" -f $entry.component, $source, $destination, $sourceBytes, $entry.projected_free_bytes)
        continue
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Move-Item -LiteralPath $source -Destination $destination
    if (-not (Test-Path -LiteralPath $destination)) { throw "Destination verification failed for $($entry.component)" }
    $entry.status = 'verified'
    $entry.verified = $true
    Write-Output "APPLY $($entry.component): moved and verified"
    if ($entry.legacy_junction_required -and -not (Test-Path -LiteralPath $source)) {
        New-Item -ItemType Junction -Path $source -Target $destination | Out-Null
        Write-Output "JUNCTION $source -> $destination"
    }
}

if ($Apply) { Write-Manifest }
