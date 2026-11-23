# C2: remote delete vs local edit -> local content stays readable, delete
# intent presented as a tombstone. Also covers the mirror case: local
# delete intent vs remote edit -> remote edit restored at original path,
# local delete intent presented as a tombstone.
param([hashtable]$Case)

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")
$projA = Join-Path $Case.Root "project-a"
$projB = Join-Path $Case.Root "project-b"

Invoke-Step "C2.1 baseline on both sides" {
    Set-Content -NoNewline (Join-Path $projA "doc.txt") -Value "v1`n" -Encoding Ascii
    Wait-Converge $Case
    Assert-True (Test-Path (Join-Path $projB "doc.txt")) "B received doc.txt"
}

Invoke-Step "C2.2 B deletes while A edits" {
    Remove-Item (Join-Path $projB "doc.txt")
    Set-Content -NoNewline (Join-Path $projA "doc.txt") -Value "A-modified-long`n" -Encoding Ascii
    Wait-Converge $Case
    Wait-Converge $Case
}

Invoke-Step "C2.3 delete intent and readable modification coexist on A" {
    $aTomb = Get-ChildItem (Join-Path $projA "doc.txt.deleted-by-B-*.json") | Select-Object -First 1
    Assert-True (Test-Path (Join-Path $projA "doc.txt")) "A original path still holds the readable modification"
    Assert-True ((Get-Content (Join-Path $projA "doc.txt") -Raw) -match "A-modified-long") "A modification content readable"
    Assert-True ($null -ne $aTomb) "A shows a tombstone presenting the delete intent"
    $tomb = (Get-Content $aTomb.FullName -Raw | ConvertFrom-Json)
    Assert-True ($tomb.deleted_by -eq "B") "tombstone names the deleting client"
    ($tomb | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-tombstone.json")

    $cb = Get-Json "http://127.0.0.1:8731/conflicts"
    Assert-True ($cb.conflicts | Where-Object { $_.kind -eq "delete-edit" }) "A conflict record kind delete-edit"
}

Invoke-Step "C2.4 local delete intent vs remote edit (mirror case)" {
    # Reset to a known state on a fresh path: keep.txt exists everywhere.
    Set-Content -NoNewline (Join-Path $projA "keep.txt") -Value "keep-v1`n" -Encoding Ascii
    Wait-Converge $Case
    # A deletes locally (pending, then confirmed push), then B edits the file
    # from the pre-delete baseline. B's edit commit lands after A's delete.
    Remove-Item (Join-Path $projA "keep.txt")
    & $Script:SYNCCTL sync a 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "sync-a-delete.log") -Append
    Set-Content -NoNewline (Join-Path $projB "keep.txt") -Value "B-edit-after-delete`n" -Encoding Ascii
    Wait-Converge $Case
    Wait-Converge $Case

    Assert-True (Test-Path (Join-Path $projA "keep.txt")) "A restored the readable remote edit at the original path"
    Assert-True ((Get-Content (Join-Path $projA "keep.txt") -Raw) -match "B-edit-after-delete") "A restored content matches B's edit"
    $aTomb = Get-ChildItem (Join-Path $projA "keep.txt.deleted-here-*.json") | Select-Object -First 1
    Assert-True ($null -ne $aTomb) "A tombstone presents the local delete intent"
    Assert-True (Test-Path (Join-Path $projB "keep.txt")) "B keeps its edit"
}
