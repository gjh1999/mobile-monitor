# C5: line-range import of the event file, then read-only replay with an
# observable call record (tools_dispatched == 0).
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$eventsFile = Join-Path $Script:SUBMISSION "公开事件.jsonl"

Invoke-Step "C5.1 import lines 1-3 only, B sees an incomplete turn" {
    $r = Get-Json "http://127.0.0.1:8731/import" ("{`"file`": `"" + ($eventsFile -replace '\\','/') + "`", `"from`": 1, `"to`": 3}")
    ($r | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "import-lines-1-3.json")
    Assert-True ($r.submitted_lines -eq 3) "3 lines submitted (range 1..3)"
    Wait-Converge $Case
    $dump = Get-Json "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01&format=json"
    ($dump | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-dump-partial.json")
    $ids = $dump.threads[0].turns[0].items | ForEach-Object { $_.item_id }
    Assert-True ($ids.Count -eq 3) "partial import shows 3 items"
}

Invoke-Step "C5.2 import lines 4-6; B completes the same turn" {
    $r = Get-Json "http://127.0.0.1:8731/import" ("{`"file`": `"" + ($eventsFile -replace '\\','/') + "`", `"from`": 4, `"to`": 6}")
    ($r | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "import-lines-4-6.json")
    Wait-Converge $Case
    $dump = Get-Json "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01&format=json"
    ($dump | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-dump-segmented.json")
    $items = $dump.threads[0].turns[0].items
    Assert-True ($items.Count -eq 4) "segmented import assembles the full turn"
    Assert-True ($items[3].text -eq "平均值是 20。") "assistant text intact after segmented import"
}

Invoke-Step "C5.3 re-import full file: everything idempotent" {
    $r = Get-Json "http://127.0.0.1:8731/import" ("{`"file`": `"" + ($eventsFile -replace '\\','/') + "`"}")
    Assert-True ($r.new_local -eq 0) "full re-import adds nothing"
    Wait-Converge $Case
    $sv = Get-Json "http://127.0.0.1:8730/v1/status"
    Assert-True ($sv.event_count -eq 6) "server still holds exactly 6 unique events"
}

Invoke-Step "C5.4 read-only replay with observable call record" {
    $rep = Get-Json "http://127.0.0.1:8732/replay" ("{`"thread`": `"thread-weather`", `"turn`": `"turn-01`"}")
    ($rep | ConvertTo-Json -Depth 3) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-replay.json")
    Assert-True ($rep.tools_dispatched -eq 0) "replay dispatched no tools"
    Assert-True ($rep.tool_calls_seen -eq 1) "replay shows the historical tool call"
    Assert-True ($rep.items -eq 4) "replay walked 4 items"
    Assert-True (($rep.transcript -match "NOT dispatched") -or ($rep.transcript -match "read-only")) "transcript is explicitly marked read-only"
}
