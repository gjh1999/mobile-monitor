# C3: receipt-loss fault injection. The server durably commits, then drops
# the ack; the sender stays paused (no auto-retry) until the reviewer
# triggers the retry, which hits the idempotent path: same version,
# same commit count, one logical change.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$projA = Join-Path $Case.Root "project-a"

Invoke-Step "C3.1 arm single-shot commit-ack drop for client A" {
    & $Script:SYNCCTL fault arm --mode commit-ack --target A --count 1 2>&1 |
        Out-File -FilePath (Join-Path $Case.Dir "fault-arm.log")
    $f = Get-Json "http://127.0.0.1:8730/v1/admin/fault"
    Assert-True ($f.fault.armed -eq $true) "fault armed"
}

Invoke-Step "C3.2 A commits; server persists, ack is dropped" {
    Set-Content -NoNewline (Join-Path $projA "receipt-note.txt") -Value "injected`n" -Encoding Ascii
    $rep = Get-Json "http://127.0.0.1:8731/sync" "{}"
    ($rep | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-sync-dropped.json")
    Assert-True ($rep.receipt_lost -eq $true) "A reports receipt lost"
    Assert-True ($rep.pending_push -ge 1) "A keeps the change pending (paused before first retry)"

    $sv = Get-Json "http://127.0.0.1:8730/v1/status"
    ($sv | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "server-before-retry.json")
    $Script:verBefore = $sv.version
    $Script:commitsBefore = $sv.commit_count
    Assert-True ($sv.version -eq 1) ("server durably committed BEFORE any receipt (version=" + $sv.version + ")")
    Assert-True ($sv.commit_count -eq 1) "commit count is 1 before retry"
    Assert-True ($sv.fault.armed -eq $false) "single-shot fault disarmed itself"
}

Invoke-Step "C3.3 retry the same operation; idempotency keeps one logical result" {
    $rep2 = Get-Json "http://127.0.0.1:8731/sync" "{}"
    ($rep2 | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-sync-retry.json")
    Assert-True ($rep2.receipt_lost -eq $false) "retry confirmed"
    Assert-True ($rep2.version -eq $Script:verBefore) ("version unchanged after retry (" + $rep2.version + ")")

    $sv2 = Get-Json "http://127.0.0.1:8730/v1/status"
    ($sv2 | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "server-after-retry.json")
    Assert-True ($sv2.version -eq $Script:verBefore) ("server version still " + $verBefore + " after retry")
    Assert-True ($sv2.commit_count -eq $Script:commitsBefore) "commit count unchanged after retry"
    Assert-True ($sv2.dup_hits -ge 1) ("idempotent hit recorded (dup_hits=" + $sv2.dup_hits + ")")
}

Invoke-Step "C3.4 B sees exactly one copy" {
    Wait-Converge $Case
    $files = Get-ClientFileList 8732
    ($files | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-files.json")
    $matches = @($files | Where-Object { $_.path -eq "receipt-note.txt" })
    Assert-True ($matches.Count -eq 1) "exactly one receipt-note.txt on B"

    & $Script:SYNCCTL serverlog --limit 10 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "server-log.json")
}
