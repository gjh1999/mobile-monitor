param([ValidateSet('drop','crash')][string]$Kind = 'drop')
. "$PSScriptRoot\lib.ps1"
try {
    $r = Invoke-WebRequest -Uri "$ServerUrl/fault?kind=$Kind" -TimeoutSec 5 -UseBasicParsing
    Write-Host "armed: $($r.Content)"
} catch {
    Write-Host "fault arm failed: $($_.Exception.Message)"
}
