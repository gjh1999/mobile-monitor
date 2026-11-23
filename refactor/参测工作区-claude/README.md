# 文件与 Codex 消息同步引擎

**参测提交 · 2026-11-23**

本工作区实现了题目要求的可离线演示同步引擎：

- **协调服务**：Go，单进程 HTTP 服务，持久化版本化文件状态、操作日志、去重表和消息事件。
- **本地客户端核心**：Rust，每个设备一个进程，负责项目目录扫描、离线队列、冲突保留、事件导入和只读历史投影。
- **验证脚本**：PowerShell，提供一键构建、启动、停止、单进程恢复、控制、故障注入和两套端到端测试。

题目原件（`任务书.md`、`公开测试说明.md`、`公开事件.jsonl`、`示例项目/`）保持原样，未修改。

---

## 1. 目录

| 路径 | 说明 |
|---|---|
| `server/` | Go 协调服务源码与单元测试 |
| `client/` | Rust 客户端核心源码、单元测试与 `Cargo.lock` |
| `scripts/` | 构建、启动、停止、控制、测试脚本 |
| `README.md` / `DESIGN.md` / `VALIDATION.md` | 使用说明、设计说明、测试记录 |
| `bin/` | 构建产物（生成，不属源码） |
| `run/` | 运行时状态、日志和测试取证（生成，不属源码） |
| `client/target/` | Cargo 构建缓存（生成，不属源码） |
| `_toolchain/` | 本机便携 Go 工具链后备（生成，不属源码） |

没有修改题目原件，也没有在题目目录之外写入文件。

---

## 2. 环境与依赖

实际验证环境：

| 项目 | 值 |
|---|---|
| 系统 | Windows 11 Pro 10.0.26100 |
| Go | 1.27.4 windows/amd64（`go.mod` 声明 1.22） |
| Rust / Cargo | 1.100.0 |
| PowerShell | Windows PowerShell 5.1 |
| 网络 | 仅本机回环；运行时不访问外网 |

Rust 依赖由 `client/Cargo.lock` 固定：`serde`、`serde_json`、`sha2`、`base64`。Go 仅用标准库。

`scripts/build.ps1` 优先使用 PATH 中的 `go`/`cargo`；找不到 Go 时使用工作区内的 `_toolchain\go\bin\go.exe`。`_toolchain/` 只是本机后备，不是提交源码的一部分；在已安装 Go 1.22+ 的评审机上可直接删除。

---

## 3. 构建

所有命令均在工作区根目录执行（`D:\VscodeProject\codexDesk\mobile-monitor\refactor\参测工作区-claude`）。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物：

- `bin\server.exe`
- `bin\sync-client.exe`

---

## 4. 一键启动、停止与重启

### 4.1 从空状态启动三个进程

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Fresh
```

`-Fresh` 只清空工作区 `run\` 下指定的本次运行目录，然后启动：

| 角色 | 身份 | 控制/服务地址 | 项目目录 | 状态目录 |
|---|---|---|---|---|
| 协调服务 | coordinator | `http://127.0.0.1:7801` | — | `run\default\server-state` |
| 客户端 A | `A` | `http://127.0.0.1:7802` | `run\default\project-a` | `run\default\state-a` |
| 客户端 B | `B` | `http://127.0.0.1:7803` | `run\default\project-b` | `run\default\state-b` |

进程号、日志和完整路径写入 `run\default\pids.json` 与 `run\default\logs\`。

自定义用例目录和端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-all.ps1 `
  -RunRoot run\case-u2 -ServerPort 7811 -APort 7812 -BPort 7813 -Fresh
```

三个角色使用互不重叠的状态目录；同一用例的重启复用原路径、原端口和原身份。

### 4.2 停止

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-all.ps1
```

先请求各进程优雅退出，超时后强制结束；状态目录保留。

### 4.3 重启（同一数据目录）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-all.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-all.ps1
```

### 4.4 单独强制结束并恢复一个进程

```powershell
# 强制结束 B（默认强制；加 -Graceful 可先请求退出）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill-one.ps1 -Role b

# 用同一项目目录、状态目录、端口和身份恢复 B
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-one.ps1 -Role b
```

`-Role` 可为 `server`、`a`、`b`。`start-one.ps1` 从 `pids.json` 读取原路径和端口，并更新对应的 PID。也可直接查看 `pids.json` 后用 `Stop-Process -Id <pid> -Force` 结束进程。

---

## 5. 控制工具

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 [参数]
```

