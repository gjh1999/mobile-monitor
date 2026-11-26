# 共享定义。用法：. "$PSScriptRoot\lib.ps1"
$WS        = Split-Path -Parent $PSScriptRoot          # 01-同步引擎 根目录
$Bin       = Join-Path $WS 'bin'
$Run       = Join-Path $WS 'run'
$ServerExe = Join-Path $Bin 'server.exe'
$ClientExe = Join-Path $Bin 'sync-client.exe'
$Port      = 8787
$ServerUrl = "http://127.0.0.1:$Port"

function Get-GoExe {
    if ($env:GO -and (Test-Path $env:GO)) { return $env:GO }
    $g = Get-Command go -ErrorAction SilentlyContinue
    if ($g) { return $g.Source }
    $vend = Join-Path $WS '..\_toolchain\go\bin\go.exe'
    if (Test-Path $vend) { return (Resolve-Path $vend).Path }
    throw 'Go 1.27+ 未找到：请安装 Go 或设置 $env:GO'
}

function Get-CargoExe {
    if ($env:CARGO -and (Test-Path $env:CARGO)) { return $env:CARGO }
    $c = Get-Command cargo -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    throw 'Rust cargo 未找到：请安装 Rust 1.100+ 或设置 $env:CARGO'
}

function Invoke-Build {
    New-Item -ItemType Directory -Force $Bin | Out-Null
    $go = Get-GoExe
    $tool = Split-Path -Parent (Split-Path -Parent $go)   # go/bin 的上一级（若为 go 则其父为工具链根）
    $prevCache = $env:GOCACHE; $prevPath = $env:GOPATH; $prevTmp = $env:GOTMPDIR
    if ($tool -and (Split-Path -Leaf $tool) -eq 'go') {
        $root = Split-Path -Parent $tool
        $env:GOCACHE = Join-Path $root 'gocache'
        $env:GOPATH  = Join-Path $root 'gopath'
        $env:GOTMPDIR = Join-Path $root 'tmp'
        New-Item -ItemType Directory -Force $env:GOCACHE, $env:GOPATH, $env:GOTMPDIR | Out-Null
    }
    Push-Location (Join-Path $WS 'server')
    & $go build -o $ServerExe .
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'Go 服务端构建失败' }
    Pop-Location

    $cargo = Get-CargoExe
    Push-Location (Join-Path $WS 'client')
    $env:CARGO_TARGET_DIR = Join-Path $WS 'client\target'
    & $cargo build --release --offline
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw 'Rust 客户端构建失败' }
    Pop-Location

    $env:GOCACHE = $prevCache; $env:GOPATH = $prevPath; $env:GOTMPDIR = $prevTmp
    Copy-Item (Join-Path $WS 'client\target\release\sync-client.exe') $ClientExe -Force
    Write-Host 'build ok:' $ServerExe $ClientExe
}

function Start-Detached {
    param([string]$Exe, [string[]]$Args, [string]$WorkDir)
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    foreach ($a in $Args) { [void]$psi.ArgumentList.Add($a) }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    return $p
}

function Start-SyncServer {
    New-Item -ItemType Directory -Force $Run, (Join-Path $Run 'logs') | Out-Null
    $log = Join-Path $Run 'logs\server.log'
    $p = Start-Detached -Exe $ServerExe -Args @('-dir', $WS, '-port', "$Port", '-log', $log) -WorkDir $WS
    Set-Content (Join-Path $Run 'server.pid') $p.Id
    $p.Id
}

function Start-SyncClient {
    param([string]$Id)
    $proj  = Join-Path $Run "project-$Id"
    $state = Join-Path $Run "state-$Id"
    New-Item -ItemType Directory -Force $proj, $state, (Join-Path $Run 'logs') | Out-Null
    $p = Start-Detached -Exe $ClientExe -Args @($Id, $proj, $state, $ServerUrl, 'daemon') -WorkDir $WS
    Set-Content (Join-Path $Run "client-$Id.pid") $p.Id
    $p.Id
}

function Stop-PidFile {
    param([string]$PidFile)
    if (Test-Path $PidFile) {
        $id = (Get-Content $PidFile).Trim()
        if ($id) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
}

function Wait-ForServer {
    param([int]$TimeoutSec = 10)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "$ServerUrl/health" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Get-ClientArgs {
    param([string]$Id)
    $proj  = Join-Path $Run "project-$Id"
    $state = Join-Path $Run "state-$Id"
    return @($Id, $proj, $state, $ServerUrl)
}

function Invoke-Client {
    param([string]$Id, [string[]]$Rest)
    $args = @(Get-ClientArgs $Id) + $Rest
    & $ClientExe @args
}

function Trigger-Sync {
    param([string]$Id, [int]$TimeoutSec = 30)
    $state = Join-Path $Run "state-$Id"
    New-Item -ItemType Directory -Force $state | Out-Null
    $trig = Join-Path $state 'trigger'
    Set-Content $trig 'sync'
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path $trig)) {
            $status = Join-Path $state 'status.txt'
            if (Test-Path $status) { Get-Content $status }
            return
        }
        Start-Sleep -Milliseconds 250
    }
    Write-Host "sync trigger timed out for $Id"
}
