# Stop all three processes (coordinator, A, B). Their data dirs survive
# for restart with the same paths.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1 [-RunRoot <dir>]
param([string]$RunRoot = "run")
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
& (Join-Path $root "bin\syncctl.exe") down --root $RunRoot
exit $LASTEXITCODE
