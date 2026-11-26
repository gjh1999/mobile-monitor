# 文件与 Codex 消息同步引擎

离线演示：一个 Go 协调服务 + 两个 Rust 本地客户端（A、B），通过本机回环 HTTP 交换文件与合成 Codex 事件。客户端只经服务端通信，可单独断开任一端。

- 服务端：`server/`（Go 1.27.4，仅标准库）
- 客户端核心：`client/`（Rust 1.100.0，仅标准库，手写 JSON/HTTP/FNV-1a）
- 运维脚本：`scripts/`（PowerShell）
- 运行状态与日志：`run/`（生成物，已 gitignore）

## 依赖版本

| 依赖 | 版本 | 用途 |
|---|---|---|
| Go | 1.27.4 | 服务端 |
| Rust (cargo) | 1.100.0 | 客户端 |
| PowerShell | 5.1+ | 一键脚本 |

脚本优先使用 `$env:GO` / `$env:CARGO` 或 PATH 中的 `go`/`cargo`；本工作区父目录 `../_toolchain/go` 提供一个离线 Go 工具链（无网时自动回退）。首次运行需允许脚本执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
```

## 一键启动 / 停止 / 重启

```powershell
# 构建并启动服务端 + 客户端 A + 客户端 B（三个独立进程，状态目录互不重叠）
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1

# 停止全部
powershell -ExecutionPolicy Bypass -File scripts\stop-all.ps1

# 单独结束某一进程（server / A / B），再用 start-all 以同一数据目录恢复
powershell -ExecutionPolicy Bypass -File scripts\kill-one.ps1 -Id B

# 清空运行状态（回到空状态）
powershell -ExecutionPolicy Bypass -File scripts\reset.ps1
```

- 端口：`127.0.0.1:8787`（服务端 HTTP）。
- 项目目录：`run/project-A`、`run/project-B`；状态目录：`run/state-A`、`run/state-B`；服务端状态：`run/server-state`。
- 客户端为守护进程，每 2 秒自动同步一次；`sync` 触发文件即一次性同步。日志：`run/logs/*`、`run/state-*/daemon.log`、`run/state-*/status.txt`。

## 文件同步（显式触发）

```powershell
# 请求某个客户端立即同步（写 trigger 文件，等守护进程消费后打印状态）
powershell -ExecutionPolicy Bypass -File scripts\sync.ps1          # A、B 都同步
powershell -ExecutionPolicy Bypass -File scripts\sync.ps1 -Ids A   # 只同步 A

# 查看待传 / 冲突 / 已提交位置
powershell -ExecutionPolicy Bypass -File scripts\status.ps1
```

对 `run/project-A` 或 `run/project-B` 做增删改/重命名后执行 `sync` 即可。冲突时：被保留的远端版本在规范路径，本地修改保存在 `<路径>.conflict-<哈希前8位>`（作为普通文件随下次同步传播到对端）；删除与修改相遇时保留删除意图（规范路径消失）并把修改内容存为冲突副本。

## 事件输入与查看

```powershell
# 向 A 导入 JSONL（全部；或指定 1 基行段 start end）
powershell -ExecutionPolicy Bypass -File scripts\import-events.ps1 -File 公开事件.jsonl -Id A
powershell -ExecutionPolicy Bypass -File scripts\import-events.ps1 -File 公开事件.jsonl -Id A -Start 1 -End 3

# 在 B 查看按线程/轮次整理的完整历史（只读回放，不派发工具）
powershell -ExecutionPolicy Bypass -File scripts\history.ps1 -Id B
```

## 暂停与恢复 / 故障注入

```powershell
# 一端离线 / 在线
powershell -ExecutionPolicy Bypass -File scripts\online.ps1 -Id B -On 0   # 离线
powershell -ExecutionPolicy Bypass -File scripts\online.ps1 -Id B -On 1   # 在线

# 回执丢失：服务端持久化后丢掉下一次 push 的响应（客户端不更新基线，重试按 op_id 去重）
powershell -ExecutionPolicy Bypass -File scripts\fault.ps1 -Kind drop
# 崩溃：服务端持久化后立即退出（不发送回执）
powershell -ExecutionPolicy Bypass -File scripts\fault.ps1 -Kind crash
# 重启服务端后，用 sync 重试；提交次数不变（op_id 去重），见 scripts\test-all.ps1 与 VALIDATION.md
```

`sync` 失败时操作保留为待传；服务端 `/stats` 展示 `head/commits/files/conflicts/events` 用于核对“重试前后提交次数不变”。

## 一键回归测试

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

覆盖 U1（空状态启动 / 停止重启）、U2（复制→追加→改名→删除四段字节断言 + summary.py 未被误动）、U3（四类消息条目 + 重放去重）。退出码 0 表示全过。

## 退出方法

`stop-all.ps1` 按 PID 文件结束三个进程；服务端收到停止即释放端口。所有状态落在 `run/`，可随时 `reset.ps1` 清空或整目录删除。
