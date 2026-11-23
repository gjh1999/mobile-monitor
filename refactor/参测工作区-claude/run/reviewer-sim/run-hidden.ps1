param(
    [Parameter(Mandatory = $true)][string]$HiddenEvents,
    [Parameter(Mandatory = $true)][string]$ExpectedHistory
)

$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $workspace 'scripts\lib.ps1')

$serverExe = Join-Path $script:BinDir 'server.exe'
$clientExe = Join-Path $script:BinDir 'sync-client.exe'
if ((-not (Test-Path -LiteralPath $serverExe)) -or (-not (Test-Path -LiteralPath $clientExe))) {
    throw 'binaries missing; run scripts\build.ps1 first'
}
$sampleDir = Get-ChildItem -LiteralPath $workspace -Directory |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'summary.py') } |
    Select-Object -First 1
if ($null -eq $sampleDir) { throw 'sample project not found' }

$simRoot = $PSScriptRoot
$script:Assertions = New-Object System.Collections.ArrayList
$script:FailCount = 0
$script:LogSeq = 0
$script:cur = $null
$script:expectedHash = ''
$script:copyRel = ''
$expected = Get-Content -LiteralPath $ExpectedHistory -Raw -Encoding UTF8 | ConvertFrom-Json

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
    param([string]$Exe, [string[]]$Arguments, [string]$Label)
    $script:LogSeq++
    $out = Join-Path $script:cur.LogDir ("{0}-{1}.out.log" -f $Label, $script:LogSeq)
    $err = Join-Path $script:cur.LogDir ("{0}-{1}.err.log" -f $Label, $script:LogSeq)
    $proc = Start-Process -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $script:cur.Dir `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    return [pscustomobject]@{ Proc = $proc; Out = $out; Err = $err; Label = $Label }
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
    $dir = Join-Path $simRoot $Name
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
    $sp = Get-FreeTcpPort; $ap = Get-FreeTcpPort; $bp = Get-FreeTcpPort
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
    Write-Host ("===== hidden scenario {0} =====" -f $Name)
    return $script:cur
}
function Start-ServerRole {
    $role = Start-Role -Exe $serverExe -Label 'server' -Arguments @(
        '--addr', "127.0.0.1:$($script:cur.ServerPort)", '--state', (Quote-Arg $script:cur.ServerState))
    if (-not (Wait-HttpOk -Uri ($script:cur.ServerUrl + '/healthz') -TimeoutSec 30)) {
        Show-RoleLogs $role; throw 'coordinator unhealthy'
    }
    $script:cur.ServerRole = $role
}
function Start-ClientRole {
    param([string]$Which)
    if ($Which -eq 'a') {
        $role = Start-Role -Exe $clientExe -Label 'client-a' -Arguments @(
            '--device', 'A', '--project', (Quote-Arg $script:cur.ProjectA), '--state', (Quote-Arg $script:cur.StateA),
            '--server', $script:cur.ServerUrl, '--control', "127.0.0.1:$($script:cur.APort)", '--interval-ms', '300')
        if (-not (Wait-HttpOk -Uri ($script:cur.AUrl + '/healthz') -TimeoutSec 30)) {
            Show-RoleLogs $role; throw 'client A unhealthy'
        }
        $script:cur.ARole = $role
    } else {
        $role = Start-Role -Exe $clientExe -Label 'client-b' -Arguments @(
            '--device', 'B', '--project', (Quote-Arg $script:cur.ProjectB), '--state', (Quote-Arg $script:cur.StateB),
            '--server', $script:cur.ServerUrl, '--control', "127.0.0.1:$($script:cur.BPort)", '--interval-ms', '300')
        if (-not (Wait-HttpOk -Uri ($script:cur.BUrl + '/healthz') -TimeoutSec 30)) {
            Show-RoleLogs $role; throw 'client B unhealthy'
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
    if ($null -eq $script:cur) { return }
    Stop-Role $script:cur.BRole ($script:cur.BUrl + '/shutdown')
    Stop-Role $script:cur.ARole ($script:cur.AUrl + '/shutdown')
    Stop-Role $script:cur.ServerRole ($script:cur.ServerUrl + '/api/admin/shutdown')
    $script:cur.BRole = $null; $script:cur.ARole = $null; $script:cur.ServerRole = $null
    Start-Sleep -Milliseconds 300
}
function ForceKill-Scenario {
    foreach ($role in @($script:cur.BRole, $script:cur.ARole, $script:cur.ServerRole)) {
        if ($null -ne $role) { Stop-ProcessForce $role.Proc.Id }
    }
    $script:cur.BRole = $null; $script:cur.ARole = $null; $script:cur.ServerRole = $null
    Start-Sleep -Milliseconds 300
}
function Restart-ClientB {
    Stop-Role $script:cur.BRole ($script:cur.BUrl + '/shutdown')
    $script:cur.BRole = $null
    Start-Sleep -Milliseconds 300
    Start-ClientRole 'b'
}
function Test-HistoryMatches {
    param($Expected, $Actual)
    if ($null -eq $Actual) { return $false }
    if ([string]$Expected.thread_id -ne [string]$Actual.thread_id) { return $false }
    if (([string]$Expected.turn_id) -ne ([string]$Actual.turn_id)) { return $false }
    if (@($Expected.items).Count -ne @($Actual.items).Count) { return $false }
    for ($i = 0; $i -lt @($Expected.items).Count; $i++) {
        $e = $Expected.items[$i]
        $a = $Actual.items[$i]
        foreach ($prop in @('item_id', 'type', 'text', 'status', 'call_id', 'name')) {
            if ($null -ne $e.$prop) {
                if ([string]$e.$prop -ne [string]$a.$prop) { return $false }
            }
        }
        if ($null -ne $e.arguments) {
            if (($e.arguments | ConvertTo-Json -Depth 10 -Compress) -ne ($a.arguments | ConvertTo-Json -Depth 10 -Compress)) { return $false }
        }
        if ($null -ne $e.result) {
            if (($e.result | ConvertTo-Json -Depth 10 -Compress) -ne ($a.result | ConvertTo-Json -Depth 10 -Compress)) { return $false }
        }
    }
    return $true
}

function Run-H1 {
    $c = New-Scenario 'H1'
    Start-Scenario
    $initialHash = '748a88ea78f384b2d39609fcc0c327f871e52a203b4b93066595aaf158a6ada3'
    $expectedConflictHash = '5e0bcdd059a560fb5cee284124be5d22891e6e184c3b4e0b37c9c7ca869983ed'
    Copy-Item -Path (Join-Path $sampleDir.FullName '*') -Destination $c.ProjectA -Recurse -Force
    Assert-True 'H1 baseline reaches B' (Wait-Until -TimeoutSec 30 -Check {
            (Get-FileSha256 (Join-Path $script:cur.ProjectB 'data\readings.txt')) -eq $initialHash })
    $null = Invoke-JsonApi -Uri ($c.BUrl + '/pause') -Method 'POST' -Body @{} -TimeoutSec 5
    Assert-True 'H1 B offline' (Wait-Until -TimeoutSec 10 -Check { -not (Get-ClientStatus $script:cur.BPort).online })
    [IO.File]::AppendAllText((Join-Path $c.ProjectB 'data\readings.txt'), "31`n", [Text.UTF8Encoding]::new($false))
    Assert-True 'H1 offline edit on disk' ((Get-FileSha256 (Join-Path $c.ProjectB 'data\readings.txt')) -eq $expectedConflictHash)
    Remove-Item -LiteralPath (Join-Path $c.ProjectA 'data\readings.txt') -Force
    Assert-True 'H1 server accepted the remote delete' (Wait-Until -TimeoutSec 30 -Check {
            $e = Get-ServerFileEntry 'data/readings.txt'
            return (($null -ne $e) -and [bool]$e.deleted) })
    Save-Evidence 'h1-server-delete-receipt.json' (Get-ServerStatus)
    Save-Evidence 'h1-delete-oplog.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=data%2Freadings.txt'))
    $null = Invoke-JsonApi -Uri ($c.BUrl + '/resume') -Method 'POST' -Body @{} -TimeoutSec 5
    Assert-True 'H1 conflict appears after B reconnects' (Wait-Until -TimeoutSec 30 -Check {
            $st = Get-ClientStatus $script:cur.BPort
            return @($st.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' }).Count -gt 0 })
    $st = Get-ClientStatus $c.BPort
    $conf = $st.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' } | Select-Object -First 1
    $script:copyRel = [string]$conf.local_conflict_path
    $copyPath = Join-Path $c.ProjectB ($script:copyRel -replace '/', '\')
    Assert-True 'H1 modified version readable with exact SHA-256' ((Test-Path -LiteralPath $copyPath) -and ((Get-FileSha256 $copyPath) -eq $expectedConflictHash)) ("copy=" + $script:copyRel)
    Assert-True 'H1 original path not silently restored' (-not (Test-Path -LiteralPath (Join-Path $c.ProjectB 'data\readings.txt')))
    Save-Evidence 'h1-b-conflict-status.json' $st
    Save-Evidence 'h1-server-conflicts.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/conflicts'))
    Restart-ClientB
    Assert-True 'H1 conflict survives B restart' (Wait-Until -TimeoutSec 15 -Check {
            $s2 = Get-ClientStatus $script:cur.BPort
            $n = @($s2.conflicts | Where-Object { $_.kind -eq 'remote-deleted-vs-local-modified' }).Count
            $p = Join-Path $script:cur.ProjectB ($script:copyRel -replace '/', '\')
            return ($n -gt 0) -and (Test-Path -LiteralPath $p) -and ((Get-FileSha256 $p) -eq $expectedConflictHash) })
    Save-Evidence 'h1-b-after-restart.json' (Get-ClientStatus $c.BPort)
    Stop-Scenario
}

function Run-H2 {
    $c = New-Scenario 'H2'
    Start-Scenario
    $rows = @(Get-Content -LiteralPath $HiddenEvents -Encoding UTF8)
    $part1 = Join-Path $c.Dir 'part1.jsonl'
    $part2 = Join-Path $c.Dir 'part2.jsonl'
    [IO.File]::WriteAllLines($part1, [string[]]$rows[0..3], (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllLines($part2, [string[]]$rows[2..6], (New-Object Text.UTF8Encoding($false)))
    $line4 = $rows[3] | ConvertFrom-Json

    $imp1 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $part1 } -TimeoutSec 30
    Assert-True 'H2 part1 imported' ($imp1.accepted -eq 4) ($imp1 | ConvertTo-Json -Compress)
    $partialOk = Wait-Until -TimeoutSec 30 -Check {
        try {
            $h = Get-History $script:cur.BPort $expected.thread_id $expected.turn_id
            return ($h.items.Count -eq 4) -and ($h.items[3].text -eq $line4.text) -and ($h.items[3].status -eq 'streaming')
        } catch { return $false }
    }
    $partial = Get-History $c.BPort $expected.thread_id $expected.turn_id
    Save-Evidence 'h2-history-partial.json' $partial
    Assert-True 'H2 B shows incomplete reply before disconnect' $partialOk

    $null = Invoke-JsonApi -Uri ($c.BUrl + '/pause') -Method 'POST' -Body @{} -TimeoutSec 5
    $imp2 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $part2 } -TimeoutSec 30
    Assert-True 'H2 part2 dedupes replayed lines' (($imp2.accepted -eq 3) -and ($imp2.duplicates -eq 2)) ($imp2 | ConvertTo-Json -Compress)
    $null = Invoke-JsonApi -Uri ($c.BUrl + '/resume') -Method 'POST' -Body @{} -TimeoutSec 5
    $finalOk = Wait-Until -TimeoutSec 30 -Check {
        try { return Test-HistoryMatches $expected (Get-History $script:cur.BPort $expected.thread_id $expected.turn_id) } catch { return $false }
    }
    $final = Get-History $c.BPort $expected.thread_id $expected.turn_id
    Save-Evidence 'h2-history-final.json' $final
    Assert-True 'H2 final history matches expected logic fields' $finalOk

    $imp3 = Invoke-JsonApi -Uri ($c.AUrl + '/import') -Method 'POST' -Body @{ file = $HiddenEvents } -TimeoutSec 30
    Assert-True 'H2 full file replay deduplicated' (($imp3.accepted -eq 0) -and ($imp3.duplicates -eq 7)) ($imp3 | ConvertTo-Json -Compress)
    Restart-ClientB
    $after = Get-History $c.BPort $expected.thread_id $expected.turn_id
    Assert-True 'H2 history matches expected after B restart' (Test-HistoryMatches $expected $after)
    Save-Evidence 'h2-history-after-restart.json' $after

    $replay = Invoke-JsonApi -Uri ($c.BUrl + '/replay?thread_id=' + [Uri]::EscapeDataString($expected.thread_id) + '&turn_id=' + [Uri]::EscapeDataString($expected.turn_id)) -TimeoutSec 10
    Assert-True 'H2 zero tool dispatch' (($replay.tool_dispatch_attempts -eq 0) -and ($replay.tool_call_items_observed -eq 1)) ($replay | ConvertTo-Json -Depth 10 -Compress)
    $marker = Get-ChildItem -LiteralPath $c.Dir -Recurse -File -Filter 'replayed.txt' -ErrorAction SilentlyContinue
    Assert-True 'H2 no marker side effect' ($null -eq $marker)
    Save-Evidence 'h2-replay.json' $replay
    Stop-Scenario
}

function Run-H3 {
    $c = New-Scenario 'H3'
    Start-Scenario
    $expectedHash = '1984ce680995c0b0e46432d070ab1307df43324309f4dd6252a0b9632939fcd2'
    Assert-True 'H3 empty queues converged' (Wait-Until -TimeoutSec 20 -Check {
            (Get-ClientStatus $script:cur.APort).queue_empty -and (Get-ClientStatus $script:cur.BPort).queue_empty })
    $null = Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/fault/drop-next-commit-ack') -Method 'POST' -Body @{ enabled = $true } -TimeoutSec 5
    $notes = Join-Path $c.ProjectA 'notes'
    New-Item -ItemType Directory -Force -Path $notes | Out-Null
    $tmp = Join-Path $c.Dir 'receipt.tmp'
    [IO.File]::WriteAllText($tmp, "sample-42`n", [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tmp -Destination (Join-Path $notes 'receipt.txt')
    $blocked = Wait-Until -TimeoutSec 30 -Check { $null -ne (Get-ClientStatus $script:cur.APort).awaiting_confirmation }
    $statusA = Get-ClientStatus $c.APort
    Save-Evidence 'h3-a-awaiting.json' $statusA
    Save-Evidence 'h3-server-committed.json' (Get-ServerStatus)
    Save-Evidence 'h3-oplog-before-retry.json' (Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=notes%2Freceipt.txt'))
    Assert-True 'H3 A paused before first retry' $blocked
    $entry = Get-ServerFileEntry 'notes/receipt.txt'
    Assert-True 'H3 commit persisted with expected hash' ((($null -ne $entry) -and (-not $entry.deleted) -and ($entry.hash -eq $expectedHash)) ) ("hash=" + $entry.hash)
    Assert-True 'H3 server has exactly one applied op' ((Get-ServerStatus).applied_ops -eq 1)
    Start-Sleep -Seconds 2
    Assert-True 'H3 no immediate retry while awaiting' ($null -ne (Get-ClientStatus $c.APort).awaiting_confirmation -and (Get-ServerStatus).applied_ops -eq 1)
    ForceKill-Scenario
    Start-Scenario -Order @('server', 'b', 'a')
    Assert-True 'H3 B receives expected bytes after restart and retry' (Wait-Until -TimeoutSec 30 -Check {
            (Get-FileSha256 (Join-Path $script:cur.ProjectB 'notes\receipt.txt')) -eq $expectedHash })
    Assert-True 'H3 A queue clears' (Wait-Until -TimeoutSec 30 -Check { (Get-ClientStatus $script:cur.APort).queue_empty })
    $srv = Get-ServerStatus
    $oplog = Invoke-JsonApi -Uri ($c.ServerUrl + '/api/admin/oplog?path=notes%2Freceipt.txt')
    Assert-True 'H3 exactly one logical commit' (($srv.applied_ops -eq 1) -and ($oplog.count -eq 1)) ("applied_ops=" + $srv.applied_ops + " oplog=" + $oplog.count)
    Assert-True 'H3 receipt resolution recorded' (@((Get-ClientStatus $c.APort).receipt_log | Where-Object { $null -ne $_.resolution }).Count -ge 1)
    Save-Evidence 'h3-after-retry-server.json' $srv
    Save-Evidence 'h3-after-retry-a.json' (Get-ClientStatus $c.APort)
    Save-Evidence 'h3-after-retry-b.json' (Get-ClientStatus $c.BPort)
    Stop-Role $c.BRole ($c.BUrl + '/shutdown'); $c.BRole = $null
    Stop-Role $c.ARole ($c.AUrl + '/shutdown'); $c.ARole = $null
    Start-Sleep -Milliseconds 300
    Start-ClientRole 'a'; Start-ClientRole 'b'
    Assert-True 'H3 second restart keeps file and empty queues' (Wait-Until -TimeoutSec 30 -Check {
            ((Get-FileSha256 (Join-Path $script:cur.ProjectB 'notes\receipt.txt')) -eq $expectedHash) -and
            (Get-ClientStatus $script:cur.APort).queue_empty -and (Get-ClientStatus $script:cur.BPort).queue_empty })
    Assert-True 'H3 still one applied op after second restart' ((Get-ServerStatus).applied_ops -eq 1)
    Stop-Scenario
}

$fatal = $null
try {
    Write-Host ("HIDDEN SIM ROOT: " + $simRoot)
    try { Run-H1 } catch { $fatal = $_.Exception.Message; Assert-True 'H1 fatal' $false $fatal; Stop-Scenario }
    try { Run-H2 } catch { $fatal = $_.Exception.Message; Assert-True 'H2 fatal' $false $fatal; Stop-Scenario }
    try { Run-H3 } catch { $fatal = $_.Exception.Message; Assert-True 'H3 fatal' $false $fatal; Stop-Scenario }
} finally {
    if ($null -ne $script:cur) { Stop-Scenario }
    $result = [ordered]@{
        sim_root = $simRoot; finished_at = (Get-Date).ToString('o'); fatal = $fatal
        passed = ($script:Assertions.Count - $script:FailCount); failed = $script:FailCount
        assertions = $script:Assertions
    }
    $result | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $simRoot 'hidden-sim-result.json')
    Write-Host ''
    Write-Host ("HIDDEN SIM RESULT: passed={0} failed={1}" -f ($script:Assertions.Count - $script:FailCount), $script:FailCount)
    if ($script:FailCount -gt 0) { exit 1 }
}
