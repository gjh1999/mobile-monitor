# U2: file round trip with byte-exact hashes per the public test spec.
param([hashtable]$Case)

$projA = Join-Path $Case.Root "project-a"
$projB = Join-Path $Case.Root "project-b"

& $Script:SYNCCTL up --root $Case.Root --fresh 2>&1 | Out-File -FilePath (Join-Path $Case.Dir "up.log")

Invoke-Step "U2.1 copy sample project to A, compare bytes on B" {
    $src = Join-Path $Script:SUBMISSION "示例项目"
    New-Item -ItemType Directory -Force -Path (Join-Path $projA "data") | Out-Null
    Copy-Item (Join-Path $src "summary.py") $projA
    Copy-Item (Join-Path $src "data\readings.txt") (Join-Path $projA "data\readings.txt")
    Wait-Converge $Case

    $bRead = Join-Path $projB "data\readings.txt"
    $bSum = Join-Path $projB "summary.py"
    Assert-True (Test-Path $bRead) "B received data/readings.txt"
    Assert-True (Test-Path $bSum) "B received summary.py"
    Assert-FileHash $bRead "748a88ea78f384b2d39609fcc0c327f871e52a203b4b93066595aaf158a6ada3" "B readings.txt hash matches initial sample"
    Assert-FileHash $bSum (Get-Sha (Join-Path $src "summary.py")) "B summary.py identical to original"
    ($bRead + " sha256=" + (Get-Sha $bRead)) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-hash-initial.txt")
}

Invoke-Step "U2.2 append 25 on A, verify new hash on B" {
    $aRead = Join-Path $projA "data\readings.txt"
    [IO.File]::AppendAllText($aRead, "25`n")
    Assert-FileHash $aRead "91060fb970dfca0cfcd6a2d582a6139378ea385e8b6c5268a59a970cb9fd78f3" "A readings.txt hash after append"
    Wait-Converge $Case
    Assert-FileHash (Join-Path $projB "data\readings.txt") "91060fb970dfca0cfcd6a2d582a6139378ea385e8b6c5268a59a970cb9fd78f3" "B readings.txt hash after append"
    ("sha256=" + (Get-Sha (Join-Path $projB "data\readings.txt"))) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-hash-after-append.txt")
}

Invoke-Step "U2.3 rename on B, then delete from A" {
    Move-Item (Join-Path $projB "data\readings.txt") (Join-Path $projB "data\week.txt")
    Wait-Converge $Case
    Assert-True (-not (Test-Path (Join-Path $projA "data\readings.txt"))) "A no longer has readings.txt after rename"
    Assert-True (Test-Path (Join-Path $projA "data\week.txt")) "A sees week.txt after rename"

    Remove-Item (Join-Path $projA "data\week.txt")
    Wait-Converge $Case

    Assert-True (-not (Test-Path (Join-Path $projA "data\week.txt"))) "A has neither path (week.txt deleted)"
    Assert-True (-not (Test-Path (Join-Path $projA "data\readings.txt"))) "A has neither path (readings.txt)"
    Assert-True (-not (Test-Path (Join-Path $projB "data\week.txt"))) "B has neither path (week.txt deleted)"
    Assert-True (-not (Test-Path (Join-Path $projB "data\readings.txt"))) "B has neither path (readings.txt)"
    Assert-FileHash (Join-Path $projB "summary.py") (Get-Sha (Join-Path $Script:SUBMISSION "示例项目\summary.py")) "summary.py unchanged throughout"
    (Get-ClientFileList 8731 | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "a-final-files.json")
    (Get-ClientFileList 8732 | ConvertTo-Json) | Out-File -Encoding utf8 (Join-Path $Case.Dir "b-final-files.json")
}
