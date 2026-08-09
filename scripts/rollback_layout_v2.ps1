[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot 'migrate_layout_v2.ps1') -Rollback
exit $LASTEXITCODE
