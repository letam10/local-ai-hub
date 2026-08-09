[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcuts = @(
    @{ name = 'Local AI Hub.lnk'; target = (Join-Path $root 'Scripts\launch_local_ai_hub.cmd'); working = $root },
    @{ name = 'SAM2 Mask.lnk'; target = (Join-Path $root 'runtime\applications\SAM2-Mask-Studio\SAM2 Mask Studio.exe'); working = (Join-Path $root 'runtime\applications\SAM2-Mask-Studio') },
    @{ name = 'AnimeSR v2 Upscale.lnk'; target = (Join-Path $root 'runtime\applications\Anime-Upscale-Studio\Anime Upscale Studio.exe'); working = (Join-Path $root 'runtime\applications\Anime-Upscale-Studio') },
    @{ name = 'Local Image Studio.lnk'; target = (Join-Path $root 'runtime\applications\FLUX-Klein-Studio\Local Image Studio.exe'); working = (Join-Path $root 'runtime\applications\FLUX-Klein-Studio') }
)

$shell = New-Object -ComObject WScript.Shell
foreach ($shortcut in $shortcuts) {
    $link = Join-Path $desktop $shortcut.name
    if (-not (Test-Path -LiteralPath $shortcut.target -PathType Leaf)) {
        Write-Output "SKIP $($shortcut.name): target is not present yet"
        continue
    }
    if (-not $Apply) {
        Write-Output "DRYRUN $($shortcut.name): $($shortcut.target)"
        continue
    }
    $item = $shell.CreateShortcut($link)
    $item.TargetPath = $shortcut.target
    $item.WorkingDirectory = $shortcut.working
    $item.Description = "Local AI Hub managed shortcut"
    $item.Save()
    Write-Output "UPDATED $link -> $($shortcut.target)"
}
