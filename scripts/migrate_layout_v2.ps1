[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Apply,
    [switch]$Rollback,
    [switch]$MarkVerified,
    [string[]]$Component
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = Join-Path $root 'Config\layout_migration.local.json'
$allowedClassifications = @('PORTABLE_APP', 'PORTABLE_ENGINE', 'MODEL_STORE', 'SHARED_RUNTIME')

$flags = @($DryRun, $Apply, $Rollback, $MarkVerified)
$operationCount = @($flags | Where-Object { $_ }).Count
if ($operationCount -gt 1) { throw 'Choose exactly one operation: -DryRun, -Apply, -Rollback or -MarkVerified.' }
if ($operationCount -eq 0) { $DryRun = $true }
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Migration manifest not found: $manifestPath"
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
$entries = @($manifest.entries)
$rootFull = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'

function Resolve-ExplicitPath([string]$value) {
    if (-not $value) { return $null }
    return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($value))
}

function Assert-ManagedDestination([string]$path) {
    $resolved = Resolve-ExplicitPath $path
    if (-not $resolved.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Destination is outside LocalAIHub: $path"
    }
    return $resolved
}

function Assert-ReviewedSource([string]$path) {
    $resolved = Resolve-ExplicitPath $path
    $videoAiRoot = 'D:\' + [char]0x1EA2 + 'NH VIDEO - AI\'
    $allowedRoots = @(
        $rootFull,
        'D:\AI\',
        'D:\AI_4K_TEMP\',
        $videoAiRoot,
        'D:\BLENDER\'
    )
    foreach ($allowedRoot in $allowedRoots) {
        if ($resolved.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) { return $resolved }
    }
    throw "Source is outside reviewed roots: $path"
}

function Get-SizeBytes([string]$path) {
    if (-not $path -or -not (Test-Path -LiteralPath $path)) { return [int64]0 }
    $item = Get-Item -LiteralPath $path -Force
    if (-not $item.PSIsContainer) { return [int64]$item.Length }
    $sum = (Get-ChildItem -LiteralPath $path -File -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return [int64]0 }
    return [int64]$sum
}

function Get-FreeBytes([string]$path) {
    $driveName = (Split-Path -Qualifier $path).TrimEnd(':')
    return [int64](Get-PSDrive -Name $driveName).Free
}

function Get-PathProcesses([string]$path) {
    $needle = (Resolve-ExplicitPath $path).TrimEnd('\')
    $result = @()
    try { $processes = Get-CimInstance Win32_Process -ErrorAction Stop } catch { return $result }
    foreach ($process in $processes) {
        if ([int]$process.ProcessId -eq $PID) { continue }
        $values = @([string]$process.ExecutablePath, [string]$process.CommandLine)
        foreach ($value in $values) {
            if ($value -and $value.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $result += [pscustomobject]@{ pid = [int]$process.ProcessId; name = [string]$process.Name; detail = $value }
                break
            }
        }
    }
    return $result
}

function Set-EntryProperty($entry, [string]$name, $value) {
    $entry | Add-Member -NotePropertyName $name -NotePropertyValue $value -Force
}

function Write-Manifest {
    $manifest.generated_at = (Get-Date).ToUniversalTime().ToString('o')
    $content = $manifest | ConvertTo-Json -Depth 12
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($manifestPath, $content + [Environment]::NewLine, $utf8NoBom)
}

function Assert-NoDestination([string]$destination) {
    if (Test-Path -LiteralPath $destination) {
        $item = Get-Item -LiteralPath $destination -Force
        throw "Destination already exists ($($item.Attributes)): $destination"
    }
}

function New-SafeJunction([string]$link, [string]$target) {
    $linkResolved = Assert-ManagedDestination $link
    $targetResolved = Assert-ReviewedSource $target
    if (-not (Test-Path -LiteralPath $targetResolved -PathType Container)) { throw "Junction target missing: $targetResolved" }
    Assert-NoDestination $linkResolved
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $linkResolved) | Out-Null
    New-Item -ItemType Junction -Path $linkResolved -Target $targetResolved | Out-Null
    if (-not (Test-Path -LiteralPath $linkResolved -PathType Container)) { throw "Junction verification failed: $linkResolved" }
}

if ($MarkVerified) {
    foreach ($entry in $entries) {
        if ($Component -and $entry.component -notin $Component) { continue }
        if ($entry.status -ne 'moved_pending_verification') { continue }
        $destination = Assert-ManagedDestination $entry.destination_path
        if (-not (Test-Path -LiteralPath $destination)) { throw "Cannot verify missing destination: $destination" }
        Set-EntryProperty $entry 'status' 'verified'
        Set-EntryProperty $entry 'verified' $true
        Set-EntryProperty $entry 'verified_at' (Get-Date).ToUniversalTime().ToString('o')
        Write-Output "MARK VERIFIED $($entry.component): bounded smoke evidence supplied by caller"
    }
    Write-Manifest
    exit 0
}

