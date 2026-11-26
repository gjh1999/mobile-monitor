param(
    [Parameter(Mandatory=$true)][string]$File,
    [string]$Id = 'A',
    [int]$Start = 0,
    [int]$End = 0
)
. "$PSScriptRoot\lib.ps1"
if ($Start -gt 0 -and $End -gt 0) {
    Invoke-Client $Id @('import-events', $File, "$Start", "$End")
} else {
    Invoke-Client $Id @('import-events', $File)
}
