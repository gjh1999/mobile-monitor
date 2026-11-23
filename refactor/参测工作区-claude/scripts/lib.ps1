$script:WorkspaceRoot = Split-Path -Parent $PSScriptRoot
$script:BinDir = Join-Path $script:WorkspaceRoot 'bin'

function Get-WorkspaceRoot { return $script:WorkspaceRoot }
function Get-BinDir { return $script:BinDir }

function Initialize-ToolchainEnv {
    $go = Get-Command go -ErrorAction SilentlyContinue
    if ($go) { return $go.Source }
    $tc = Join-Path $script:WorkspaceRoot '_toolchain'
    $candidate = Join-Path (Join-Path $tc 'go\bin') 'go.exe'
    if (Test-Path -LiteralPath $candidate) {
        $env:GOROOT = Join-Path $tc 'go'
        if (-not $env:GOCACHE) { $env:GOCACHE = Join-Path $tc 'gocache' }
        if (-not $env:GOPATH) { $env:GOPATH = Join-Path $tc 'gopath' }
        $env:PATH = (Join-Path $tc 'go\bin') + ';' + $env:PATH
        return $candidate
    }
    throw 'Go toolchain not found. Install Go 1.22+ on PATH or place it at _toolchain\go.'
}

function Get-CargoExe {
    $cargo = Get-Command cargo -ErrorAction SilentlyContinue
    if ($cargo) { return $cargo.Source }
    $candidate = Join-Path $env:USERPROFILE '.cargo\bin\cargo.exe'
    if (Test-Path -LiteralPath $candidate) {
        $env:PATH = (Split-Path -Parent $candidate) + ';' + $env:PATH
        return $candidate
    }
    throw 'cargo not found. Install Rust 1.80+ or add cargo to PATH.'
}

function Get-RustcExe {
    $rustc = Get-Command rustc -ErrorAction SilentlyContinue
    if ($rustc) { return $rustc.Source }
    $candidate = Join-Path $env:USERPROFILE '.cargo\bin\rustc.exe'
    if (Test-Path -LiteralPath $candidate) {
        $env:PATH = (Split-Path -Parent $candidate) + ';' + $env:PATH
        return $candidate
    }
    throw 'rustc not found. Install Rust 1.80+.'
}

function Wait-HttpOk {
    param([string]$Uri, [int]$TimeoutSec = 20)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-RestMethod -Uri $Uri -TimeoutSec 1
            return $true
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    return $false
}

function Invoke-JsonApi {
    param([string]$Uri, [string]$Method = 'GET', $Body = $null, [int]$TimeoutSec = 60)
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $Uri -Method $Method -TimeoutSec $TimeoutSec
    }
    $json = $Body | ConvertTo-Json -Depth 20 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri $Uri -Method $Method -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec $TimeoutSec
}

function Wait-Condition {
    param([scriptblock]$Condition, [int]$TimeoutSec = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Get-FileSha256 {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return '' }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Stop-ProcessForce {
    param([int]$ProcessId)
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($p) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
        try { Wait-Process -Id $ProcessId -Timeout 5 -ErrorAction Stop } catch { }
    }
}

function Get-FreeTcpPort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

function Resolve-RunPath {
    param([string]$RunRoot)
    $root = $script:WorkspaceRoot
    if ([IO.Path]::IsPathRooted($RunRoot)) {
        $runPath = $RunRoot
    } else {
        $runPath = Join-Path $root $RunRoot
    }
    $runPath = [IO.Path]::GetFullPath($runPath)
    $runBase = [IO.Path]::GetFullPath((Join-Path $root 'run'))
    if (-not $runPath.StartsWith($runBase, [StringComparison]::OrdinalIgnoreCase)) {
        throw "RunRoot must be inside $runBase (got $runPath)"
    }
    return $runPath
}

function Quote-Arg {
    param([string]$Value)
    return '"' + $Value + '"'
}
