param([string[]]$Ids = @('A','B'))
. "$PSScriptRoot\lib.ps1"
foreach ($id in $Ids) {
    Write-Host "==== $id ===="
    Invoke-Client $id @('status')
}