if ($Rollback) {
    foreach ($entry in ($entries | Sort-Object component -Descending)) {
        if ($Component -and $entry.component -notin $Component) { continue }
        $strategy = [string]$entry.move_strategy
        if ($strategy -match 'junction_only') {
            $link = Assert-ManagedDestination $entry.destination_path
            if (-not (Test-Path -LiteralPath $link)) { continue }
            $item = Get-Item -LiteralPath $link -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { throw "Rollback refused to remove a real directory: $link" }
            Remove-Item -LiteralPath $link -Force
            Set-EntryProperty $entry 'status' 'rolled_back'
            Set-EntryProperty $entry 'verified' $false
            Write-Output "ROLLBACK JUNCTION $($entry.component): $link"
            continue
        }
        if ($entry.status -notin @('verified', 'moved_pending_verification')) { continue }
        $source = Assert-ReviewedSource $entry.source_path
        $destination = Assert-ManagedDestination $entry.destination_path
        if (-not (Test-Path -LiteralPath $destination)) { continue }
        $processes = @(Get-PathProcesses $destination)
        if ($processes.Count -gt 0) { throw "Rollback refused while a process references $destination" }
        if (Test-Path -LiteralPath $source) {
            $legacy = Get-Item -LiteralPath $source -Force
            if (($legacy.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) { throw "Rollback refused to overwrite real legacy data: $source" }
            Remove-Item -LiteralPath $source -Force
        }
        Move-Item -LiteralPath $destination -Destination $source
        Set-EntryProperty $entry 'status' 'rolled_back'
        Set-EntryProperty $entry 'verified' $false
        Write-Output "ROLLBACK $($entry.component): $destination -> $source"
    }
    Write-Manifest
    exit 0
}

foreach ($entry in $entries) {
    if ($Component -and $entry.component -notin $Component) { continue }
    if ($entry.status -notin @('planned', 'needs_review')) {
        Write-Output "SKIP $($entry.component): status=$($entry.status)"
        continue
    }
    $classification = [string]$entry.classification
    $confidence = [string]$entry.confidence
    $strategy = [string]$entry.move_strategy
    if ($strategy -match 'junction_only') {
        if ($confidence -ne 'high') { Write-Output "SKIP $($entry.component): junction confidence is not high"; continue }
        $target = Assert-ReviewedSource $entry.source_path
        $link = Assert-ManagedDestination $entry.destination_path
        if ($DryRun) { Write-Output "DRYRUN JUNCTION $($entry.component): $link -> $target"; continue }
        New-SafeJunction $link $target
        Set-EntryProperty $entry 'status' 'verified'
        Set-EntryProperty $entry 'verified' $true
        Set-EntryProperty $entry 'verified_at' (Get-Date).ToUniversalTime().ToString('o')
        Write-Output "JUNCTION $($entry.component): $link -> $target"
        continue
    }
    if ($classification -notin $allowedClassifications -or $confidence -ne 'high') {
        Write-Output "REVIEW REQUIRED $($entry.component): only high-confidence portable entries are auto-migrated"
        continue
    }
    $source = Assert-ReviewedSource $entry.source_path
    $destination = Assert-ManagedDestination $entry.destination_path
    if (-not (Test-Path -LiteralPath $source)) {
        Set-EntryProperty $entry 'status' 'not_found'
        Write-Output "SKIP $($entry.component): source missing"
        continue
    }
    $sourceItem = Get-Item -LiteralPath $source -Force
    if ($sourceItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Output "SKIP $($entry.component): source is already a reparse point"
        continue
    }
    try { Assert-NoDestination $destination } catch { Set-EntryProperty $entry 'status' 'blocked_destination_exists'; Write-Output "SKIP $($entry.component): destination exists"; continue }
    $sameVolume = ((Split-Path -Qualifier $source).TrimEnd(':') -ieq (Split-Path -Qualifier $destination).TrimEnd(':'))
    Set-EntryProperty $entry 'same_volume' $sameVolume
    $sourceBytes = Get-SizeBytes $source
    $freeBytes = Get-FreeBytes $source
    $temporaryBytes = if ($sameVolume -and $strategy -match 'atomic') { [int64]0 } else { $sourceBytes }
    Set-EntryProperty $entry 'size_bytes_observed' $sourceBytes
    Set-EntryProperty $entry 'temporary_required_bytes' $temporaryBytes
    Set-EntryProperty $entry 'projected_free_bytes' ($freeBytes - $temporaryBytes)
    if (-not $sameVolume) { Set-EntryProperty $entry 'status' 'blocked_cross_volume'; Write-Output "SKIP $($entry.component): cross-volume move is not allowed"; continue }
    if (($freeBytes - $temporaryBytes) -lt 4GB) { Set-EntryProperty $entry 'status' 'blocked_low_disk'; Write-Output "SKIP $($entry.component): projected free space below 4 GB"; continue }
    $manifestProcesses = @($entry.processes_using_path)
    $liveProcesses = @(Get-PathProcesses $source)
    if ($manifestProcesses.Count -gt 0 -or $liveProcesses.Count -gt 0) {
        Set-EntryProperty $entry 'status' 'blocked_processes'
        Set-EntryProperty $entry 'processes_observed' $liveProcesses
        Write-Output "SKIP $($entry.component): relevant process references source"
        continue
    }
    if ($DryRun) {
        Write-Output ("DRYRUN {0}: {1} -> {2}; source={3:N0} bytes; projected_free={4:N0} bytes" -f $entry.component, $source, $destination, $sourceBytes, ($freeBytes - $temporaryBytes))
        continue
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
    Move-Item -LiteralPath $source -Destination $destination
    if (-not (Test-Path -LiteralPath $destination)) { throw "Destination verification failed for $($entry.component)" }
    Set-EntryProperty $entry 'status' 'moved_pending_verification'
    Set-EntryProperty $entry 'verified' $false
    Set-EntryProperty $entry 'moved_at' (Get-Date).ToUniversalTime().ToString('o')
    Write-Output "APPLY $($entry.component): moved and awaiting bounded smoke"
    if ($entry.legacy_junction_required -and -not (Test-Path -LiteralPath $source)) {
        New-Item -ItemType Junction -Path $source -Target $destination | Out-Null
        Write-Output "JUNCTION $source -> $destination"
    }
}

if ($Apply) { Write-Manifest }
