param(
    [string]$Root = '',
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$workspace = $script:WorkspaceRoot
$serverExe = Join-Path $script:BinDir 'server.exe'
$clientExe = Join-Path $script:BinDir 'sync-client.exe'
if ((-not (Test-Path -LiteralPath $serverExe)) -or (-not (Test-Path -LiteralPath $clientExe))) {
    if ($NoBuild) { throw 'binaries missing; run scripts\build.ps1 first' }
    & (Join-Path $PSScriptRoot 'build.ps1')
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if ([string]::IsNullOrWhiteSpace($Root)) {
    $runPath = Join-Path $workspace ('run\e2e-' + $stamp)
} else {
    $runPath = Resolve-RunPath $Root
}
$runPath = [IO.Path]::GetFullPath($runPath)
$serverState = Join-Path $runPath 'server-state'
$stateA = Join-Path $runPath 'state-a'
$stateB = Join-Path $runPath 'state-b'
$projectA = Join-Path $runPath 'project-a'
$projectB = Join-Path $runPath 'project-b'
$logDir = Join-Path $runPath 'logs'
$evidenceDir = Join-Path $runPath 'evidence'
foreach ($d in @($serverState, $stateA, $stateB, $projectA, $projectB, $logDir, $evidenceDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$serverPort = Get-FreeTcpPort
$aPort = Get-FreeTcpPort
$bPort = Get-FreeTcpPort
while ($aPort -eq $serverPort) { $aPort = Get-FreeTcpPort }
while (($bPort -eq $serverPort) -or ($bPort -eq $aPort)) { $bPort = Get-FreeTcpPort }
$serverUrl = "http://127.0.0.1:$serverPort"
$aUrl = "http://127.0.0.1:$aPort"
$bUrl = "http://127.0.0.1:$bPort"

$sampleDir = Get-ChildItem -LiteralPath $workspace -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'summary.py') } |
    Select-Object -First 1
$eventsFile = Get-ChildItem -LiteralPath $workspace -File -Filter '*.jsonl' | Select-Object -First 1
if ($null -eq $sampleDir) { throw 'sample project (directory containing summary.py) not found at workspace root' }
if ($null -eq $eventsFile) { throw 'public events JSONL not found at workspace root' }

$aReadings = Join-Path $projectA 'data\readings.txt'
$bReadings = Join-Path $projectB 'data\readings.txt'
$bWeek = Join-Path $projectB 'data\week.txt'
$aWeek = Join-Path $projectA 'data\week.txt'
$aSummary = Join-Path $projectA 'summary.py'
$bSummary = Join-Path $projectB 'summary.py'
$sourceSummary = Join-Path $sampleDir.FullName 'summary.py'

$script:Assertions = New-Object System.Collections.ArrayList
$script:FailCount = 0
$script:LogSeq = 0
$script:ServerRole = $null
$script:ARole = $null
$script:BRole = $null

function Assert-True {
    param([string]$Name, $Condition, [string]$Detail = '')
    $ok = [bool]$Condition
    if ($ok) {
        Write-Host ("PASS  " + $Name)
        [void]$script:Assertions.Add([pscustomobject]@{ name = $Name; pass = $true; detail = $Detail })
    } else {
        Write-Host ("FAIL  " + $Name + " :: " + $Detail)
        [void]$script:Assertions.Add([pscustomobject]@{ name = $Name; pass = $false; detail = $Detail })
        $script:FailCount++
    }
}

function Wait-Until {
    param([scriptblock]$Check, [int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Check) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Get-ClientStatus {
    param([int]$ControlPort)
    return Invoke-JsonApi -Uri ("http://127.0.0.1:" + $ControlPort + "/status") -TimeoutSec 5
}

function Get-History {
    param([int]$ControlPort, [string]$ThreadId, [string]$TurnId)
    $url = "http://127.0.0.1:$ControlPort/history?thread_id=" + [Uri]::EscapeDataString($ThreadId)
    if (-not [string]::IsNullOrWhiteSpace($TurnId)) {
        $url = $url + '&turn_id=' + [Uri]::EscapeDataString($TurnId)
    }
    return Invoke-JsonApi -Uri $url -TimeoutSec 10
}

function Save-Evidence {
    param([string]$Name, $Value)
    $path = Join-Path $evidenceDir $Name
    $Value | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -LiteralPath $path
    return $path
}

function Start-Role {
    param([string]$Exe, [string[]]$Arguments, [string]$Label, [string]$ControlUrl = '')
    $script:LogSeq++
    $out = Join-Path $logDir ("{0}-{1}.out.log" -f $Label, $script:LogSeq)
    $err = Join-Path $logDir ("{0}-{1}.err.log" -f $Label, $script:LogSeq)
    $proc = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $runPath `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    return [pscustomobject]@{ Proc = $proc; Out = $out; Err = $err; Label = $Label; ControlUrl = $ControlUrl }
}

function Show-RoleLogs {
    param($Role)
    if ($null -eq $Role) { return }
    foreach ($f in @($Role.Out, $Role.Err)) {
        if (Test-Path -LiteralPath $f) {
            Write-Host ("---- tail " + $f + " ----")
            Get-Content -LiteralPath $f -Tail 20 -ErrorAction SilentlyContinue | Write-Host
        }
    }
}

function Stop-Role {
    param($Role, [string]$ShutdownUrl)
    if ($null -eq $Role) { return }
    if (-not [string]::IsNullOrWhiteSpace($ShutdownUrl)) {
        try { $null = Invoke-JsonApi -Uri $ShutdownUrl -Method 'POST' -Body @{} -TimeoutSec 3 } catch { }
        Start-Sleep -Milliseconds 250
    }
    Stop-ProcessForce $Role.Proc.Id
}

function Stop-Stack {
    Stop-Role $script:BRole ($bUrl + '/shutdown')
    Stop-Role $script:ARole ($aUrl + '/shutdown')
    Stop-Role $script:ServerRole ($serverUrl + '/api/admin/shutdown')
    $script:ServerRole = $null
    $script:ARole = $null
    $script:BRole = $null
    Start-Sleep -Milliseconds 300
}

function Start-Stack {
    $script:ServerRole = Start-Role -Exe $serverExe -Label 'server' -Arguments @(
        '--addr', "127.0.0.1:$serverPort", '--state', (Quote-Arg $serverState))
    if (-not (Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 30)) {
        Show-RoleLogs $script:ServerRole
        throw 'coordinator did not become healthy'
    }
    $script:ARole = Start-Role -Exe $clientExe -Label 'client-a' -ControlUrl $aUrl -Arguments @(
        '--device', 'A', '--project', (Quote-Arg $projectA), '--state', (Quote-Arg $stateA),
        '--server', $serverUrl, '--control', "127.0.0.1:$aPort", '--interval-ms', '300')
    if (-not (Wait-HttpOk -Uri "$aUrl/healthz" -TimeoutSec 30)) {
        Show-RoleLogs $script:ARole
        throw 'client A did not become healthy'
    }
    $script:BRole = Start-Role -Exe $clientExe -Label 'client-b' -ControlUrl $bUrl -Arguments @(
        '--device', 'B', '--project', (Quote-Arg $projectB), '--state', (Quote-Arg $stateB),
        '--server', $serverUrl, '--control', "127.0.0.1:$bPort", '--interval-ms', '300')
    if (-not (Wait-HttpOk -Uri "$bUrl/healthz" -TimeoutSec 30)) {
        Show-RoleLogs $script:BRole
        throw 'client B did not become healthy'
    }
}

function Restart-ClientB {
    Stop-Role $script:BRole ($bUrl + '/shutdown')
    $script:BRole = $null
    Start-Sleep -Milliseconds 300
    $script:BRole = Start-Role -Exe $clientExe -Label 'client-b' -ControlUrl $bUrl -Arguments @(
        '--device', 'B', '--project', (Quote-Arg $projectB), '--state', (Quote-Arg $stateB),
        '--server', $serverUrl, '--control', "127.0.0.1:$bPort", '--interval-ms', '300')
    if (-not (Wait-HttpOk -Uri "$bUrl/healthz" -TimeoutSec 30)) {
        Show-RoleLogs $script:BRole
        throw 'client B restart did not become healthy'
    }
}

$fatal = $null
try {
    Write-Host ("E2E ROOT: " + $runPath)
    Write-Host ("SAMPLE:   " + $sampleDir.FullName)
    Write-Host ("EVENTS:   " + $eventsFile.FullName)
    Save-Evidence 'stack.json' ([ordered]@{
            run_root = $runPath; server_url = $serverUrl; a_url = $aUrl; b_url = $bUrl
            project_a = $projectA; project_b = $projectB; state_a = $stateA; state_b = $stateB
            server_state = $serverState; sample_dir = $sampleDir.FullName; events = $eventsFile.FullName
        })

    # ---------------- U1: start from empty, restart ----------------
    Write-Host ''
    Write-Host '== U1 start from empty state =='
    Start-Stack
    Assert-True 'U1 coordinator healthy' (Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 5)
    Assert-True 'U1 client A healthy' (Wait-HttpOk -Uri "$aUrl/healthz" -TimeoutSec 5)
    Assert-True 'U1 client B healthy' (Wait-HttpOk -Uri "$bUrl/healthz" -TimeoutSec 5)
    $emptyA = ((Get-ChildItem -LiteralPath $projectA -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)
    $emptyB = ((Get-ChildItem -LiteralPath $projectB -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0)
    Assert-True 'U1 project A starts empty' $emptyA
    Assert-True 'U1 project B starts empty' $emptyB
    Save-Evidence 'u1-status-a.json' (Get-ClientStatus $aPort)
    Save-Evidence 'u1-status-b.json' (Get-ClientStatus $bPort)
    Save-Evidence 'u1-server-status.json' (Invoke-JsonApi -Uri "$serverUrl/api/status")

    Stop-Stack
    Start-Stack
    Assert-True 'U1 three roles restart with same state dirs' ((Wait-HttpOk -Uri "$serverUrl/healthz" -TimeoutSec 5) -and (Wait-HttpOk -Uri "$aUrl/healthz" -TimeoutSec 5) -and (Wait-HttpOk -Uri "$bUrl/healthz" -TimeoutSec 5))
    Assert-True 'U1 restart keeps both projects empty' (((Get-ChildItem -LiteralPath $projectA -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) -and ((Get-ChildItem -LiteralPath $projectB -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0))

    # ---------------- U2: file round trip ----------------
    Write-Host ''
    Write-Host '== U2 file round trip =='
    Copy-Item -Path (Join-Path $sampleDir.FullName '*') -Destination $projectA -Recurse -Force
    $initialHash = '748a88ea78f384b2d39609fcc0c327f871e52a203b4b93066595aaf158a6ada3'
    $appendedHash = '91060fb970dfca0cfcd6a2d582a6139378ea385e8b6c5268a59a970cb9fd78f3'
    $sawInitial = Wait-Until -TimeoutSec 30 -Check {
        (Get-FileSha256 $bReadings) -eq $initialHash -and (Get-FileSha256 $aReadings) -eq $initialHash
    }
    Assert-True 'U2 initial copy reaches B byte-identically' $sawInitial ("A=" + (Get-FileSha256 $aReadings) + " B=" + (Get-FileSha256 $bReadings))
    Assert-True 'U2 initial queue converged' (Wait-Until -TimeoutSec 30 -Check { (Get-ClientStatus $aPort).queue_empty -and (Get-ClientStatus $bPort).queue_empty })
    Save-Evidence 'u2-initial-server-status.json' (Invoke-JsonApi -Uri "$serverUrl/api/status")

    [IO.File]::AppendAllText($aReadings, "25`n", [Text.UTF8Encoding]::new($false))
    $sawAppend = Wait-Until -TimeoutSec 30 -Check { (Get-FileSha256 $bReadings) -eq $appendedHash }
    Assert-True 'U2 append on A propagates to B' $sawAppend ("B=" + (Get-FileSha256 $bReadings))
    Assert-True 'U2 append queue converged' (Wait-Until -TimeoutSec 30 -Check { (Get-ClientStatus $aPort).queue_empty -and (Get-ClientStatus $bPort).queue_empty })

    Move-Item -LiteralPath $bReadings -Destination $bWeek
    $sawRename = Wait-Until -TimeoutSec 30 -Check {
        (Test-Path -LiteralPath $aWeek) -and (-not (Test-Path -LiteralPath $aReadings)) -and (-not (Test-Path -LiteralPath $bReadings))
    }
    Assert-True 'U2 rename on B reaches A' $sawRename ("A week=" + (Test-Path -LiteralPath $aWeek) + " A readings=" + (Test-Path -LiteralPath $aReadings))
    Assert-True 'U2 rename queue converged' (Wait-Until -TimeoutSec 30 -Check { (Get-ClientStatus $aPort).queue_empty -and (Get-ClientStatus $bPort).queue_empty })

    Remove-Item -LiteralPath $aWeek -Force
    $sawDelete = Wait-Until -TimeoutSec 30 -Check {
        (-not (Test-Path -LiteralPath $aWeek)) -and (-not (Test-Path -LiteralPath $bWeek)) -and
        (-not (Test-Path -LiteralPath $aReadings)) -and (-not (Test-Path -LiteralPath $bReadings))
    }
    Assert-True 'U2 delete on A removes new path on both ends' $sawDelete
    Assert-True 'U2 final queue converged' (Wait-Until -TimeoutSec 30 -Check { (Get-ClientStatus $aPort).queue_empty -and (Get-ClientStatus $bPort).queue_empty })
    Assert-True 'U2 summary.py untouched on both ends' (((Get-FileSha256 $aSummary) -eq (Get-FileSha256 $sourceSummary)) -and ((Get-FileSha256 $bSummary) -eq (Get-FileSha256 $sourceSummary))) ("source=" + (Get-FileSha256 $sourceSummary) + " A=" + (Get-FileSha256 $aSummary) + " B=" + (Get-FileSha256 $bSummary))

    # ---------------- U3: event import, replay, restart ----------------
    Write-Host ''
    Write-Host '== U3 event import and read-only history =='
    $import1 = Invoke-JsonApi -Uri "$aUrl/import" -Method 'POST' -Body @{ file = $eventsFile.FullName }
    Assert-True 'U3 first import accepts all six events' (($import1.accepted -eq 6) -and ($import1.duplicates -eq 0)) ($import1 | ConvertTo-Json -Compress)
    $gotHistory = Wait-Until -TimeoutSec 30 -Check {
        try {
            $h = Get-History $bPort 'thread-weather' 'turn-01'
            return ($h.items.Count -eq 4) -and ($h.items[3].status -eq 'completed')
        } catch { return $false }
    }
    $history1 = Get-History $bPort 'thread-weather' 'turn-01'
    Save-Evidence 'u3-history-first.json' $history1
    Assert-True 'U3 B shows four logical items' ($gotHistory -and ($history1.items.Count -eq 4)) ("items=" + $history1.items.Count)
    $items = $history1.items
    Assert-True 'U3 user message' ($items[0].item_id -eq 'u1' -and $items[0].text -eq '计算读数的平均值。') ($items[0] | ConvertTo-Json -Compress)
    Assert-True 'U3 tool call fields' ($items[1].item_id -eq 'c1' -and $items[1].call_id -eq 'call-1' -and $items[1].name -eq 'sample.mean' -and $items[1].arguments.values[0] -eq 18 -and $items[1].arguments.values[1] -eq 22) ($items[1] | ConvertTo-Json -Compress)
    Assert-True 'U3 tool result association' ($items[2].item_id -eq 'r1' -and $items[2].call_id -eq 'call-1' -and $items[2].result.mean -eq 20) ($items[2] | ConvertTo-Json -Compress)
    Assert-True 'U3 assistant completed text' ($items[3].item_id -eq 'a1' -and $items[3].text -eq '平均值是 20。' -and $items[3].status -eq 'completed') ($items[3] | ConvertTo-Json -Compress)

    $import2 = Invoke-JsonApi -Uri "$aUrl/import" -Method 'POST' -Body @{ file = $eventsFile.FullName }
    Assert-True 'U3 second import is fully deduplicated' (($import2.accepted -eq 0) -and ($import2.duplicates -eq 6)) ($import2 | ConvertTo-Json -Compress)
    $history2 = Get-History $bPort 'thread-weather' 'turn-01'
    $same = (($history1.items | ConvertTo-Json -Depth 20 -Compress) -eq ($history2.items | ConvertTo-Json -Depth 20 -Compress))
    Assert-True 'U3 replay keeps item count, order, association and text' $same
    Save-Evidence 'u3-history-after-replay.json' $history2

    $replay = Invoke-JsonApi -Uri ("$bUrl/replay?thread_id=thread-weather&turn_id=turn-01")
    Assert-True 'U3 read-only replay reports zero tool dispatch' (($replay.tool_dispatch_attempts -eq 0) -and ($replay.tool_call_items_observed -eq 1)) ($replay | ConvertTo-Json -Depth 10 -Compress)
    $marker = Get-ChildItem -LiteralPath $runPath -Recurse -File -Filter 'replayed.txt' -ErrorAction SilentlyContinue
    Assert-True 'U3 no tool side-effect file exists' ($null -eq $marker)
    Save-Evidence 'u3-replay.json' $replay

    Restart-ClientB
    $history3 = Get-History $bPort 'thread-weather' 'turn-01'
    $sameAfterRestart = (($history1.items | ConvertTo-Json -Depth 20 -Compress) -eq ($history3.items | ConvertTo-Json -Depth 20 -Compress))
    Assert-True 'U3 history is identical after B restart' $sameAfterRestart
    Save-Evidence 'u3-history-after-restart.json' $history3
    Save-Evidence 'u3-server-status.json' (Invoke-JsonApi -Uri "$serverUrl/api/status")
    Save-Evidence 'u3-status-a.json' (Get-ClientStatus $aPort)
    Save-Evidence 'u3-status-b.json' (Get-ClientStatus $bPort)
} catch {
    $fatal = $_.Exception.Message
    Write-Host ("FATAL: " + $fatal)
    [void]$script:Assertions.Add([pscustomobject]@{ name = 'fatal'; pass = $false; detail = $fatal })
    $script:FailCount++
} finally {
    Stop-Stack
    $result = [ordered]@{
        run_root = $runPath
        finished_at = (Get-Date).ToString('o')
        fatal = $fatal
        passed = ($script:Assertions.Count - $script:FailCount)
        failed = $script:FailCount
        assertions = $script:Assertions
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $runPath 'e2e-result.json')
    Write-Host ''
    Write-Host ("E2E RESULT: passed={0} failed={1}" -f ($script:Assertions.Count - $script:FailCount), $script:FailCount)
    Write-Host ("evidence: " + $evidenceDir)
    if ($script:FailCount -gt 0) { exit 1 }
}
