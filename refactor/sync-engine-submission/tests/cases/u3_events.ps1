# U3: full message turn + replayed import idempotency.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$eventsFile = Join-Path $Script:SUBMISSION "公开事件.jsonl"

Invoke-Step "U3.1 import full file on A, read turn on B" {
    $r = Get-Json "http://127.0.0.1:8731/import" ("{`"file`": `"" + ($eventsFile -replace '\\','/') + "`"}")
    ($r | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "import1.json")
    Assert-True ($r.submitted_lines -eq 6) "6 lines submitted"
    Assert-True ($r.new_local -eq 6) "6 events accepted locally"
    Wait-Converge $Case

    $dump = Get-Json "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01&format=json"
    ($dump | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-dump-first.json")
    $items = $dump.threads[0].turns[0].items
    Assert-True ($items.Count -eq 4) "four logical records"

    Assert-True ($items[0].item_id -eq "u1" -and $items[0].kind -eq "user") "u1 is the user message"
    Assert-True ($items[0].text -eq "计算读数的平均值。") "u1 text preserved verbatim"
    Assert-True ($items[1].item_id -eq "c1" -and $items[1].kind -eq "tool_call") "c1 is the tool call"
    Assert-True ($items[1].name -eq "sample.mean") "c1 tool name"
    Assert-True ($items[1].call_id -eq "call-1") "c1 call id"
    Assert-True (($items[1].arguments | ConvertTo-Json -Compress -Depth 4) -eq ((New-Object PSObject -Property @{values=@(18,22)} | ConvertTo-Json -Compress -Depth 4).Replace(' ',''))) ("c1 arguments are " + ($items[1].arguments | ConvertTo-Json -Compress))
    Assert-True ($items[2].item_id -eq "r1" -and $items[2].kind -eq "tool_result") "r1 is the tool result"
    Assert-True ($items[2].call_id -eq "call-1") "r1 associated to call-1 by call_id"
    Assert-True ($items[2].result.mean -eq 20) "r1 result is mean 20"
    Assert-True ($items[3].item_id -eq "a1" -and $items[3].kind -eq "assistant") "a1 is the assistant reply"
    Assert-True ($items[3].done -eq $true) "a1 marked done"
    Assert-True ($items[3].text -eq "平均值是 20。") "a1 deltas concatenated once"
}

Invoke-Step "U3.2 import the same file again, dump must be identical" {
    $r2 = Get-Json "http://127.0.0.1:8731/import" ("{`"file`": `"" + ($eventsFile -replace '\\','/') + "`"}")
    ($r2 | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "import2.json")
    Assert-True ($r2.new_local -eq 0) "replayed import adds no new events"
    Wait-Converge $Case

    $dump2 = Get-Json "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01&format=json"
    ($dump2 | ConvertTo-Json -Depth 6) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-dump-after-replay.json")
    $items2 = $dump2.threads[0].turns[0].items
    Assert-True ($items2.Count -eq 4) "still exactly four logical records"
    $ids1 = ($items | ForEach-Object { $_.item_id }) -join ","
    $ids2 = ($items2 | ForEach-Object { $_.item_id }) -join ","
    Assert-True ($ids1 -eq $ids2) "order and set of items identical after replay"
    Assert-True ($items2[3].text -eq "平均值是 20。") "assistant text unchanged after replay"

    $sv = Get-Json "http://127.0.0.1:8730/v1/status"
    Assert-True ($sv.event_count -eq 6) "server holds exactly 6 unique events"
    Assert-True ($sv.event_dup_hits -ge 6) ("server absorbed " + $sv.event_dup_hits + " duplicate deliveries")
    $sv | ConvertTo-Json | Out-File -Encoding utf8 (Join-Path $Case.Dir "server-status.json")
}
