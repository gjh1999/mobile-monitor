param(
    [string]$OutputDir = 'dist'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$root = $script:WorkspaceRoot
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = Join-Path $root $OutputDir
$stage = Join-Path $outDir ("stage-" + $stamp)
$zip = Join-Path $outDir ("sync-engine-source-" + $stamp + ".zip")
New-Item -ItemType Directory -Force -Path $stage | Out-Null

try {
    # Root files (task materials, README, DESIGN, VALIDATION, .gitignore, ...).
    Get-ChildItem -LiteralPath $root -File -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $stage $_.Name) -Force
    }

    # Sample project directory discovered by content, avoids hard-coded non-ASCII names.
    $sampleDir = Get-ChildItem -LiteralPath $root -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'summary.py') } |
        Select-Object -First 1
    if ($null -eq $sampleDir) { throw 'sample project not found' }
    Copy-Item -LiteralPath $sampleDir.FullName -Destination (Join-Path $stage $sampleDir.Name) -Recurse -Force

    Copy-Item -LiteralPath (Join-Path $root 'server') -Destination (Join-Path $stage 'server') -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $root 'scripts') -Destination (Join-Path $stage 'scripts') -Recurse -Force

    $clientStage = Join-Path $stage 'client'
    New-Item -ItemType Directory -Force -Path $clientStage | Out-Null
    foreach ($f in @('Cargo.toml', 'Cargo.lock')) {
        Copy-Item -LiteralPath (Join-Path $root ('client\' + $f)) -Destination (Join-Path $clientStage $f) -Force
    }
    Copy-Item -LiteralPath (Join-Path $root 'client\src') -Destination (Join-Path $clientStage 'src') -Recurse -Force

    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip -Force
    $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
    $info = [ordered]@{
        created_at = (Get-Date).ToString('o')
        zip = $zip
        sha256 = $hash
        bytes = (Get-Item -LiteralPath $zip).Length
        excludes = @('bin', 'run', 'client\target', '_toolchain', 'dist', 'reviewer-only inputs')
    }
    $info | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $outDir 'last-package.json')
    Write-Host ("SOURCE PACKAGE: " + $zip)
    Write-Host ("SHA-256: " + $hash)
    Write-Host ("bytes: " + $info.bytes)
} finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
