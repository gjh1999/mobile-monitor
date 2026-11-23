param(
    [Parameter(Mandatory = $true)][ValidateSet('server', 'a', 'b')][string]$Role,
    [string]$RunRoot = 'run\default',
    [switch]$Graceful
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$runPath = Resolve-RunPath $RunRoot
$pidsPath = Join-Path $runPath 'pids.json'
if (-not (Test-Path -LiteralPath $pidsPath)) { throw "no pids.json under $runPath; start the stack first" }
$info = Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json

$targetPid = 0
if ($Role -eq 'server') { $targetPid = [int]$info.server_pid }
elseif ($Role -eq 'a') { $targetPid = [int]$info.a_pid }
else { $targetPid = [int]$info.b_pid }
if ($targetPid -le 0) { throw "no recorded pid for role $Role" }
if (-not (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
    Write-Host "role $Role pid=$targetPid is not running"
    return
}

if ($Graceful) {
    try {
        if ($Role -eq 'a') { $null = Invoke-JsonApi -Uri ($info.a_control_url + '/shutdown') -Method 'POST' -Body @{} -TimeoutSec 3 }
        elseif ($Role -eq 'b') { $null = Invoke-JsonApi -Uri ($info.b_control_url + '/shutdown') -Method 'POST' -Body @{} -TimeoutSec 3 }
        else { $null = Invoke-JsonApi -Uri ($info.server_url + '/api/admin/shutdown') -Method 'POST' -Body @{} -TimeoutSec 3 }
        Start-Sleep -Milliseconds 300
    } catch { }
}

Stop-ProcessForce $targetPid
Write-Host ("killed role={0} pid={1}; state directories kept under {2}" -f $Role, $targetPid, $runPath)
Write-Host ("restart with: powershell -File scripts\start-one.ps1 -Role {0} -RunRoot {1}" -f $Role, $RunRoot)
