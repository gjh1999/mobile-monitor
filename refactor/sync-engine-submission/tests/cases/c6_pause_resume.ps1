# C6: pause/resume. Paused client refuses network I/O, keeps its local
# changes recoverable, and reports pending work explicitly.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$projA = Join-Path $Case.Root "project-a"
$projB = Join-Path $Case.Root "project-b"

Invoke-Step "C6.1 baseline" {
    Set-Content -NoNewline (Join-Path $projA "p-base.txt") -Value "b`n" -Encoding Ascii
    Wait-Converge $Case
    Assert-True (Test-Path (Join-Path $projB "p-base.txt")) "baseline reached B"
}

Invoke-Step "C6.2 pause B; A keeps changing; B stays consistent" {
    Get-Json "http://127.0.0.1:8732/pause" "{}" | Out-Null
    $sb = Get-Json "http://127.0.0.1:8732/status"
    Assert-True ($sb.online -eq $false) "B reports offline"

    Set-Content -NoNewline (Join-Path $projA "p-during.txt") -Value "d`n" -Encoding Ascii
    Set-Content -NoNewline (Join-Path $projB "p-local.txt") -Value "local`n" -Encoding Ascii
    $rep = Get-Json "http://127.0.0.1:8732/sync" "{}"
    ($rep | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-sync-paused.json")
    Assert-True ($rep.offline -eq $true) "paused sync refuses network"
    Assert-True (Test-Path (Join-Path $projB "p-local.txt")) "B's local change is on disk (recoverable)"

    Wait-Converge $Case   # A->server side converges; B stays offline
    Assert-True (-not (Test-Path (Join-Path $projB "p-during.txt"))) "B did not receive A's change while paused"
}

Invoke-Step "C6.3 resume B; queues drain and both sides converge" {
    Get-Json "http://127.0.0.1:8732/resume" "{}" | Out-Null
    $sb = Get-Json "http://127.0.0.1:8732/status"
    Assert-True ($sb.online -eq $true) "B reports online"
    Wait-Converge $Case
    Assert-True (Test-Path (Join-Path $projB "p-during.txt")) "B received A's change after resume"
    Assert-True (Test-Path (Join-Path $projA "p-local.txt")) "A received B's offline change after resume"
    Assert-FileHash (Join-Path $projA "p-local.txt") (Get-Sha (Join-Path $projB "p-local.txt")) "offline-made change bytes identical on A"
}
