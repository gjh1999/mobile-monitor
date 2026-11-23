param(
    [ValidateSet(
        'health', 'status', 'pending', 'conflicts', 'sync', 'pause', 'resume', 'retry',
        'history', 'replay', 'threads', 'events', 'import',
        'fault-arm', 'fault-disarm',
        'server-health', 'server-status', 'oplog', 'server-conflicts',
        'shutdown-client', 'shutdown-server'
    )]
    [string]$Action = 'status',
    [int]$Port = 7802,
    [string]$Server = 'http://127.0.0.1:7801',
    [string]$ThreadId = '',
    [string]$TurnId = '',
    [string]$File = '',
    [int]$StartLine = 0,
    [int]$EndLine = 0,
    [string]$PathFilter = '',
    [int]$Limit = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib.ps1')

$clientUrl = "http://127.0.0.1:$Port"
$serverUrl = $Server.TrimEnd('/')

try {
    $result = $null
    if ($Action -eq 'health') {
        $result = Invoke-JsonApi -Uri "$clientUrl/healthz"
    } elseif ($Action -eq 'status') {
        $result = Invoke-JsonApi -Uri "$clientUrl/status"
    } elseif ($Action -eq 'pending') {
        $result = Invoke-JsonApi -Uri "$clientUrl/pending"
    } elseif ($Action -eq 'conflicts') {
        $result = Invoke-JsonApi -Uri "$clientUrl/conflicts"
    } elseif ($Action -eq 'sync') {
        $result = Invoke-JsonApi -Uri "$clientUrl/sync" -Method 'POST' -Body @{}
    } elseif ($Action -eq 'pause') {
        $result = Invoke-JsonApi -Uri "$clientUrl/pause" -Method 'POST' -Body @{}
    } elseif ($Action -eq 'resume') {
        $result = Invoke-JsonApi -Uri "$clientUrl/resume" -Method 'POST' -Body @{}
    } elseif ($Action -eq 'retry') {
        $result = Invoke-JsonApi -Uri "$clientUrl/retry" -Method 'POST' -Body @{}
    } elseif ($Action -eq 'history' -or $Action -eq 'replay') {
        if ([string]::IsNullOrWhiteSpace($ThreadId)) { throw "-ThreadId is required for $Action" }
        $url = "$clientUrl/$Action?thread_id=" + [Uri]::EscapeDataString($ThreadId)
        if (-not [string]::IsNullOrWhiteSpace($TurnId)) {
            $url = $url + '&turn_id=' + [Uri]::EscapeDataString($TurnId)
        }
        $result = Invoke-JsonApi -Uri $url
    } elseif ($Action -eq 'threads') {
        $result = Invoke-JsonApi -Uri "$clientUrl/threads"
    } elseif ($Action -eq 'events') {
        $url = "$clientUrl/events"
        if (-not [string]::IsNullOrWhiteSpace($ThreadId)) {
            $url = $url + '?thread_id=' + [Uri]::EscapeDataString($ThreadId)
        }
        $result = Invoke-JsonApi -Uri $url
    } elseif ($Action -eq 'import') {
        if ([string]::IsNullOrWhiteSpace($File)) { throw "-File is required for import" }
        $body = @{ file = [IO.Path]::GetFullPath($File) }
        if ($StartLine -gt 0) { $body['start_line'] = $StartLine }
        if ($EndLine -gt 0) { $body['end_line'] = $EndLine }
        $result = Invoke-JsonApi -Uri "$clientUrl/import" -Method 'POST' -Body $body
    } elseif ($Action -eq 'fault-arm') {
        $result = Invoke-JsonApi -Uri "$serverUrl/api/admin/fault/drop-next-commit-ack" -Method 'POST' -Body @{ enabled = $true }
    } elseif ($Action -eq 'fault-disarm') {
        $result = Invoke-JsonApi -Uri "$serverUrl/api/admin/fault/drop-next-commit-ack" -Method 'POST' -Body @{ enabled = $false }
    } elseif ($Action -eq 'server-health') {
        $result = Invoke-JsonApi -Uri "$serverUrl/healthz"
    } elseif ($Action -eq 'server-status') {
        $result = Invoke-JsonApi -Uri "$serverUrl/api/status"
    } elseif ($Action -eq 'oplog') {
        $url = "$serverUrl/api/admin/oplog"
        $parts = @()
        if (-not [string]::IsNullOrWhiteSpace($PathFilter)) {
            $parts += 'path=' + [Uri]::EscapeDataString($PathFilter)
        }
        if ($Limit -gt 0) { $parts += 'limit=' + $Limit }
        if ($parts.Count -gt 0) { $url = $url + '?' + ($parts -join '&') }
        $result = Invoke-JsonApi -Uri $url
    } elseif ($Action -eq 'server-conflicts') {
        $result = Invoke-JsonApi -Uri "$serverUrl/api/admin/conflicts"
    } elseif ($Action -eq 'shutdown-client') {
        $result = Invoke-JsonApi -Uri "$clientUrl/shutdown" -Method 'POST' -Body @{}
    } elseif ($Action -eq 'shutdown-server') {
        $result = Invoke-JsonApi -Uri "$serverUrl/api/admin/shutdown" -Method 'POST' -Body @{}
    }
    $result | ConvertTo-Json -Depth 30
} catch {
    Write-Error ("ctl failed: " + $_.Exception.Message)
    exit 1
}
