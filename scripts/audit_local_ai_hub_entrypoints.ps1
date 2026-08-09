[CmdletBinding()]
param(
    [string[]]$ScanRoot = @(),
    [switch]$IncludeDDrive,
    [switch]$AsJson
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = [Environment]::GetFolderPath('Desktop')
$commonDesktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$startMenu = [Environment]::GetFolderPath('StartMenu')
$commonStartMenu = [Environment]::GetFolderPath('CommonStartMenu')
if (-not $ScanRoot) {
    $ScanRoot = @($root, $desktop, $commonDesktop, $startMenu, $commonStartMenu)
}
if ($IncludeDDrive -and (Test-Path -LiteralPath 'D:\')) { $ScanRoot += 'D:\' }
$ScanRoot = @($ScanRoot | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)

$shell = New-Object -ComObject WScript.Shell
$needle = [regex]::Escape($root)
$shortcuts = @()
$seenShortcutPaths = New-Object System.Collections.Generic.HashSet[string]([System.StringComparer]::OrdinalIgnoreCase)
foreach ($scan in $ScanRoot) {
    $shortcutPaths = if ($scan -eq 'D:\' -and (Get-Command rg.exe -ErrorAction SilentlyContinue)) {
        @(& rg.exe --files --glob '*.lnk' $scan 2>$null)
    } else {
        @(Get-ChildItem -LiteralPath $scan -Recurse -Force -File -Filter '*.lnk' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    }
    foreach ($shortcutPath in $shortcutPaths) {
        if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf) -or -not $seenShortcutPaths.Add([string]$shortcutPath)) { continue }
        try { $link = $shell.CreateShortcut($shortcutPath) } catch { continue }
        $target = [string]$link.TargetPath
        $arguments = [string]$link.Arguments
        $working = [string]$link.WorkingDirectory
        $icon = [string]$link.IconLocation
        $name = [System.IO.Path]::GetFileName($shortcutPath)
        $isHub = $name -match '(?i)local\s*ai\s*hub' -or $target -match $needle -or $arguments -match $needle -or $working -match $needle -or $icon -match $needle
        if (-not $isHub) { continue }
        $normal = $name -match '(?i)^local\s*ai\s*hub\.lnk$'
        $problems = New-Object System.Collections.Generic.List[string]
        $warnings = New-Object System.Collections.Generic.List[string]
        if ($target -match '(?i)launch_local_ai_hub\.cmd') { $problems.Add('shortcut targets retired normal cmd launcher') }
        if ($normal -and $target -match '(?i)\.cmd$|\.bat$') { $problems.Add('normal shortcut targets a cmd/bat launcher') }
        if ($normal -and $target -match '(?i)\\python\.exe$') { $problems.Add('normal shortcut targets python.exe console host') }
        if ($arguments -match '(?i)launch_local_ai_hub\.cmd') { $problems.Add('shortcut arguments reference retired normal cmd launcher') }
        if ($icon -match '(?i)launch_local_ai_hub\.cmd') { $problems.Add('shortcut icon still references retired cmd launcher') }
        if ($normal -and $target -notmatch '(?i)\\pythonw\.exe$') { $problems.Add('normal shortcut must target pythonw.exe or a GUI-subsystem launcher') }
        if (-not $normal -and $target -match '(?i)\.cmd$|\.bat$') { $warnings.Add('legacy standalone shortcut targets a cmd/bat launcher; it is outside the normal Hub route') }
        $shortcuts += [pscustomobject]@{
            path = $shortcutPath
            target = $target
            arguments = $arguments
            working_directory = $working
            icon = $icon
            normal = $normal
            problems = @($problems)
            warnings = @($warnings)
        }
    }
}

$runEntries = @()
foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run')) {
    if (-not (Test-Path -LiteralPath $key)) { continue }
    $properties = Get-ItemProperty -LiteralPath $key
    foreach ($property in $properties.PSObject.Properties) {
        if ($property.Name -like 'PS*') { continue }
        $value = [string]$property.Value
        if ($value -match $needle -or $value -match '(?i)local\s*ai\s*hub') {
            $runEntries += [pscustomobject]@{ registry_key = $key; name = $property.Name; value = $value }
        }
    }
}

$scriptEntries = Get-ChildItem -LiteralPath (Join-Path $root 'scripts') -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in '.cmd', '.bat', '.ps1', '.py' } |
    ForEach-Object {
        $content = Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue
        if ($content -match '(?i)local\s*ai\s*hub|src\.app\.main|api_server') {
            [pscustomobject]@{ path = $_.FullName; extension = $_.Extension; diagnostic = $_.Name -match '(?i)diagnose|audit'; references_retired_cmd = $content -match '(?i)launch_local_ai_hub\.cmd' }
        }
    }

$problems = @($shortcuts | ForEach-Object { $_.problems } | Where-Object { $_ })
$result = [pscustomobject]@{
    status = if ($problems.Count) { 'error' } else { 'completed' }
    root = $root
    scan_roots = $ScanRoot
    normal_shortcuts = @($shortcuts | Where-Object { $_.normal })
    related_shortcuts = @($shortcuts)
    registry_entries = @($runEntries)
    script_entrypoints = @($scriptEntries)
    problems = $problems
    warnings = @($shortcuts | ForEach-Object { $_.warnings } | Where-Object { $_ })
    policy = 'Normal Local AI Hub shortcuts must target pythonw.exe or a GUI-subsystem launcher; cmd/python.exe launchers are diagnostic only.'
}
if ($AsJson) { $result | ConvertTo-Json -Depth 7 } else { $result | Format-List }
if ($problems.Count) { exit 1 }
