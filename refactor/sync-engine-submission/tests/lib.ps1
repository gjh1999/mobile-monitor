# Shared helpers for the acceptance test cases. ASCII-only output so that
# Windows PowerShell 5.1 renders it identically regardless of codepage.
$ErrorActionPreference = "Stop"

$Script:SUBMISSION = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Script:BIN = Join-Path $Script:SUBMISSION "bin"
$Script:SYNCCTL = Join-Path $Script:BIN "syncctl.exe"
$Script:EVIDENCE = Join-Path $Script:SUBMISSION "运行结果"

function New-Case {
    param([string]$Name)
    $root = Join-Path $Script:SUBMISSION ("run\test-" + $Name)
    if (Test-Path $root) { & $Script:SYNCCTL down --root $root 2>$null | Out-Null }
    if (Test-Path $root) { Start-Sleep -Milliseconds 800; Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue }
    $dir = Join-Path $Script:EVIDENCE $Name
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return @{ Root = $root; Name = $Name; Dir = $dir }
}

function Invoke-Step {
    param([string]$Title, [scriptblock]$Action)
    Write-Host ""
    Write-Host ("== " + $Title)
    # Dot-source so variable assignments inside the step stay visible to
    # later steps in the same case (PS scriptblock & creates a child scope).
    . $Action
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        Write-Host ("  PASS " + $Message)
    } else {
        Write-Host ("  FAIL " + $Message)
        throw ("assertion failed: " + $Message)
    }
}

function Get-Sha {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLower()
}

function Get-Json {
    param([string]$Url, [string]$Body = $null)
    # PS 5.1 mangles non-ASCII twice: string request bodies are sent as
    # ISO-8859-1, and responses without an explicit charset are decoded as
    # ISO-8859-1 as well. Send UTF-8 bytes and decode the raw stream as
    # UTF-8 explicitly on both directions.
    if ($Body) {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Body)
        $resp = Invoke-WebRequest -UseBasicParsing -Method POST -ContentType "application/json; charset=utf-8" -Body $bytes -Uri $Url -TimeoutSec 30
    } else {
        $resp = Invoke-WebRequest -UseBasicParsing -Method GET -Uri $Url -TimeoutSec 30
    }
    $text = [Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
    return ($text | ConvertFrom-Json)
}

function Wait-Converge {
    param([hashtable]$Case, [int]$Timeout = 30)
    $out = & $Script:SYNCCTL converge --root $Case.Root --timeout $Timeout 2>&1
    ($out -join "`n") | Out-File -Encoding utf8 (Join-Path $Case.Dir "converge.log") -Append
    $ok = ($out -match "converged") -and -not ($out -match "error:")
    if (-not $ok) { throw ("converge did not converge: " + ($out -join " | ")) }
}

function Assert-FileHash {
    param([string]$Path, [string]$Expected, [string]$Message)
    $actual = Get-Sha $Path
    Assert-True ($actual -eq $Expected) ($Message + " (sha256 " + $actual.Substring(0,16) + "...)")
}

function Get-ClientFileList {
    param([string]$Port)
    $j = Get-Json ("http://127.0.0.1:" + $Port + "/files")
    return $j.files
}
