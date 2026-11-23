param(
    [string]$RunRoot = 'run\default',
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$runPath = Resolve-RunPath $RunRoot
$pidsPath = Join-Path $runPath 'pids.json'
if (-not (Test-Path -LiteralPath $pidsPath)) {
    if (-not $Quiet) { Write-Host "no pids.json under $runPath; nothing to stop" }
    return
}
$info = Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json
$serverUrl = $info.server_url

if (-not $Quiet) { Write-Host "stopping clients gracefully" }
foreach ($item in @(
        @{ name = 'A'; url = $info.a_control_url; pid = [int]$info.a_pid },
        @{ name = 'B'; url = $info.b_control_url; pid = [int]$info.b_pid }
    )) {
    try {
        $null = Invoke-JsonApi -Uri ($item.url + '/shutdown') -Method 'POST' -Body @{} -TimeoutSec 3
        if (-not $Quiet) { Write-Host ("  client {0} shutdown requested" -f $item.name) }
    } catch { }
}
if (-not $Quiet) { Write-Host "stopping coordinator gracefully" }
try {
    $null = Invoke-JsonApi -Uri ($serverUrl + '/api/admin/shutdown') -Method 'POST' -Body @{} -TimeoutSec 3
} catch { }

Start-Sleep -Milliseconds 400
foreach ($item in @(
        @{ name = 'A'; pid = [int]$info.a_pid },
        @{ name = 'B'; pid = [int]$info.b_pid },
        @{ name = 'coordinator'; pid = [int]$info.server_pid }
    )) {
    $alive = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
    if ($alive) {
        if (-not $Quiet) { Write-Host ("  force-killing {0} pid={1}" -f $item.name, $item.pid) }
        Stop-ProcessForce $item.pid
    }
}

$still = @()
foreach ($item in @(
        @{ name = 'A'; pid = [int]$info.a_pid },
        @{ name = 'B'; pid = [int]$info.b_pid },
        @{ name = 'coordinator'; pid = [int]$info.server_pid }
    )) {
    if (Get-Process -Id $item.pid -ErrorAction SilentlyContinue) { $still += $item.name }
}
if ($still.Count -gt 0) {
    throw ("processes still running: " + ($still -join ', '))
}
Remove-Item -LiteralPath $pidsPath -Force -ErrorAction SilentlyContinue
if (-not $Quiet) { Write-Host "ALL STOPPED (state directories kept)" }
