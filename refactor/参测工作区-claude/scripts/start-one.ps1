param(
    [Parameter(Mandatory = $true)][ValidateSet('server', 'a', 'b')][string]$Role,
    [string]$RunRoot = 'run\default',
    [int]$ServerPort = 7801,
    [int]$APort = 7802,
    [int]$BPort = 7803
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$serverExe = Join-Path $script:BinDir 'server.exe'
$clientExe = Join-Path $script:BinDir 'sync-client.exe'
$runPath = Resolve-RunPath $RunRoot
$pidsPath = Join-Path $runPath 'pids.json'
$serverState = Join-Path $runPath 'server-state'
$stateA = Join-Path $runPath 'state-a'
$stateB = Join-Path $runPath 'state-b'
$projectA = Join-Path $runPath 'project-a'
$projectB = Join-Path $runPath 'project-b'
$logDir = Join-Path $runPath 'logs'
$serverUrl = "http://127.0.0.1:$ServerPort"

$info = $null
if (Test-Path -LiteralPath $pidsPath) {
    $info = Get-Content -LiteralPath $pidsPath -Raw | ConvertFrom-Json
    $ServerPort = [int]$info.server_port
    $APort = [int]$info.a_control_port
    $BPort = [int]$info.b_control_port
    $serverUrl = [string]$info.server_url
    $serverState = [string]$info.server_state
    $stateA = [string]$info.a_state
    $stateB = [string]$info.b_state
    $projectA = [string]$info.a_project
    $projectB = [string]$info.b_project
    $logDir = [string]$info.logs
}

foreach ($d in @($serverState, $stateA, $stateB, $projectA, $projectB, $logDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'

if ($Role -eq 'server') {
    if (-not (Test-Path -LiteralPath $serverExe)) { throw "missing $serverExe; run scripts\build.ps1" }
    if (Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 1) { throw "coordinator already healthy at $serverUrl" }
    $out = Join-Path $logDir "server-$stamp.out.log"
    $err = Join-Path $logDir "server-$stamp.err.log"
    $proc = Start-Process -FilePath $serverExe -ArgumentList @('--addr', "127.0.0.1:$ServerPort", '--state', (Quote-Arg $serverState)) `
        -WorkingDirectory $runPath -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    if (-not (Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 30)) {
        Get-Content -LiteralPath $err -Tail 30 -ErrorAction SilentlyContinue
        throw "coordinator failed to become healthy; see $out / $err"
    }
    $serverPid = $proc.Id
    Write-Host ("coordinator started pid={0} {1} state={2}" -f $serverPid, $serverUrl, $serverState)
} elseif ($Role -eq 'a') {
    if (-not (Test-Path -LiteralPath $clientExe)) { throw "missing $clientExe; run scripts\build.ps1" }
    if (Wait-HttpOk -Uri "http://127.0.0.1:$APort/healthz" -TimeoutSec 1) { throw "client A already healthy on port $APort" }
    $out = Join-Path $logDir "client-a-$stamp.out.log"
    $err = Join-Path $logDir "client-a-$stamp.err.log"
    $proc = Start-Process -FilePath $clientExe -ArgumentList @(
        '--device', 'A', '--project', (Quote-Arg $projectA), '--state', (Quote-Arg $stateA),
        '--server', $serverUrl, '--control', "127.0.0.1:$APort", '--interval-ms', '300') `
        -WorkingDirectory $runPath -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    if (-not (Wait-HttpOk -Uri "http://127.0.0.1:$APort/healthz" -TimeoutSec 30)) {
        Get-Content -LiteralPath $err -Tail 30 -ErrorAction SilentlyContinue
        throw "client A failed to become healthy; see $out / $err"
    }
    $aPid = $proc.Id
    Write-Host ("client A started pid={0} control={1} project={2} state={3}" -f $aPid, "http://127.0.0.1:$APort", $projectA, $stateA)
} else {
    if (-not (Test-Path -LiteralPath $clientExe)) { throw "missing $clientExe; run scripts\build.ps1" }
    if (Wait-HttpOk -Uri "http://127.0.0.1:$BPort/healthz" -TimeoutSec 1) { throw "client B already healthy on port $BPort" }
    $out = Join-Path $logDir "client-b-$stamp.out.log"
    $err = Join-Path $logDir "client-b-$stamp.err.log"
    $proc = Start-Process -FilePath $clientExe -ArgumentList @(
        '--device', 'B', '--project', (Quote-Arg $projectB), '--state', (Quote-Arg $stateB),
        '--server', $serverUrl, '--control', "127.0.0.1:$BPort", '--interval-ms', '300') `
        -WorkingDirectory $runPath -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    if (-not (Wait-HttpOk -Uri "http://127.0.0.1:$BPort/healthz" -TimeoutSec 30)) {
        Get-Content -LiteralPath $err -Tail 30 -ErrorAction SilentlyContinue
        throw "client B failed to become healthy; see $out / $err"
    }
    $bPid = $proc.Id
    Write-Host ("client B started pid={0} control={1} project={2} state={3}" -f $bPid, "http://127.0.0.1:$BPort", $projectB, $stateB)
}

if ($null -eq $info) {
    $info = [pscustomobject]@{
        started_at     = (Get-Date).ToString('o')
        run_root       = $runPath
        server_url     = $serverUrl
        server_pid     = 0
        server_state   = $serverState
        a_control_url  = "http://127.0.0.1:$APort"
        a_pid          = 0
        a_project      = $projectA
        a_state        = $stateA
        b_control_url  = "http://127.0.0.1:$BPort"
        b_pid          = 0
        b_project      = $projectB
        b_state        = $stateB
        server_port    = $ServerPort
        a_control_port = $APort
        b_control_port = $BPort
        logs           = $logDir
    }
}
if ($Role -eq 'server') { $info.server_pid = $serverPid }
if ($Role -eq 'a') { $info.a_pid = $aPid }
if ($Role -eq 'b') { $info.b_pid = $bPid }
$info | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath $pidsPath
