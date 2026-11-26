. "$PSScriptRoot\lib.ps1"
Invoke-Build
Stop-PidFile (Join-Path $Run 'server.pid')
Stop-PidFile (Join-Path $Run 'client-A.pid')
Stop-PidFile (Join-Path $Run 'client-B.pid')
Start-Sleep -Milliseconds 300
$s = Start-SyncServer
$pa = Start-SyncClient 'A'
$pb = Start-SyncClient 'B'
if (Wait-ForServer 10) {
    Write-Host "server pid=$s client-A pid=$pa client-B pid=$pb url=$ServerUrl"
} else {
    Write-Host "错误：服务端未在 $ServerUrl 响应"
    exit 1
}
