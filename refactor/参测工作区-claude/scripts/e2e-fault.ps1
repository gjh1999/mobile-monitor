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
    $faultRoot = Join-Path $workspace ('run\fault-' + $stamp)
} else {
    $faultRoot = Resolve-RunPath $Root
}
$faultRoot = [IO.Path]::GetFullPath($faultRoot)
New-Item -ItemType Directory -Force -Path $faultRoot | Out-Null

$sampleDir = Get-ChildItem -LiteralPath $workspace -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'summary.py') } |
    Select-Object -First 1
if ($null -eq $sampleDir) { throw 'sample project not found at workspace root' }

$script:Assertions = New-Object System.Collections.ArrayList
$script:FailCount = 0
$script:LogSeq = 0
$script:cur = $null
$script:expectedHash = ''
$script:copyRel = ''

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

function Get-ServerStatus { return Invoke-JsonApi -Uri ($script:cur.ServerUrl + '/api/status') -TimeoutSec 5 }

function Get-ServerFileEntry {
    param([string]$Path)
    $s = Get-ServerStatus
    $p = $s.files.PSObject.Properties[$Path]
    if ($null -eq $p) { return $null }
    return $p.Value
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
    $path = Join-Path $script:cur.EvidenceDir $Name
    $Value | ConvertTo-Json -Depth 40 | Set-Content -Encoding UTF8 -LiteralPath $path
}

