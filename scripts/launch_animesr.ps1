[CmdletBinding()]
param(
    [string]$Executable = $env:ANIMESR_EXECUTABLE
)

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($Executable)) {
    $Executable = Join-Path $root 'runtime\applications\Anime-Upscale-Studio\Anime Upscale Studio.exe'
}
$animeExecutable = [Environment]::ExpandEnvironmentVariables($Executable)
$animeWorkingDirectory = Split-Path -LiteralPath $animeExecutable -Parent
if (-not (Test-Path -LiteralPath $animeExecutable)) { throw "AnimeSR executable not found: $animeExecutable" }
Start-Process -FilePath $animeExecutable -WorkingDirectory $animeWorkingDirectory
