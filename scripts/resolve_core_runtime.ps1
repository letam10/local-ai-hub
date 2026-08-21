param(
    [ValidateSet('Python', 'Pythonw')]
    [string]$Mode = 'Python'
)

$ErrorActionPreference = 'SilentlyContinue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$dataRoot = if ($env:LOCALAIHUB_DATA_ROOT) { [IO.Path]::GetFullPath($env:LOCALAIHUB_DATA_ROOT) } else { $root }
$leaf = if ($Mode -eq 'Pythonw') { 'pythonw.exe' } else { 'python.exe' }
$candidates = @(
    (Join-Path $dataRoot ("Environments\core\Scripts\" + $leaf)),
    (Join-Path $dataRoot ("Environments\hub\Scripts\" + $leaf))
)
$command = Get-Command $leaf -ErrorAction SilentlyContinue
if ($command -and $command.Source) { $candidates += $command.Source }
foreach ($candidate in $candidates) {
    if ([string]::IsNullOrWhiteSpace([string]$candidate)) { continue }
    $item = Get-Item -LiteralPath $candidate -ErrorAction SilentlyContinue
    if ($null -eq $item -or $item.PSIsContainer -or $item.Attributes.ToString().Contains('ReparsePoint')) { continue }
    Write-Output $item.FullName
    exit 0
}
exit 1
