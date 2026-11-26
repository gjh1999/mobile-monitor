. "$PSScriptRoot\lib.ps1"
Stop-PidFile (Join-Path $Run 'server.pid')
Stop-PidFile (Join-Path $Run 'client-A.pid')
Stop-PidFile (Join-Path $Run 'client-B.pid')
Write-Host '已停止三个进程'
