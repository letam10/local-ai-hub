[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$AppRoot,
    [string[]]$Locations,
    [switch]$MigrationSucceeded
)

$ErrorActionPreference = 'Stop'

function Get-InstalledRoot {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { $Value = $env:LOCALAIHUB_INSTALL_ROOT }
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'INSTALLED_PRODUCT_ROOT_REQUIRED' }
    $rawItem = Get-Item -LiteralPath $Value -Force -ErrorAction Stop
    if ($rawItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'INSTALLED_PRODUCT_ROOT_REPARSE' }
    $resolved = (Resolve-Path -LiteralPath $Value -ErrorAction Stop).Path
    $normalized = $resolved.TrimEnd('\')
    if ($normalized -match '(?i)\\Temp(?:\\|$)' -or $normalized -match '(?i)\\\.git(?:\\|$)' -or $normalized -match '(?i)\\(?:test|tests|worktree)(?:\\|$)') { throw 'INSTALLED_PRODUCT_ROOT_REFUSED' }
    $cursor = Get-Item -LiteralPath $normalized -Force
    while ($null -ne $cursor) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'INSTALLED_PRODUCT_ROOT_REPARSE' }
        if ($cursor.Parent -eq $null) { break }
        $cursor = Get-Item -LiteralPath $cursor.Parent.FullName -Force
    }
    return $normalized
}

function Read-InstalledProduct {
    param([string]$Root)
    $productPath = Join-Path $Root 'product.json'
    $installationPath = Join-Path $Root 'installation.json'
    $launcherPath = Join-Path $Root 'LocalAIHub.exe'
    $internalPath = Join-Path $Root '_internal'
    $iconPath = Join-Path $Root 'local-ai-hub.ico'
    if (-not (Test-Path -LiteralPath $productPath -PathType Leaf) -or -not (Test-Path -LiteralPath $installationPath -PathType Leaf) -or -not (Test-Path -LiteralPath $launcherPath -PathType Leaf) -or -not (Test-Path -LiteralPath $iconPath -PathType Leaf)) { throw 'INSTALLED_PRODUCT_MANIFEST_REQUIRED' }
    if (-not (Test-Path -LiteralPath $internalPath -PathType Container)) { throw 'INSTALLED_PRODUCT_SHELL_NOT_MIGRATED' }
    $product = Get-Content -LiteralPath $productPath -Raw | ConvertFrom-Json
    $installation = Get-Content -LiteralPath $installationPath -Raw | ConvertFrom-Json
    if ($product.schema_version -ne 'v8.0.1-product.v1' -or $product.product_id -ne 'LocalAIHub' -or $product.launcher -ne 'LocalAIHub.exe' -or $product.icon -ne 'local-ai-hub.ico') { throw 'PRODUCT_MANIFEST_INVALID' }
    if ($installation.schema_version -ne 'v8.0.1-installation.v1' -or $installation.product_id -ne 'LocalAIHub' -or $installation.launcher -ne 'LocalAIHub.exe') { throw 'INSTALLATION_MANIFEST_INVALID' }
    if ([IO.Path]::GetFullPath([string]$installation.app_root).TrimEnd('\') -ne $Root.TrimEnd('\')) { throw 'INSTALLATION_ROOT_MISMATCH' }
    return @{ Product = $product; Installation = $installation; Launcher = $launcherPath; Icon = $iconPath }
}

function Get-ShortcutLocations {
    if ($Locations -and $Locations.Count -gt 0) { return @($Locations) }
    $profile = [Environment]::GetFolderPath('UserProfile')
    $common = [Environment]::GetFolderPath('CommonApplicationData')
    return @(
        (Join-Path $profile 'Desktop'),
        (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),
        (Join-Path $common 'Microsoft\Windows\Start Menu\Programs')
    )
}

function Test-ShortcutOwned {
    param([object]$Shortcut, [string]$Root, [string]$LinkPath)
    $target = [string]$Shortcut.TargetPath
    $expected = [IO.Path]::GetFullPath((Join-Path $Root 'LocalAIHub.exe'))
    try { $targetFull = [IO.Path]::GetFullPath($target) } catch { $targetFull = $target }
    if ($targetFull.TrimEnd('\').Equals($expected.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)) { return $true }
    # Historical all-users launchers require the same bounded provenance as the
    # Python classifier: exact common Start Menu shape, external D:\LocalAIHub\Temp
    # VBS, and an approved marker/content fingerprint.  A random wscript/VBS
    # shortcut is therefore review-only and never mutated.
    $legacyHost = 'w' + 'script.exe'
    $legacyScript = 'LocalAIHub' + '.vbs'
    if (-not ([IO.Path]::GetFileName($targetFull)).Equals($legacyHost, [StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not ([string]$Shortcut.Arguments).ToLowerInvariant().Contains($legacyScript.ToLowerInvariant())) { return $false }
    if (-not $LinkPath -or $LinkPath -notmatch '(?i)\\Microsoft\\Windows\\Start Menu\\Programs\\Local AI Hub\.lnk$') { return $false }
    if ($LinkPath -notmatch '(?i)ProgramData') { return $false }
    $legacyPathMatch = [regex]::Match([string]$Shortcut.Arguments, '(?i)(D:\\LocalAIHub\\Temp\\[^" ]*LocalAIHub\.vbs)')
    if (-not $legacyPathMatch.Success) { return $false }
    $legacyPath = $legacyPathMatch.Groups[1].Value
    if (-not (Test-Path -LiteralPath $legacyPath -PathType Leaf)) { return $false }
    $content = Get-Content -LiteralPath $legacyPath -Raw -ErrorAction SilentlyContinue
    if (-not $content -or $content.IndexOf('LOCALAIHUB_LEGACY_VBS_V1', [StringComparison]::Ordinal) -lt 0) { return $false }
    try { $digest = (Get-FileHash -LiteralPath $legacyPath -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant() } catch { return $false }
    return $digest -eq '53e0d08791c9eaa4cea0e59c6faaad98e0ba6e97d3207103277c0b19a0423090'
}

$root = Get-InstalledRoot -Value $AppRoot
$product = Read-InstalledProduct -Root $root
$locationsToRepair = Get-ShortcutLocations
$shell = New-Object -ComObject WScript.Shell
foreach ($location in $locationsToRepair) {
    if ([string]::IsNullOrWhiteSpace($location)) { continue }
    $link = Join-Path $location 'Local AI Hub.lnk'
    if (-not (Test-Path -LiteralPath $link -PathType Leaf)) { Write-Output "MISSING"; continue }
    try { $shortcut = $shell.CreateShortcut($link) } catch { Write-Output "UNREADABLE"; continue }
    if (-not (Test-ShortcutOwned -Shortcut $shortcut -Root $root -LinkPath $link)) { Write-Output "SKIPPED_UNOWNED_OR_AMBIGUOUS"; continue }
    if (-not $Apply) { Write-Output "DRYRUN"; continue }
    try {
        $shortcut.TargetPath = $product.Launcher
        $shortcut.Arguments = ''
        $shortcut.WorkingDirectory = $root
        $shortcut.Description = 'Local AI Hub stable installed product'
        $shortcut.IconLocation = "$($product.Launcher),0"
        $shortcut.Save()
        Write-Output "UPDATED"
    } catch {
        if ($location -match '(?i)ProgramData') { Write-Output "ADMIN_REQUIRED" }
        else { Write-Output "SHORTCUT_REPAIR_FAILED" }
    }
}