function Start-Role {
    param([string]$Exe, [string[]]$Arguments, [string]$Label, [string]$ControlUrl = '')
    $script:LogSeq++
    $out = Join-Path $script:cur.LogDir ("{0}-{1}.out.log" -f $Label, $script:LogSeq)
    $err = Join-Path $script:cur.LogDir ("{0}-{1}.err.log" -f $Label, $script:LogSeq)
    $proc = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $script:cur.Dir `
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

function New-Scenario {
    param([string]$Name)
    $dir = Join-Path $faultRoot $Name
    $serverState = Join-Path $dir 'server-state'
    $stateA = Join-Path $dir 'state-a'
    $stateB = Join-Path $dir 'state-b'
    $projectA = Join-Path $dir 'project-a'
    $projectB = Join-Path $dir 'project-b'
    $logDir = Join-Path $dir 'logs'
    $evidenceDir = Join-Path $dir 'evidence'
    foreach ($d in @($serverState, $stateA, $stateB, $projectA, $projectB, $logDir, $evidenceDir)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
    $sp = Get-FreeTcpPort
    $ap = Get-FreeTcpPort
    $bp = Get-FreeTcpPort
    while ($ap -eq $sp) { $ap = Get-FreeTcpPort }
    while (($bp -eq $sp) -or ($bp -eq $ap)) { $bp = Get-FreeTcpPort }
    $script:cur = [pscustomobject]@{
        Name = $Name; Dir = $dir
        ServerPort = $sp; APort = $ap; BPort = $bp
        ServerUrl = "http://127.0.0.1:$sp"; AUrl = "http://127.0.0.1:$ap"; BUrl = "http://127.0.0.1:$bp"
        ServerState = $serverState; StateA = $stateA; StateB = $stateB
        ProjectA = $projectA; ProjectB = $projectB
        LogDir = $logDir; EvidenceDir = $evidenceDir
        ServerRole = $null; ARole = $null; BRole = $null
    }
    Write-Host ''
    Write-Host ("===== scenario {0} : {1} =====" -f $Name, $dir)
    return $script:cur
}

function Start-ServerRole {
    $role = Start-Role -Exe $serverExe -Label 'server' -Arguments @(
        '--addr', "127.0.0.1:$($script:cur.ServerPort)", '--state', (Quote-Arg $script:cur.ServerState))
    if (-not (Wait-HttpOk -Uri ($script:cur.ServerUrl + '/healthz') -TimeoutSec 30)) {
        Show-RoleLogs $role
        throw 'coordinator did not become healthy'
    }
    $script:cur.ServerRole = $role
}

function Start-ClientRole {
    param([string]$Which)
    if ($Which -eq 'a') {
        $role = Start-Role -Exe $clientExe -Label 'client-a' -ControlUrl $script:cur.AUrl -Arguments @(
            '--device', 'A', '--project', (Quote-Arg $script:cur.ProjectA), '--state', (Quote-Arg $script:cur.StateA),
            '--server', $script:cur.ServerUrl, '--control', "127.0.0.1:$($script:cur.APort)", '--interval-ms', '300')
        if (-not (Wait-HttpOk -Uri ($script:cur.AUrl + '/healthz') -TimeoutSec 30)) {
            Show-RoleLogs $role
            throw 'client A did not become healthy'
        }
        $script:cur.ARole = $role
    } else {
        $role = Start-Role -Exe $clientExe -Label 'client-b' -ControlUrl $script:cur.BUrl -Arguments @(
            '--device', 'B', '--project', (Quote-Arg $script:cur.ProjectB), '--state', (Quote-Arg $script:cur.StateB),
            '--server', $script:cur.ServerUrl, '--control', "127.0.0.1:$($script:cur.BPort)", '--interval-ms', '300')
        if (-not (Wait-HttpOk -Uri ($script:cur.BUrl + '/healthz') -TimeoutSec 30)) {
            Show-RoleLogs $role
            throw 'client B did not become healthy'
        }
        $script:cur.BRole = $role
    }
}

function Start-Scenario {
    param([string[]]$Order = @('server', 'a', 'b'))
    foreach ($r in $Order) {
        if ($r -eq 'server') { Start-ServerRole }
        elseif ($r -eq 'a') { Start-ClientRole 'a' }
        else { Start-ClientRole 'b' }
    }
}

function Stop-Scenario {
    Stop-Role $script:cur.BRole ($script:cur.BUrl + '/shutdown')
    Stop-Role $script:cur.ARole ($script:cur.AUrl + '/shutdown')
    Stop-Role $script:cur.ServerRole ($script:cur.ServerUrl + '/api/admin/shutdown')
    if ($null -ne $script:cur) {
        $script:cur.BRole = $null
        $script:cur.ARole = $null
        $script:cur.ServerRole = $null
    }
    Start-Sleep -Milliseconds 300
}

function ForceKill-Scenario {
    foreach ($role in @($script:cur.BRole, $script:cur.ARole, $script:cur.ServerRole)) {
        if ($null -ne $role) { Stop-ProcessForce $role.Proc.Id }
    }
    $script:cur.BRole = $null
    $script:cur.ARole = $null
    $script:cur.ServerRole = $null
    Start-Sleep -Milliseconds 300
}

function Restart-ClientB {
    Stop-Role $script:cur.BRole ($script:cur.BUrl + '/shutdown')
    $script:cur.BRole = $null
    Start-Sleep -Milliseconds 300
    Start-ClientRole 'b'
}

# ---------------- F1: offline modification meets remote delete ----------------
function Run-F1 {
    $c = New-Scenario 'F1'
    Start-Scenario
    $initialHash = '748a88ea78f384b2d39609fcc0c327f871e52a203b4b93066595aaf158a6ada3'
    Copy-Item -Path (Join-Path $sampleDir.FullName '*') -Destination $c.ProjectA -Recurse -Force
    $okSeed = Wait-Until -TimeoutSec 30 -Check {
        (Get-FileSha256 (Join-Path $script:cur.ProjectB 'data\readings.txt')) -eq $initialHash
    }
    Assert-True 'F1 baseline reaches B' $okSeed
    Assert-True 'F1 baseline queues converged' (Wait-Until -TimeoutSec 30 -Check {
            (Get-ClientStatus $script:cur.APort).queue_empty -and (Get-ClientStatus $script:cur.BPort).queue_empty
        })

    $null = Invoke-JsonApi -Uri ($c.BUrl + '/pause') -Method 'POST' -Body @{} -TimeoutSec 5
    Assert-True 'F1 B transport paused' (Wait-Until -TimeoutSec 10 -Check { -not (Get-ClientStatus $script:cur.BPort).online })

    $bReadings = Join-Path $c.ProjectB 'data\readings.txt'
    [IO.File]::AppendAllText($bReadings, "31`n", [Text.UTF8Encoding]::new($false))
    $script:expectedHash = Get-FileSha256 $bReadings
    Assert-True 'F1 local edit stays on disk while offline' ((Get-FileSha256 $bReadings) -eq $script:expectedHash) ("hash=" + $script:expectedHash)
    Assert-True 'F1 B reports pending local change' (Wait-Until -TimeoutSec 10 -Check { (Get-ClientStatus $script:cur.BPort).pending_count -ge 1 })

    Remove-Item -LiteralPath (Join-Path $c.ProjectA 'data\readings.txt') -Force
    $deleteCommitted = Wait-Until -TimeoutSec 30 -Check {
        $e = Get-ServerFileEntry 'data/readings.txt'
        return (($null -ne $e) -and [bool]$e.deleted)
    }
    Assert-True 'F1 remote delete is committed before B reconnects' $deleteCommitted
    Save-Evidence 'f1-server-after-delete.json' (Get-ServerStatus)

    $null = Invoke-JsonApi -Uri ($c.BUrl + '/resume') -Method 'POST' -Body @{} -TimeoutSec 5
    $gotConflict = Wait-Until -TimeoutSec 30 -Check {
        $st = Get-ClientStatus $script:cur.BPort
        $n = @($st.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' }).Count
        return $n -gt 0
    }
    Assert-True 'F1 conflict recorded on B after resume' $gotConflict
    $st = Get-ClientStatus $c.BPort
    $conf = $st.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' } | Select-Object -First 1
    Save-Evidence 'f1-b-status-conflict.json' $st
    Save-Evidence 'f1-server-conflicts.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/conflicts'))
    if ($null -ne $conf) {
        $script:copyRel = [string]$conf.local_conflict_path
        $copyPath = Join-Path $c.ProjectB ($script:copyRel -replace '/', '\')
        Assert-True 'F1 modified content is preserved in conflict copy' ((Test-Path -LiteralPath $copyPath) -and ((Get-FileSha256 $copyPath) -eq $script:expectedHash)) ("copy=" + $script:copyRel + " hash=" + (Get-FileSha256 $copyPath))
        Assert-True 'F1 original path is not silently restored on B' (-not (Test-Path -LiteralPath (Join-Path $c.ProjectB 'data\readings.txt')))
        $null = Invoke-JsonApi -Uri ($c.BUrl + '/sync') -Method 'POST' -Body @{} -TimeoutSec 5
        $onA = Wait-Until -TimeoutSec 30 -Check {
            if ([string]::IsNullOrWhiteSpace($script:copyRel)) { return $false }
            $p = Join-Path $script:cur.ProjectA ($script:copyRel -replace '/', '\')
            return (Test-Path -LiteralPath $p) -and ((Get-FileSha256 $p) -eq $script:expectedHash)
        }
        Assert-True 'F1 conflict copy also reaches A' $onA
    } else {
        Assert-True 'F1 conflict record present' $false 'no remote-deleted-vs-local-modified record'
    }

    Restart-ClientB
    $afterRestart = Wait-Until -TimeoutSec 15 -Check {
        $s2 = Get-ClientStatus $script:cur.BPort
        $n = @($s2.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' }).Count
        if ([string]::IsNullOrWhiteSpace($script:copyRel)) { return $false }
        $p = Join-Path $script:cur.ProjectB ($script:copyRel -replace '/', '\')
        return ($n -gt 0) -and (Test-Path -LiteralPath $p) -and ((Get-FileSha256 $p) -eq $script:expectedHash) -and (-not (Test-Path -LiteralPath (Join-Path $script:cur.ProjectB 'data\readings.txt')))
    }
    Assert-True 'F1 conflict and modified content survive B restart' $afterRestart
    Save-Evidence 'f1-b-status-after-restart.json' (Get-ClientStatus $c.BPort)
    Save-Evidence 'f1-server-oplog.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=data%2Freadings.txt'))
    Stop-Scenario
}

# ---------------- F2: interrupted reply, overlapping replay, no tool dispatch ----------------
function Run-F2 {
    $c = New-Scenario 'F2'
    Start-Scenario
    $fixture = Join-Path $c.Dir 'fixture-events.jsonl'
    $lines = @(
        '{"event_id":"f2-1","thread_id":"thread-f2","turn_id":"turn-01","seq":1,"type":"user.message","item_id":"u1","text":"Run the check."}',
        '{"event_id":"f2-2","thread_id":"thread-f2","turn_id":"turn-01","seq":2,"type":"tool.call","item_id":"c1","call_id":"call-f2","name":"fixture.append_marker","arguments":{"path":"evidence/replayed.txt","line":"dispatch"}}',
        '{"event_id":"f2-3","thread_id":"thread-f2","turn_id":"turn-01","seq":3,"type":"tool.result","item_id":"r1","call_id":"call-f2","result":{"accepted":true}}',
        '{"event_id":"f2-4","thread_id":"thread-f2","turn_id":"turn-01","seq":4,"type":"assistant.delta","item_id":"a1","text":"哈"}',
        '{"event_id":"f2-5","thread_id":"thread-f2","turn_id":"turn-01","seq":5,"type":"assistant.delta","item_id":"a1","text":"哈"}',
        '{"event_id":"f2-6","thread_id":"thread-f2","turn_id":"turn-01","seq":6,"type":"assistant.delta","item_id":"a1","text":"，记录已保存。"}',
        '{"event_id":"f2-7","thread_id":"thread-f2","turn_id":"turn-01","seq":7,"type":"assistant.done","item_id":"a1"}'
    )
    [IO.File]::WriteAllText($fixture, (($lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))

    $imp1 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $fixture; start_line = 1; end_line = 4 } -TimeoutSec 30
    Assert-True 'F2 first 4 lines imported' (($imp1.accepted -eq 4) -and ($imp1.duplicates -eq 0)) ($imp1 | ConvertTo-Json -Compress)
    $h1ok = Wait-Until -TimeoutSec 30 -Check {
        try {
            $h = Get-History $script:cur.BPort 'thread-f2' 'turn-01'
            return ($h.items.Count -eq 4) -and ($h.items[3].text -eq '哈') -and ($h.items[3].status -eq 'streaming')
        } catch { return $false }
    }
    $h1 = Get-History $c.BPort 'thread-f2' 'turn-01'
    Save-Evidence 'f2-history-interrupted.json' $h1
    Assert-True 'F2 B shows incomplete reply before reconnect' $h1ok

    $null = Invoke-JsonApi -Uri ($c.BUrl + '/pause') -Method 'POST' -Body @{} -TimeoutSec 5
    $imp2 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $fixture; start_line = 3; end_line = 7 } -TimeoutSec 30
    Assert-True 'F2 overlapping batch dedupes the replayed lines' (($imp2.accepted -eq 3) -and ($imp2.duplicates -eq 2)) ($imp2 | ConvertTo-Json -Compress)
    $null = Invoke-JsonApi -Uri ($c.BUrl + '/resume') -Method 'POST' -Body @{} -TimeoutSec 5
    $finalOk = Wait-Until -TimeoutSec 30 -Check {
        try {
            $h = Get-History $script:cur.BPort 'thread-f2' 'turn-01'
            return ($h.items.Count -eq 4) -and ($h.items[3].text -eq '哈哈，记录已保存。') -and ($h.items[3].status -eq 'completed')
        } catch { return $false }
    }
    $final = Get-History $c.BPort 'thread-f2' 'turn-01'
    Save-Evidence 'f2-history-complete.json' $final
    Assert-True 'F2 equal-text deltas both count and reply completes' $finalOk
    Assert-True 'F2 tool result stays associated with call-f2' ($final.items[2].call_id -eq 'call-f2')

    $imp3 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $fixture } -TimeoutSec 30
    Assert-True 'F2 full replay is fully deduplicated' (($imp3.accepted -eq 0) -and ($imp3.duplicates -eq 7)) ($imp3 | ConvertTo-Json -Compress)
    Restart-ClientB
    $afterRestart = Get-History $c.BPort 'thread-f2' 'turn-01'
    $same = (($final.items | ConvertTo-Json -Depth 20 -Compress) -eq ($afterRestart.items | ConvertTo-Json -Depth 20 -Compress))
    Assert-True 'F2 history is identical after B restart' $same
    Save-Evidence 'f2-history-after-restart.json' $afterRestart

    $replay = Invoke-JsonApi -Uri ($c.BUrl + '/replay?thread_id=thread-f2&turn_id=turn-01') -TimeoutSec 10
    Assert-True 'F2 replay performs zero tool dispatch' (($replay.tool_dispatch_attempts -eq 0) -and ($replay.tool_call_items_observed -eq 1)) ($replay | ConvertTo-Json -Depth 10 -Compress)
    $marker = Get-ChildItem -LiteralPath $c.Dir -Recurse -File -Filter 'replayed.txt' -ErrorAction SilentlyContinue
    Assert-True 'F2 replay creates no marker file' ($null -eq $marker)
    Save-Evidence 'f2-replay.json' $replay
    Save-Evidence 'f2-server-status.json' (Get-ServerStatus)
    Stop-Scenario
}

