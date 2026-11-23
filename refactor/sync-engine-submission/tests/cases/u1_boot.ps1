# U1: boot from empty state, verify identities/paths, stop, restart on the
# same data dirs, verify all three roles work again.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Tee-Object -FilePath (Join-Path $Case.Dir "up.log")

$uplog = Get-Content (Join-Path $Case.Dir "up.log") -Raw
Assert-True ($uplog -match "server pid=") "server process reported with pid"
Assert-True ($uplog -match "client A pid=") "client A process reported with pid"
Assert-True ($uplog -match "client B pid=") "client B process reported with pid"

$sa = Get-Json "http://127.0.0.1:8731/status"
$sb = Get-Json "http://127.0.0.1:8732/status"
$sv = Get-Json "http://127.0.0.1:8730/v1/status"

Assert-True ($sa.client_id -eq "A") "client A identity is A"
Assert-True ($sb.client_id -eq "B") "client B identity is B"
Assert-True ($sa.project_dir.EndsWith("project-a")) "client A project dir is project-a"
Assert-True ($sb.project_dir.EndsWith("project-b")) "client B project dir is project-b"
Assert-True ($sv.version -eq 0) "server starts at version 0"
Assert-True ($sa.files -eq 0) "client A project is empty"
Assert-True ($sb.files -eq 0) "client B project is empty"

$snapshot = [ordered]@{
    server = @{ version = $sv.version; addr = "127.0.0.1:8730"; state = "$($Case.Root)\server-data" }
    clientA = @{ id = $sa.client_id; addr = "127.0.0.1:8731"; project = $sa.project_dir; state = "$($Case.Root)\a-state" }
    clientB = @{ id = $sb.client_id; addr = "127.0.0.1:8732"; project = $sb.project_dir; state = "$($Case.Root)\b-state" }
}
$snapshot | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $Case.Dir "identities.json")

& $Script:SYNCCTL down --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "down.log")
Start-Sleep -Milliseconds 500

# Restart on the same paths (no --fresh: state dirs persist).
& $Script:SYNCCTL up --root $Case.Root 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "restart.log")
$sa2 = Get-Json "http://127.0.0.1:8731/status"
$sb2 = Get-Json "http://127.0.0.1:8732/status"
$sv2 = Get-Json "http://127.0.0.1:8730/v1/status"
Assert-True ($sa2.client_id -eq "A") "after restart client A works again"
Assert-True ($sb2.client_id -eq "B") "after restart client B works again"
Assert-True ($sv2.version -ge 0) "after restart server works again"
