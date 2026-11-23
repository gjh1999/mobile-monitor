param(
    [string]$RunRoot = 'run\default',
    [int]$ServerPort = 7801,
    [int]$APort = 7802,
    [int]$BPort = 7803,
    [switch]$Fresh,
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$serverExe = Join-Path $script:BinDir 'server.exe'
$clientExe = Join-Path $script:BinDir 'sync-client.exe'
if ((-not (Test-Path -LiteralPath $serverExe)) -or (-not (Test-Path -LiteralPath $clientExe))) {
    if ($NoBuild) { throw "binaries missing; run scripts\build.ps1 first" }
    Write-Host "binaries missing; building"
    & (Join-Path $PSScriptRoot 'build.ps1')
    if (-not (Test-Path -LiteralPath $serverExe)) { throw "build did not produce $serverExe" }
}

$runPath = Resolve-RunPath $RunRoot
if ($Fresh) {
    if (Test-Path -LiteralPath (Join-Path $runPath 'pids.json')) {
        try { & (Join-Path $PSScriptRoot 'stop-all.ps1') -RunRoot $RunRoot -Quiet } catch { }
    }
    if (Test-Path -LiteralPath $runPath) {
        Remove-Item -LiteralPath $runPath -Recurse -Force
    }
}

$serverState = Join-Path $runPath 'server-state'
$stateA = Join-Path $runPath 'state-a'
$stateB = Join-Path $runPath 'state-b'
$projectA = Join-Path $runPath 'project-a'
$projectB = Join-Path $runPath 'project-b'
$logDir = Join-Path $runPath 'logs'
foreach ($d in @($serverState, $stateA, $stateB, $projectA, $projectB, $logDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$serverOut = Join-Path $logDir "server-$stamp.out.log"
$serverErr = Join-Path $logDir "server-$stamp.err.log"
$aOut = Join-Path $logDir "client-a-$stamp.out.log"
$aErr = Join-Path $logDir "client-a-$stamp.err.log"
$bOut = Join-Path $logDir "client-b-$stamp.out.log"
$bErr = Join-Path $logDir "client-b-$stamp.err.log"
$serverUrl = "http://127.0.0.1:$ServerPort"

$started = @()
try {
    $serverArgs = @('--addr', "127.0.0.1:$ServerPort", '--state', (Quote-Arg $serverState))
    $serverProc = Start-Process -FilePath $serverExe -ArgumentList $serverArgs -WorkingDirectory $runPath `
        -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr -PassThru -WindowStyle Hidden
    $started += $serverProc
    if (-not (Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 30)) {
        throw "coordinator did not become healthy; see $serverOut / $serverErr"
    }

    $commonA = @('--device', 'A', '--project', (Quote-Arg $projectA), '--state', (Quote-Arg $stateA),
        '--server', $serverUrl, '--control', "127.0.0.1:$APort", '--interval-ms', '300')
    $aProc = Start-Process -FilePath $clientExe -ArgumentList $commonA -WorkingDirectory $runPath `
        -RedirectStandardOutput $aOut -RedirectStandardError $aErr -PassThru -WindowStyle Hidden
    $started += $aProc
    if (-not (Wait-HttpOk -Uri "http://127.0.0.1:$APort/healthz" -TimeoutSec 30)) {
        throw "client A did not become healthy; see $aOut / $aErr"
    }

    $commonB = @('--device', 'B', '--project', (Quote-Arg $projectB), '--state', (Quote-Arg $stateB),
        '--server', $serverUrl, '--control', "127.0.0.1:$BPort", '--interval-ms', '300')
    $bProc = Start-Process -FilePath $clientExe -ArgumentList $commonB -WorkingDirectory $runPath `
        -RedirectStandardOutput $bOut -RedirectStandardError $bErr -PassThru -WindowStyle Hidden
    $started += $bProc
    if (-not (Wait-HttpOk -Uri "http://127.0.0.1:$BPort/healthz" -TimeoutSec 30)) {
        throw "client B did not become healthy; see $bOut / $bErr"
    }
} catch {
    foreach ($p in $started) { try { Stop-ProcessForce $p.Id } catch { } }
    throw
}

$info = [ordered]@{
    started_at        = (Get-Date).ToString('o')
    run_root          = $runPath
    server_url        = $serverUrl
    server_pid        = $serverProc.Id
    server_state      = $serverState
    a_control_url     = "http://127.0.0.1:$APort"
    a_pid             = $aProc.Id
    a_project         = $projectA
    a_state           = $stateA
    b_control_url     = "http://127.0.0.1:$BPort"
    b_pid             = $bProc.Id
    b_project         = $projectB
    b_state           = $stateB
    server_port       = $ServerPort
    a_control_port    = $APort
    b_control_port    = $BPort
    logs              = $logDir
}
$info | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $runPath 'pids.json')

Write-Host "THREE PROCESSES RUNNING"
Write-Host ("  coordinator  pid={0}  {1}  state={2}" -f $serverProc.Id, $serverUrl, $serverState)
Write-Host ("  client A     pid={0}  {1}  project={2}  state={3}" -f $aProc.Id, "http://127.0.0.1:$APort", $projectA, $stateA)
Write-Host ("  client B     pid={0}  {1}  project={2}  state={3}" -f $bProc.Id, "http://127.0.0.1:$BPort", $projectB, $stateB)
Write-Host ("  run root: {0}" -f $runPath)
Write-Host ("  logs: {0}" -f $logDir)
