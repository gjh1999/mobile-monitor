param([Parameter(Mandatory=$true)][string]$Id)
. "$PSScriptRoot\lib.ps1"
if ($Id -eq 'server') {
    Stop-PidFile (Join-Path $Run 'server.pid')
    Write-Host '已结束 server'
} else {
    Stop-PidFile (Join-Path $Run "client-$Id.pid")
    Write-Host "已结束 client-$Id"
}
