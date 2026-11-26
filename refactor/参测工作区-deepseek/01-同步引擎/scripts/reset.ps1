. "$PSScriptRoot\lib.ps1"
if (Test-Path $Run) { Remove-Item -Recurse -Force $Run }
Write-Host "run 目录已重置（空状态）"
