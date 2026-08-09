[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$IncludeLegacy
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = [Environment]::GetFolderPath('Desktop')
$pythonw = Join-Path $root 'Environments\hub\Scripts\pythonw.exe'
$shortcuts = @(
    @{ locations = @($desktop, $root); name = 'Local AI Hub.lnk'; target = $pythonw; arguments = '-m src.app.main'; working = $root; description = 'Local AI Hub no-console desktop entry' }
)
if ($IncludeLegacy) {
    $shortcuts += @(
        @{ locations = @($desktop); name = 'Legacy SAM2 Mask Studio.lnk'; target = (Join-Path $root 'runtime\applications\SAM2-Mask-Studio\SAM2 Mask Studio.exe'); arguments = ''; working = (Join-Path $root 'runtime\applications\SAM2-Mask-Studio'); description = 'Advanced legacy Local AI Hub application' },
        @{ locations = @($desktop); name = 'Legacy Anime Upscale Studio.lnk'; target = (Join-Path $root 'runtime\applications\Anime-Upscale-Studio\Anime Upscale Studio.exe'); arguments = ''; working = (Join-Path $root 'runtime\applications\Anime-Upscale-Studio'); description = 'Advanced legacy Local AI Hub application' },
        @{ locations = @($desktop); name = 'Legacy Local Image Studio.lnk'; target = (Join-Path $root 'runtime\applications\FLUX-Klein-Studio\Local Image Studio.exe'); arguments = ''; working = (Join-Path $root 'runtime\applications\FLUX-Klein-Studio'); description = 'Advanced legacy Local AI Hub application' }
    )
}

$shell = New-Object -ComObject WScript.Shell
foreach ($shortcut in $shortcuts) {
    if (-not (Test-Path -LiteralPath $shortcut.target -PathType Leaf)) {
        Write-Output "SKIP $($shortcut.name): target is not present yet"
        continue
    }
    foreach ($location in $shortcut.locations) {
        $link = Join-Path $location $shortcut.name
        if (-not $Apply) {
            Write-Output "DRYRUN $link -> $($shortcut.target) $($shortcut.arguments)"
            continue
        }
        $item = $shell.CreateShortcut($link)
        $item.TargetPath = $shortcut.target
        $item.Arguments = $shortcut.arguments
        $item.WorkingDirectory = $shortcut.working
        $item.Description = $shortcut.description
        $item.IconLocation = "$pythonw,0"
        $item.Save()
        Write-Output "UPDATED $link -> $($shortcut.target) $($shortcut.arguments)"
    }
}
