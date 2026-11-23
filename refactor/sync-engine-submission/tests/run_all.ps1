# Master test runner: runs every acceptance case in its own fresh run
# root and writes per-case evidence under 运行结果\<case>\.
# Usage:  powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
$ErrorActionPreference = "Stop"
. (Join-Path (Split-Path -Parent $PSCommandPath) "lib.ps1")

# Clean slate: these image names belong to this submission only, so killing
# leftovers (e.g. from a crashed earlier run) cannot hurt anything else.
taskkill /F /IM syncclient.exe 2>$null | Out-Null
taskkill /F /IM server.exe 2>$null | Out-Null
Start-Sleep -Milliseconds 500

$cases = @(
    @{ Name = "U1-boot";        Script = "u1_boot.ps1" },
    @{ Name = "U2-files";       Script = "u2_files.ps1" },
    @{ Name = "U3-events";      Script = "u3_events.ps1" },
    @{ Name = "C1-conflict";    Script = "c1_conflict_edit_edit.ps1" },
    @{ Name = "C2-delete-edit"; Script = "c2_delete_edit.ps1" },
    @{ Name = "C3-fault";       Script = "c3_fault_receipt.ps1" },
    @{ Name = "C4-kill";        Script = "c4_kill_restore.ps1" },
    @{ Name = "C5-segment-replay"; Script = "c5_segment_and_replay.ps1" },
    @{ Name = "C6-pause";       Script = "c6_pause_resume.ps1" }
)

$results = @()
foreach ($c in $cases) {
    $case = New-Case $c.Name
    Write-Host ""
    Write-Host ("#### CASE " + $c.Name + "  (root=" + $case.Root + ")")
    $sw = [Diagnostics.Stopwatch]::StartNew()
    try {
        # Dot-source so the case script shares this scope (and lib.ps1 vars).
        . (Join-Path $Script:SUBMISSION ("tests\cases\" + $c.Script)) $case
        $results += [ordered]@{ case = $c.Name; result = "PASS"; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
        Write-Host ("#### " + $c.Name + " PASS")
    } catch {
        $results += [ordered]@{ case = $c.Name; result = "FAIL"; error = $_.Exception.Message; seconds = [math]::Round($sw.Elapsed.TotalSeconds, 1) }
        Write-Host ("#### " + $c.Name + " FAIL: " + $_.Exception.Message)
        # Keep going: every case uses its own root, later cases are unaffected.
    }
    & $Script:SYNCCTL down --root $case.Root 2>$null | Out-Null
}

Write-Host ""
Write-Host "==== SUMMARY ===="
$results | ForEach-Object { Write-Host ("  " + $_.result.PadRight(6) + " " + $_.case + "  (" + $_.seconds + "s" + $(if ($_.error) { ", " + $_.error }) + ")") }
$summaryPath = Join-Path $Script:EVIDENCE "summary.txt"
$results | ConvertTo-Json | Out-File -Encoding utf8 $summaryPath
$failed = @($results | Where-Object { $_.result -eq "FAIL" })
if ($failed.Count -gt 0) {
    Write-Host ("RESULT: " + $failed.Count + " case(s) FAILED")
    exit 1
}
Write-Host "RESULT: ALL PASS"
exit 0
