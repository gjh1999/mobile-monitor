param([string[]]$Ids = @('A','B'))
. "$PSScriptRoot\lib.ps1"
foreach ($id in $Ids) {
    Write-Host "==== sync $id ===="
    Trigger-Sync $id
}
