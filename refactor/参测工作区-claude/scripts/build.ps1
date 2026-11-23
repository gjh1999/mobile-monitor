param(
    [switch]$DebugBuild
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$go = Initialize-ToolchainEnv
$cargo = Get-CargoExe
$rustc = Get-RustcExe

New-Item -ItemType Directory -Force -Path $script:BinDir | Out-Null

Write-Host "== building coordinator (Go) =="
Push-Location (Join-Path $script:WorkspaceRoot 'server')
try {
    & $go build -o (Join-Path $script:BinDir 'server.exe') .
    if ($LASTEXITCODE -ne 0) { throw "go build failed with exit $LASTEXITCODE" }
} finally {
    Pop-Location
}

Write-Host "== building client core (Rust) =="
Push-Location (Join-Path $script:WorkspaceRoot 'client')
try {
    if ($DebugBuild) {
        & $cargo build
        $src = Join-Path (Join-Path (Get-Location) 'target\debug') 'sync-client.exe'
    } else {
        & $cargo build --release
        $src = Join-Path (Join-Path (Get-Location) 'target\release') 'sync-client.exe'
    }
    if ($LASTEXITCODE -ne 0) { throw "cargo build failed with exit $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath $src)) { throw "client binary not found at $src" }
    Copy-Item -LiteralPath $src -Destination (Join-Path $script:BinDir 'sync-client.exe') -Force
} finally {
    Pop-Location
}

Write-Host "== toolchain =="
& $go version
& $rustc --version
& $cargo --version
Write-Host "BUILD OK -> $script:BinDir"
