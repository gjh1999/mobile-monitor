# C1: same-baseline edit/edit conflict preserves both readable contents.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$projA = Join-Path $Case.Root "project-a"
$projB = Join-Path $Case.Root "project-b"

Invoke-Step "C1.1 establish shared baseline" {
    Set-Content -NoNewline (Join-Path $projA "shared.txt") -Value "base`n" -Encoding Ascii
    Wait-Converge $Case
    Assert-FileHash (Join-Path $projB "shared.txt") (Get-Sha (Join-Path $projA "shared.txt")) "B received baseline"
}

Invoke-Step "C1.2 both sides edit from the same baseline" {
    Set-Content -NoNewline (Join-Path $projA "shared.txt") -Value "A-edit`n" -Encoding Ascii
    Set-Content -NoNewline (Join-Path $projB "shared.txt") -Value "B-edit`n" -Encoding Ascii
    Wait-Converge $Case
    # Wait one extra round so each side's conflict file propagates back.
    Wait-Converge $Case
}

Invoke-Step "C1.3 both contents readable on both sides" {
    $aConf = Get-ChildItem (Join-Path $projA "shared.txt.conflict-from-B-*") | Select-Object -First 1
    $bConf = Get-ChildItem (Join-Path $projB "shared.txt.conflict-from-A-*") | Select-Object -First 1
    Assert-True ($null -ne $aConf) "A keeps a conflict file with B's content"
    Assert-True ($null -ne $bConf) "B keeps a conflict file with A's content"
    Assert-True ((Get-Content (Join-Path $projA "shared.txt") -Raw) -match "A-edit") "A original path keeps A's edit"
    Assert-True ((Get-Content $aConf.FullName -Raw) -match "B-edit") "A conflict file holds B's edit"
    Assert-True ((Get-Content (Join-Path $projB "shared.txt") -Raw) -match "B-edit") "B original path keeps B's edit"
    Assert-True ((Get-Content $bConf.FullName -Raw) -match "A-edit") "B conflict file holds A's edit"

    $ca = Get-Json "http://127.0.0.1:8731/conflicts"
    $cb = Get-Json "http://127.0.0.1:8732/conflicts"
    ($ca | ConvertTo-Json -Depth 4) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-conflicts.json")
    ($cb | ConvertTo-Json -Depth 4) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-conflicts.json")
    Assert-True ($ca.conflicts.Count -ge 1) "A reports the conflict record"
    Assert-True ($ca.conflicts[0].kind -eq "edit-edit") "conflict kind edit-edit"
}
