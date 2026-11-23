# One command from a clean workspace: build if needed, then start
# coordinator + client A + client B from an empty state.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 [-Fresh] [-RunRoot <dir>]
param(
    [switch]$Fresh,
    [string]$RunRoot = "run"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $root

$needBuild = -not (Test-Path (Join-Path $root "bin\server.exe")) -or
             -not (Test-Path (Join-Path $root "bin\syncctl.exe")) -or
             -not (Test-Path (Join-Path $root "bin\syncclient.exe"))
if ($needBuild) {
    Write-Host "binaries missing -> building first"
    & (Join-Path $root "scripts\build.ps1")
    if ($LASTEXITCODE -ne 0) { Write-Error "build failed" }
}

$syncctl = Join-Path $root "bin\syncctl.exe"
$args = @("up", "--root", $RunRoot)
if ($Fresh) { $args += "--fresh" }
& $syncctl @args
exit $LASTEXITCODE
