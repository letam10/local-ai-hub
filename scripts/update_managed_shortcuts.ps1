[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$IncludeLegacy
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktop = [Environment]::GetFolderPath('Desktop')
$pythonw = Join-Path $root 'Environments\hub\Scripts\pythonw.exe'

function Test-HubPythonwFallback {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonwPath,
        [Parameter(Mandatory = $true)]
        [string]$HubRoot
    )

    if (-not (Test-Path -LiteralPath $PythonwPath -PathType Leaf)) { return $false }
    $python = Join-Path (Split-Path -Parent $PythonwPath) 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { return $false }
    $probe = @'
import importlib.util
import pathlib
import sys

root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
required = ("src.app.main", "src.services.api.api_server", "webview")
raise SystemExit(0 if all(importlib.util.find_spec(name) is not None for name in required) else 1)
'@
    & $python -c $probe $HubRoot 2>$null
    return $LASTEXITCODE -eq 0
}

# A machine may use the system Python environment when the optional Hub
# environment is not provisioned yet.  A PATH fallback is eligible only when
# its paired interpreter can resolve the Hub desktop entry dependencies; an
# arbitrary pythonw.exe must never create a silently broken shortcut.
$missingRuntime = $false
if (Test-Path -LiteralPath $pythonw -PathType Leaf) {
    if (-not (Test-HubPythonwFallback -PythonwPath $pythonw -HubRoot $root)) {
        $missingRuntime = $true
    }
} else {
    $systemPythonw = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($systemPythonw -and (Test-HubPythonwFallback -PythonwPath $systemPythonw.Source -HubRoot $root)) {
        $pythonw = $systemPythonw.Source
    } else {
        $missingRuntime = $true
    }
}
if ($missingRuntime) {
    Write-Output 'MISSING_RUNTIME Local AI Hub shortcut was not created: no Hub-capable pythonw.exe was verified.'
    return
}
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
