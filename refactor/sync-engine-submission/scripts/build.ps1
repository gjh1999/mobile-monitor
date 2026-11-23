# Build all three binaries into bin\. Go toolchain is bundled under
# .toolchain\go; Rust must be installed (see README for versions).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $root

# --- Go (bundled, offline; the server uses stdlib only) ---
$go = Join-Path $root ".toolchain\go\bin\go.exe"
if (-not (Test-Path $go)) {
    Write-Error "bundled Go not found at $go - see README section 2"
}
$env:GOPROXY = "off"
$env:GOFLAGS = "-mod=mod"
New-Item -ItemType Directory -Force -Path (Join-Path $root "bin") | Out-Null
& $go build -o (Join-Path $root "bin\server.exe")  ./cmd/server
if ($LASTEXITCODE -ne 0) { Write-Error "go build server failed" }
& $go build -o (Join-Path $root "bin\syncctl.exe") ./cmd/syncctl
if ($LASTEXITCODE -ne 0) { Write-Error "go build syncctl failed" }
Write-Host "go build: bin\server.exe, bin\syncctl.exe"

# --- Rust client ---
$cargo = "cargo"
$maybe = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
if (Test-Path $maybe) { $cargo = $maybe }
Push-Location (Join-Path $root "client")
try {
    # The three dependencies (serde, serde_json, sha2) are vendored in the
    # local cargo registry cache; --offline keeps the build reproducible.
    & $cargo build --offline
    if ($LASTEXITCODE -ne 0) { & $cargo build; if ($LASTEXITCODE -ne 0) { Write-Error "cargo build failed" } }
} finally { Pop-Location }
Copy-Item (Join-Path $root "client\target\debug\syncclient.exe") (Join-Path $root "bin\") -Force
Write-Host "cargo build: bin\syncclient.exe"

Write-Host "BUILD OK"
