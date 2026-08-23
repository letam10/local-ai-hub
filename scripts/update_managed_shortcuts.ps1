[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$AppRoot,
    [string[]]$Locations
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
    $iconPath = Join-Path $Root 'local-ai-hub.ico'
    if (-not (Test-Path -LiteralPath $productPath -PathType Leaf) -or -not (Test-Path -LiteralPath $installationPath -PathType Leaf) -or -not (Test-Path -LiteralPath $launcherPath -PathType Leaf) -or -not (Test-Path -LiteralPath $iconPath -PathType Leaf)) { throw 'INSTALLED_PRODUCT_MANIFEST_REQUIRED' }
    $product = Get-Content -LiteralPath $productPath -Raw | ConvertFrom-Json
    $installation = Get-Content -LiteralPath $installationPath -Raw | ConvertFrom-Json
    if ($product.schema_version -ne 'v8.0.1-product.v1' -or $product.product_id -ne 'LocalAIHub' -or $product.launcher -ne 'LocalAIHub.exe' -or $product.icon -ne 'local-ai-hub.ico') { throw 'PRODUCT_MANIFEST_INVALID' }
    if ($installation.schema_version -ne 'v8.0.1-installation.v1' -or $installation.product_id -ne 'LocalAIHub' -or $installation.launcher -ne 'LocalAIHub.exe') { throw 'INSTALLATION_MANIFEST_INVALID' }
    if ([IO.Path]::GetFullPath([string]$installation.app_root).TrimEnd('\') -ne $Root.TrimEnd('\')) { throw 'INSTALLATION_ROOT_MISMATCH' }
    return @{ Product = $product; Installation = $installation; Launcher = $launcherPath; Icon = $iconPath }
}

function Get-ShortcutLocations {
    if ($Locations -and $Locations.Count -gt 0) { return $Locations }
    return @([Environment]::GetFolderPath('Desktop'), (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'))
}

$root = Get-InstalledRoot -Value $AppRoot
$product = Read-InstalledProduct -Root $root
$locationsToRepair = Get-ShortcutLocations
$shell = New-Object -ComObject WScript.Shell
foreach ($location in $locationsToRepair) {
    if ([string]::IsNullOrWhiteSpace($location)) { continue }
    New-Item -ItemType Directory -Path $location -Force | Out-Null
    $link = Join-Path $location 'Local AI Hub.lnk'
    if (-not $Apply) { Write-Output "DRYRUN $link -> $($product.Launcher)"; continue }
    $shortcut = $shell.CreateShortcut($link)
    $shortcut.TargetPath = $product.Launcher
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = $root
    $shortcut.Description = 'Local AI Hub stable installed product'
    $shortcut.IconLocation = "$($product.Launcher),0"
    $shortcut.Save()
    Write-Output "UPDATED $link -> $($product.Launcher)"
}
