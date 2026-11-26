# 三题联测 · 题1 公开用例（U1/U2/U3）一键回归
# 用法：powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1
param([int]$TimeoutSec = 30)
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib.ps1"

$failures = New-Object System.Collections.ArrayList
$checks   = 0

function Check {
    param([string]$Name, [bool]$Ok, [string]$Detail = '')
    $script:checks++
    $mark = if ($Ok) { 'PASS' } else { 'FAIL' }
    Write-Host ("[{0}] {1}  {2}" -f $mark, $Name, $Detail)
    if (-not $Ok) { [void]$failures.Add("$Name : $Detail") }
}

function Sha([string]$Path) {
    if (Test-Path $Path) { return (Get-FileHash $Path -Algorithm SHA256).Hash.ToLower() }
    return '<missing>'
}

function Wait-For {
    param([scriptblock]$Cond, [string]$Desc, [int]$Timeout = $TimeoutSec)
    $deadline = (Get-Date).AddSeconds($Timeout)
    while ((Get-Date) -lt $deadline) {
        try { if (& $Cond) { return $true } } catch {}
        Start-Sleep -Milliseconds 300
    }
    Check "wait: $Desc" $false '超时'
    return $false
}

# ---- 清空并启动 ----
& "$PSScriptRoot\stop-all.ps1" | Out-Null
if (Test-Path $Run) { Remove-Item -Recurse -Force $Run }
& "$PSScriptRoot\start-all.ps1" | Out-Null
Start-Sleep -Seconds 1

# ---- U1 从空状态启动 ----
$up = Wait-For { (Invoke-WebRequest -Uri "$ServerUrl/health" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 } 'server up' 15
Check 'U1 server 健康' $up
$pidServer = (Get-Content (Join-Path $Run 'server.pid')).Trim()
$pidA = (Get-Content (Join-Path $Run 'client-A.pid')).Trim()
$pidB = (Get-Content (Join-Path $Run 'client-B.pid')).Trim()
$allAlive = ($pidServer -and $pidA -and $pidB) -and
    [bool](Get-Process -Id $pidServer -ErrorAction SilentlyContinue) -and
    [bool](Get-Process -Id $pidA -ErrorAction SilentlyContinue) -and
    [bool](Get-Process -Id $pidB -ErrorAction SilentlyContinue)
Check 'U1 三个进程存活' $allAlive "$pidServer/$pidA/$pidB"

# U1 重启
& "$PSScriptRoot\stop-all.ps1" | Out-Null
Start-Sleep -Milliseconds 500
& "$PSScriptRoot\start-all.ps1" | Out-Null
Start-Sleep -Seconds 1
$up2 = Wait-For { (Invoke-WebRequest -Uri "$ServerUrl/health" -TimeoutSec 2 -UseBasicParsing).StatusCode -eq 200 } 'server up after restart' 15
Check 'U1 停止后原路径重启' $up2

# ---- U2 文件往返 ----
$exa = "$WS\示例项目"
Copy-Item "$exa\summary.py" "$Run\project-A\"
Copy-Item -Recurse -Force "$exa\data" "$Run\project-A\"
Wait-For { (Test-Path "$Run\project-B\data\readings.txt") } 'B 收到初始副本' | Out-Null
Start-Sleep -Milliseconds 500

$h1 = Sha "$Run\project-B\data\readings.txt"
Check 'U2 初始副本字节匹配' ($h1 -eq '748a88ea78f384b2d39609fcc0c327f871e52a203b4b93066595aaf158a6ada3') "B=$h1"

[IO.File]::AppendAllText((Join-Path $Run 'project-A\data\readings.txt'), "25`n")
Wait-For { (Sha "$Run\project-B\data\readings.txt") -eq '91060fb970dfca0cfcd6a2d582a6139378ea385e8b6c5268a59a970cb9fd78f3' } 'B 收到追加' | Out-Null
$h2 = Sha "$Run\project-B\data\readings.txt"
Check 'U2 A 追加后 B 字节匹配' ($h2 -eq '91060fb970dfca0cfcd6a2d582a6139378ea385e8b6c5268a59a970cb9fd78f3') "B=$h2"

Move-Item (Join-Path $Run 'project-B\data\readings.txt') (Join-Path $Run 'project-B\data\week.txt') -Force
Wait-For { (Test-Path "$Run\project-A\data\week.txt") -and -not (Test-Path "$Run\project-A\data\readings.txt") } 'A 看到改名' | Out-Null
Check 'U2 B 改名后 A 路径一致' ((Test-Path "$Run\project-A\data\week.txt") -and -not (Test-Path "$Run\project-A\data\readings.txt"))

Remove-Item (Join-Path $Run 'project-A\data\week.txt') -Force
Wait-For { -not (Test-Path "$Run\project-B\data\week.txt") } 'B 看到删除' | Out-Null
$bothGone = (-not (Test-Path "$Run\project-A\data\week.txt")) -and (-not (Test-Path "$Run\project-A\data\readings.txt")) -and
    (-not (Test-Path "$Run\project-B\data\week.txt")) -and (-not (Test-Path "$Run\project-B\data\readings.txt"))
Check 'U2 删除后两端路径一致' $bothGone
$s0 = Sha "$exa\summary.py"
Check 'U2 summary.py 未被误动 (A)' ((Sha "$Run\project-A\summary.py") -eq $s0)
Check 'U2 summary.py 未被误动 (B)' ((Sha "$Run\project-B\summary.py") -eq $s0)

# ---- U3 完整消息轮次 ----
& $ClientExe A "$Run\project-A" "$Run\state-A" $ServerUrl import-events "$WS\公开事件.jsonl" | Out-Null
Wait-For { (Get-Content "$Run\state-B\events.jsonl" -ErrorAction SilentlyContinue | Where-Object { $_ -match 'demo-1' }) } 'B 收到事件' | Out-Null
Start-Sleep -Milliseconds 500
$hist = & $ClientExe B "$Run\project-B" "$Run\state-B" $ServerUrl history
$histText = ($hist | Out-String)
$cUser   = $histText -match '\[user\.message u1\] 计算读数的平均值。'
$cCall   = $histText -match '\[tool\.call call-1\] sample\.mean \{"values":\[18,22\]\}'
$cResult = $histText -match '\[tool\.result call-1\] \{"mean":20\}'
$cAsst   = $histText -match '\[assistant a1\] 平均值是 20。'
Check 'U3 用户消息' $cUser
Check 'U3 工具调用' $cCall
Check 'U3 工具结果' $cResult
Check 'U3 助手回复' $cAsst

# 重放去重
$before = (& $ClientExe B "$Run\project-B" "$Run\state-B" $ServerUrl history | Out-String)
& $ClientExe A "$Run\project-A" "$Run\state-A" $ServerUrl import-events "$WS\公开事件.jsonl" | Out-Null
Start-Sleep -Milliseconds 500
$after = (& $ClientExe B "$Run\project-B" "$Run\state-B" $ServerUrl history | Out-String)
$countBefore = ([regex]::Matches($before, '\[user\.message')).Count
$countAfter  = ([regex]::Matches($after,  '\[user\.message')).Count
Check 'U3 重放去重（条目数不变）' ($countBefore -eq $countAfter) "before=$countBefore after=$countAfter"

# ---- 汇总 ----
Write-Host ''
Write-Host ("===== 共 {0} 项检查，{1} 失败 =====" -f $checks, $failures.Count)
if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Host "  FAILED: $_" }
    exit 1
}
Write-Host 'ALL PASS'
exit 0
