[CmdletBinding()]
param(
    [string]$Executable = $env:ANIMESR_EXECUTABLE
)

$animeExecutable = [Environment]::ExpandEnvironmentVariables($Executable)
if ([string]::IsNullOrWhiteSpace($animeExecutable)) {
    throw 'AnimeSR executable is not configured. Set ANIMESR_EXECUTABLE in Config/local.env.cmd or the environment.'
}
$animeWorkingDirectory = Split-Path -LiteralPath $animeExecutable -Parent
if (-not (Test-Path -LiteralPath $animeExecutable)) { throw "AnimeSR executable not found: $animeExecutable" }
Start-Process -FilePath $animeExecutable -WorkingDirectory $animeWorkingDirectory
