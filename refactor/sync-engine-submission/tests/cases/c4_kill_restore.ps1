# C4: hard-kill each process, restore it on the same data dir, verify
# recovery and continued convergence.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$projA = Join-Path $Case.Root "project-a"
$projB = Join-Path $Case.Root "project-b"

Invoke-Step "C4.1 kill B; A keeps working" {
    & $Script:SYNCCTL kill b --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "kill-b.log")
    Set-Content -NoNewline (Join-Path $projA "during-outage.txt") -Value "outage`n" -Encoding Ascii
    $rep = Get-Json "http://127.0.0.1:8731/sync" "{}"
    Assert-True ($rep.pushed -eq 1) "A pushed its change while B was down"
}

Invoke-Step "C4.2 restart B on the same data dir; it converges" {
    & $Script:SYNCCTL start b --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "start-b.log")
    Wait-Converge $Case
    Assert-True (Test-Path (Join-Path $projB "during-outage.txt")) "B recovered the file created during the outage"
}

Invoke-Step "C4.3 kill server; both clients report unreachable, state intact" {
    & $Script:SYNCCTL kill server --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "kill-server.log")
    Set-Content -NoNewline (Join-Path $projA "after-server-death.txt") -Value "x`n" -Encoding Ascii
    $rep = Get-Json "http://127.0.0.1:8731/sync" "{}"
    ($rep | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-sync-server-down.json")
    Assert-True ($rep.error -ne "") "A reports the coordinator is down (change stays pending)"
}

Invoke-Step "C4.4 restart server on same data dir; everything converges" {
    & $Script:SYNCCTL start server --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "start-server.log")
    $sv = Get-Json "http://127.0.0.1:8730/v1/status"
    Assert-True ($sv.version -ge 1) "server restored its version from the durable log"
    Wait-Converge $Case
    Assert-True (Test-Path (Join-Path $projB "after-server-death.txt")) "change made while server was dead reached B"
    ($sv | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "server-after-restart.json")
}
