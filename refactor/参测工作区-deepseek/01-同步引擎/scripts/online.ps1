param([Parameter(Mandatory=$true)][string]$Id, [Parameter(Mandatory=$true)][int]$On)
. "$PSScriptRoot\lib.ps1"
if ($On -eq 1) { Invoke-Client $Id @('online') } else { Invoke-Client $Id @('offline') }