常用参数：

| 参数 | 含义 |
|---|---|
| `-Port 7802` / `-Port 7803` | 指定 A 或 B 的控制端口 |
| `-Action status` | 在线状态、待传队列、冲突、待重试回执、最近同步错误 |
| `-Action pending` / `conflicts` | 仅看待传/冲突 |
| `-Action sync` | 立即触发一次同步（平时每 300 ms 自动同步） |
| `-Action pause` / `resume` | 断开/恢复该客户端与协调服务的传输 |
| `-Action retry` | 放行丢回执后暂停的重试 |
| `-Action import -File <路径> [-StartLine n] [-EndLine m]` | 导入 JSONL 文件或行段（1 基、含首尾） |
| `-Action history -ThreadId <id> [-TurnId <id>]` | 只读历史（客户端投影） |
| `-Action replay -ThreadId <id> [-TurnId <id>]` | 只读回放，附派发计数 |
| `-Action threads` / `events` | 线程列表 / 原始事件 |
| `-Action fault-arm` / `fault-disarm` | 武装/解除“丢弃下一次成功提交回执” |
| `-Action server-status` / `server-conflicts` / `oplog` | 服务端已提交状态、冲突日志、操作日志 |
| `-Action shutdown-client` / `shutdown-server` | 优雅退出 |

所有响应均为 JSON。

---

## 6. 操作指南

### 6.1 文件同步

A、B 的项目目录就是普通目录；保存后客户端在 300 ms 内扫描并按需同步。也可显式触发：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action sync
```

示例（U2）：

```powershell
# 1. 把示例项目复制到 A
Copy-Item -Path 示例项目\* -Destination run\default\project-a -Recurse -Force

# 2. 在 A 追加一行
[IO.File]::AppendAllText((Join-Path (Get-Location) 'run\default\project-a\data\readings.txt'), "25`n", [Text.UTF8Encoding]::new($false))

# 3. 在 B 改名
Move-Item -LiteralPath run\default\project-b\data\readings.txt -Destination run\default\project-b\data\week.txt

# 4. 在 A 删除新路径
Remove-Item -LiteralPath run\default\project-a\data\week.txt -Force
```

观察：

```powershell
# 本机待传、待重试、冲突
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action status

# 服务端已提交的路径、版本、删除墓碑、操作次数
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Action server-status

# 单路径提交/冲突记录
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Action oplog -PathFilter data/readings.txt
```

`queue_empty: true` 表示本机待传队列已处理完；`awaiting_confirmation` 非空表示成功回执丢失、重试被暂停。

字节级核对：

```powershell
Get-FileHash -LiteralPath run\default\project-b\data\readings.txt -Algorithm SHA256
```

### 6.2 事件导入与历史查看

```powershell
# 导入整个文件到 A
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action import -File "D:\...\公开事件.jsonl"

# 只导入第 1～4 行（1 基，含第 4 行）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action import -File "D:\...\公开事件.jsonl" -StartLine 1 -EndLine 4

# 在 B 查看指定线程/轮次
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action history -ThreadId thread-weather -TurnId turn-01

# 查看整条线程（按轮次分组）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action history -ThreadId thread-weather

# 只读回放与派发计数
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action replay -ThreadId thread-weather -TurnId turn-01
```

事件按 `event_id` 在协调服务去重；相同 ID 重放不会重复计数。不同 `event_id` 即使文本相同也各拼接一次。助手回复按 `seq` 拼接，`assistant.done` 标记完成。工具调用与结果按 `call_id` 关联展示。

重启 A 或 B 后，历史仍可从协调服务读取；客户端也会缓存最近查询过的线程，服务不可达时可读缓存。

### 6.3 暂停与恢复

```powershell
# B 离线（进程和本地写权限保留）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action pause

# 此时修改 B 项目目录，本地文件保留；status 的 pending_count 增加
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action status

# 恢复传输并等待收敛
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action resume
```

暂停期间仍扫描本地文件并算出待传项，但不发网络请求。暂停期间提交的导入会排队，恢复后执行。

### 6.4 回执丢失故障注入（单次）

1. 从空状态启动并确认两端 `queue_empty: true`。
2. 武装故障：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Action fault-arm
```

3. 在 A 写入文件（示例内容 `sample-42` + LF）。为原子出现，可先写临时文件再移入：