# ---------------- F3: committed, success receipt lost, restart and retry ----------------
function Run-F3 {
    $c = New-Scenario 'F3'
    Start-Scenario
    Assert-True 'F3 stack starts with converged empty queues' (Wait-Until -TimeoutSec 20 -Check {
            (Get-ClientStatus $script:cur.APort).queue_empty -and (Get-ClientStatus $script:cur.BPort).queue_empty
        })

    $null = Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/fault/drop-next-commit-ack') -Method 'POST' -Body @{ enabled = $true } -TimeoutSec 5
    $notesDir = Join-Path $c.ProjectA 'notes'
    New-Item -ItemType Directory -Force -Path $notesDir | Out-Null
    $content = "receipt-test`n"
    $tmp = Join-Path $c.Dir 'receipt.tmp'
    [IO.File]::WriteAllText($tmp, $content, [Text.UTF8Encoding]::new($false))
    $script:expectedHash = Get-FileSha256 $tmp
    Move-Item -LiteralPath $tmp -Destination (Join-Path $notesDir 'receipt.txt')

    $blocked = Wait-Until -TimeoutSec 30 -Check {
        $s = Get-ClientStatus $script:cur.APort
        return ($null -ne $s.awaiting_confirmation)
    }
    $statusA = Get-ClientStatus $c.APort
    Save-Evidence 'f3-a-status-receipt-lost.json' $statusA
    Save-Evidence 'f3-server-after-commit.json' (Get-ServerStatus)
    Save-Evidence 'f3-oplog.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=notes%2Freceipt.txt'))
    Assert-True 'F3 A pauses before first retry after lost receipt' $blocked
    if ($blocked) {
        Assert-True 'F3 A reports retry blocked and pending op' (($statusA.retry_blocked -eq $true) -and ($statusA.pending_count -ge 1)) ("pending=" + $statusA.pending_count)
        $srv = Get-ServerStatus
        $entry = Get-ServerFileEntry 'notes/receipt.txt'
        Assert-True 'F3 server persisted the commit before the drop' (($null -ne $entry) -and (-not $entry.deleted) -and ($entry.hash -eq $script:expectedHash) -and ($srv.applied_ops -eq 1)) ("applied_ops=" + $srv.applied_ops)
    } else {
        Assert-True 'F3 server persisted the commit before the drop' $false 'A never entered awaiting_confirmation'
    }

    Start-Sleep -Seconds 2
    $stillBlocked = Get-ClientStatus $c.APort
    $srvStill = Get-ServerStatus
    Assert-True 'F3 no immediate retry while paused for receipt' (($null -ne $stillBlocked.awaiting_confirmation) -and ($srvStill.applied_ops -eq 1))

    ForceKill-Scenario
    Start-Scenario -Order @('server', 'b', 'a')
    $delivered = Wait-Until -TimeoutSec 30 -Check {
        (Get-FileSha256 (Join-Path $script:cur.ProjectB 'notes\receipt.txt')) -eq $script:expectedHash
    }
    Assert-True 'F3 retry after restart delivers exactly one file to B' $delivered ("B=" + (Get-FileSha256 (Join-Path $c.ProjectB 'notes\receipt.txt')))
    Assert-True 'F3 A queue converges after retry' (Wait-Until -TimeoutSec 30 -Check {
            $s = Get-ClientStatus $script:cur.APort
            return [bool]$s.queue_empty
        })
    $srvAfter = Get-ServerStatus
    $oplog = Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=notes%2Freceipt.txt')
    Assert-True 'F3 exactly one logical commit survives the retry' (($srvAfter.applied_ops -eq 1) -and ($oplog.count -eq 1) -and ($oplog.entries[0].result -eq 'applied')) ("applied_ops=" + $srvAfter.applied_ops + " oplog=" + $oplog.count)
    $statusAfter = Get-ClientStatus $c.APort
    $resolved = @($statusAfter.receipt_log | Where-Object { $null -ne $_.resolution }).Count
    Assert-True 'F3 receipt log records the retry resolution' ($resolved -ge 1) ("resolved=" + $resolved)
    Save-Evidence 'f3-a-status-after-retry.json' $statusAfter
    Save-Evidence 'f3-server-after-retry.json' $srvAfter
    Save-Evidence 'f3-oplog-after-retry.json' $oplog
    Save-Evidence 'f3-b-status-after-retry.json' (Get-ClientStatus $c.BPort)

    Stop-Role $c.BRole ($c.BUrl + '/shutdown')
    $c.BRole = $null
    Stop-Role $c.ARole ($c.AUrl + '/shutdown')
    $c.ARole = $null
    Start-Sleep -Milliseconds 300
    Start-ClientRole 'a'
    Start-ClientRole 'b'
    $afterRestart = Wait-Until -TimeoutSec 30 -Check {
        ((Get-FileSha256 (Join-Path $script:cur.ProjectB 'notes\receipt.txt')) -eq $script:expectedHash) -and
        (Get-ClientStatus $script:cur.APort).queue_empty -and (Get-ClientStatus $script:cur.BPort).queue_empty
    }
    Assert-True 'F3 file and empty queues survive a second restart' $afterRestart
    Assert-True 'F3 no duplicate commit after second restart' ((Get-ServerStatus).applied_ops -eq 1)
    Save-Evidence 'f3-final-server-status.json' (Get-ServerStatus)
    Save-Evidence 'f3-final-status-a.json' (Get-ClientStatus $c.APort)
    Save-Evidence 'f3-final-status-b.json' (Get-ClientStatus $c.BPort)
    Stop-Scenario
}

