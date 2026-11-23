param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$go = Initialize-ToolchainEnv
$cargo = Get-CargoExe

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'build failed' }
}

Write-Host ''
Write-Host '== Go unit tests =='
Push-Location (Join-Path $script:WorkspaceRoot 'server')
try {
    & $go test ./... -v
    $goExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($goExit -ne 0) { Write-Host 'GO TESTS FAILED'; exit 1 }

Write-Host ''
Write-Host '== Rust unit tests =='
Push-Location (Join-Path $script:WorkspaceRoot 'client')
try {
    & $cargo test
    $cargoExit = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($cargoExit -ne 0) { Write-Host 'RUST TESTS FAILED'; exit 1 }

Write-Host ''
Write-Host '== Public acceptance U1-U3 (live processes) =='
& (Join-Path $PSScriptRoot 'e2e-public.ps1')
$e2eExit = $LASTEXITCODE
if ($e2eExit -ne 0) { Write-Host 'PUBLIC E2E FAILED'; exit 1 }

Write-Host ''
Write-Host 'ALL TEST SUITES PASSED'