```powershell
New-Item -ItemType Directory -Force run\default\project-a\notes | Out-Null
[IO.File]::WriteAllText((Join-Path (Get-Location) 'receipt.tmp'), "sample-42`n", [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath receipt.tmp -Destination run\default\project-a\notes\receipt.txt
```

4. 服务端持久提交后丢弃成功响应，A 在收到传输错误后进入“等待回执”，不会立即重试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action status
# awaiting_confirmation 非空；retry_blocked=true；pending_count>=1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Action server-status
# applied_ops=1；files["notes/receipt.txt"].version=1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Action oplog -PathFilter notes/receipt.txt
```

5. 强制结束三个进程（保留状态目录）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill-one.ps1 -Role a
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill-one.ps1 -Role b
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\kill-one.ps1 -Role server
```

6. 按“服务端 → B → A”恢复：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-one.ps1 -Role server
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-one.ps1 -Role b
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-one.ps1 -Role a
```

A 启动时默认放行待重试操作；重试使用相同的确定性 `op_id`，服务端返回原提交确认而不会二次提交。若希望启动后仍保持阻塞，可手工以 `--no-auto-retry` 启动 A，再用 `-Action retry` 放行：

```powershell
bin\sync-client.exe --device A --project run\default\project-a --state run\default\state-a `
  --server http://127.0.0.1:7801 --control 127.0.0.1:7802 --interval-ms 300 --no-auto-retry
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7802 -Action retry
```

7. 核对：B 文件字节正确；A `queue_empty: true`；`receipt_log` 有 `resolution`；服务端 `applied_ops` 仍为 1，操作日志仍只有一条 `applied`。再次重启 A、B 后内容与队列不变。

故障只对“下一次成功提交”生效；冲突响应不会触发丢弃。服务端先持久化提交（含 blob 与 `state.json`），再关闭连接，因此故障窗口正好位于“已提交、发起方未收到成功回执”。

### 6.5 历史回放与工具派发核查

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ctl.ps1 -Port 7803 -Action replay -ThreadId thread-weather -TurnId turn-01
```

响应包含：

- `history`：只读投影；
- `replay_mode: "read-only-projection"`；
- `tool_dispatch_attempts: 0`；
- `tool_call_items_observed`：看到的工具调用条目数（只计数，不执行）。

代码核查入口：

- `client/src/projection.rs`：纯函数，仅读取 JSON 值，没有进程、命令或工具执行依赖。
- `client/src/sync.rs`：事件导入只做 JSON 校验、HTTP 上传和缓存；没有任何执行器调用。
- `server/main.go`：事件只存原文，历史查询只返回事件/计数。
- 全仓库没有 `std::process::Command` 或等价的工具派发代码。

可以用以下命令辅助核查：

```powershell
Select-String -Path client\src\*.rs,server\*.go -Pattern "Command|process::|exec|CreateProcess" -CaseSensitive
```

---

## 7. 取证接口与状态文件

| 证据 | 位置 |
|---|---|
| 协调服务已提交状态 | `GET http://127.0.0.1:7801/api/status` |
| 操作日志（可过滤路径） | `GET http://127.0.0.1:7801/api/admin/oplog?path=...` |
| 冲突日志 | `GET http://127.0.0.1:7801/api/admin/conflicts` |
| 原始事件 | `GET http://127.0.0.1:7801/api/events?thread_id=...` |
| A/B 状态 | `GET http://127.0.0.1:7802/status`、`http://127.0.0.1:7803/status` |
| 服务端持久状态 | `run\default\server-state\state.json`，文件内容在 `server-state\blobs\` |
| A 持久状态 | `run\default\state-a\client-state.json` |
| B 持久状态 | `run\default\state-b\client-state.json` |
| 进程号/路径 | `run\default\pids.json` |
| 进程日志 | `run\default\logs\*.out.log` / `*.err.log` |

`state.json` 内的关键字段：`files`（每路径当前版本、删除墓碑、哈希、提交设备）、`applied_ops`（逻辑操作去重表）、`op_log`（含冲突尝试）、`conflicts`、`fault_log`、`events`、`devices`。客户端状态含 `base`（已确认基线）、`last_seen_version`、`conflicts`、`receipt_log`（丢回执恢复记录）、`imports`。

客户端时间戳为 Unix 毫秒；服务端为 RFC3339。两端日志均带时间。

---

## 8. 同步与冲突语义

- 每个路径由协调服务分配单调版本号；客户端提交时携带上次确认的 `base_version`。
- 版本匹配才应用；否则返回冲突，不覆盖任何一方。
- 客户端操作 ID 由设备、路径、基线版本、内容哈希确定性生成；重试和服务重启后仍相同，服务端按 `op_id` 去重。
- **远端删除 vs 本机修改**：本机修改字节先保存到 `<原路径>.conflict-<设备>-v<服务端版本>`，再在原名上保持删除墓碑；冲突记录写入客户端与协调服务。冲突副本随后作为普通新文件同步到另一端。
- **双方修改**：本机内容保存为冲突副本，原名采用服务端内容。
- **本机删除 vs 远端修改**：远端内容恢复到原名，冲突记录保留本机删除意图。
- 同一已确认变更重复传输、进程重启或重试只产生一份逻辑提交。
- 同步先推送本机变更，再拉取服务端版本；拉取时若仍发现未提交的本地修改，按同样规则保留双方内容。

---

## 9. 测试

一条命令运行全部测试（Go 单元测试 + Rust 单元测试 + 公开验收 U1–U3 + 故障场景 F1–F3）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

单独运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\e2e-public.ps1   # U1-U3
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\e2e-fault.ps1    # F1-F3
```