$fatal = $null
try {
    Write-Host ("FAULT E2E ROOT: " + $faultRoot)
    try { Run-F1 } catch {
        $fatal = $_.Exception.Message
        Assert-True 'F1 fatal' $false $fatal
        if ($null -ne $script:cur) { Stop-Scenario }
    }
    try { Run-F2 } catch {
        $fatal = $_.Exception.Message
        Assert-True 'F2 fatal' $false $fatal
        if ($null -ne $script:cur) { Stop-Scenario }
    }
    try { Run-F3 } catch {
        $fatal = $_.Exception.Message
        Assert-True 'F3 fatal' $false $fatal
        if ($null -ne $script:cur) { Stop-Scenario }
    }
} finally {
    if ($null -ne $script:cur -and $null -ne $script:cur.ServerRole) { Stop-Scenario }
    $result = [ordered]@{
        run_root = $faultRoot
        finished_at = (Get-Date).ToString('o')
        fatal = $fatal
        passed = ($script:Assertions.Count - $script:FailCount)
        failed = $script:FailCount
        assertions = $script:Assertions
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $faultRoot 'e2e-fault-result.json')
    Write-Host ''
    Write-Host ("FAULT E2E RESULT: passed={0} failed={1}" -f ($script:Assertions.Count - $script:FailCount), $script:FailCount)
    Write-Host ("evidence: " + $faultRoot)
    if ($script:FailCount -gt 0) { exit 1 }
}
