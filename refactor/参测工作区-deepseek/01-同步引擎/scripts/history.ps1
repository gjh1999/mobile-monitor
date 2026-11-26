param([string]$Id = 'B')
. "$PSScriptRoot\lib.ps1"
Invoke-Client $Id @('history')