测试特性：

- 每个用例使用独立临时根目录与随机空闲端口，测试结束保留状态、日志和取证。
- U1–U3 使用题目公开材料；F1–F3 使用自建合成事件与文件，不依赖评审隐藏输入。
- 所有等待上限 30 秒，逐项打印 `PASS`/`FAIL` 和实际值。
- 结果写入 `run\e2e-*\e2e-result.json`、`run\fault-*\e2e-fault-result.json`。

最近一次结果（2026-11-23）：公开验收 **27/27 通过**，故障场景 **31/31 通过**。详细断言与证据见 `VALIDATION.md`。

---

## 10. 实际验证范围与已知限制

已验证：

- 从空状态启动三个隔离实例，停止/重启后继续工作。
- 文件初始复制、追加、改名、删除的双向字节一致；旁侧文件未被误动。
- 公开事件的首次导入、完整重放去重、助手文本拼接、调用/结果关联。
- 离线修改与远端删除冲突，冲突副本精确到字节，重启后仍可找回。
- 重叠批次导入去重、相同文本的不同事件各计一次、重启后历史一致。
- 单次成功回执丢弃、发起方暂停重试、强制重启、按原 `op_id` 重试只提交一次。
- 只读回放不派发工具，无标记文件副作用。

已知限制：

- 仅本机回环测试；无认证、TLS、多协调服务或公网部署。
- 300 ms 轮询扫描，不是操作系统文件监听；大文件会整体读入内存，服务端请求上限 64 MiB。
- 改名按“删除 + 新增”处理，不保留文件系统元数据（权限、时间戳、符号链接）。
- 冲突副本不会自动合并或清理；墓碑、历史版本 blob 和操作日志不做垃圾回收，适合演示规模。
- 消息事件只校验必需字段，不校验完整 Codex 语义；`seq` 按线程排序，轮次内条目按首次出现序号排列。
- 不支持超过两个设备的并发压力、时钟偏斜或非 UTF-8 文件名。
- 客户端在收到“已发送后”的传输错误时一律保守地暂停重试，需要重启或 `/retry` 放行；这同样适用于服务端真实崩溃而不仅是故障注入。
- PowerShell 脚本面向 Windows；Go/Rust 源码本身可跨平台构建。
- 未实现真实 Codex 接入、工具执行、GUI 或公网部署（题目留给后续评测）。

---

## 11. 源码包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\package.ps1
```

生成 `dist\sync-engine-source-<时间戳>.zip`，并在 `dist\last-package.json` 写入 ZIP 的 SHA-256 和字节数。打包只包含源码、脚本、文档和题目原件，不含 `bin/`、`run/`、`client/target/`、`_toolchain/`、`dist/` 及评审专用输入。

---

## 12. 退出与清理

- 正常退出：`scripts\stop-all.ps1`。
- 强制退出单个角色：`scripts\kill-one.ps1 -Role <server|a|b>`。
- 删除某次运行的全部状态：删除其 `run\<名称>` 目录（脚本生成的路径均在工作区 `run\` 下）。
- 重新开始：`scripts\start-all.ps1 -Fresh`。

运行时不访问公网，不会修改题目原件或工作区外文件。
