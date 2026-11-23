# 文件与 Codex 消息同步引擎 · 提交物

按《任务书》与《公开测试说明》实现的本地同步引擎：**Go 编写协调服务与进程编排器，Rust 编写客户端核心**。全部验收在本机回环网络完成，不依赖外部账号、公网或真实 Codex。

- 架构：两个客户端（A、B）各自持有独立的项目目录、身份与持久状态；客户端之间不直接通信，只通过协调服务交换数据。B 断开期间 A 可继续工作。
- 同步模型：显式触发。`sync` = 推送本机待传变更 → 推送未确认事件 → 拉取远端变更（按基线判定并发冲突，双方内容都保留）→ 拉取远端事件。每一步先落盘再出网，任一时刻强杀进程都不丢已保存的更改。
- 事件模型：JSONL 事件按 `event_id` 在客户端与服务端双重去重；`tool.result` 作为独立逻辑条目按 `call_id` 关联到 `tool.call`；`assistant.delta` 同 `item_id` 按事件序拼接；轮内条目按首次出现序号排列。

## 1. 依赖与版本

| 依赖 | 版本 | 获取方式 |
|---|---|---|
| Go | 1.27.4 | **已随包提供**：`.toolchain/go/`（便携版，官方 `go1.27.4.windows-amd64.zip` 解压，SHA-256 `178ab98950ac69ab25069307dc6ade79f3816e414cce95b4da166a115cde1e7e` 与 go.dev 一致）。不写入系统 PATH。 |
| Rust | 1.100.0（stable-x86_64-pc-windows-gnu） | 评审机需已安装 `rustc`/`cargo`（如 `C:\Users\<用户>\.cargo\bin`）。构建脚本会自动探测。 |
| cargo 依赖 | serde 1.0.229 / serde_json 1.0.154 / sha2 0.10.9 | 仅这三个 crate 及其闭包，全部命中本机 cargo 缓存，`cargo build --offline` 可构建。服务端与编排器**零第三方依赖**（纯 Go 标准库），无需网络。 |
| PowerShell | 5.1（Windows 自带） | 所有脚本与命令均在此版本验证过。 |
| 构建产物 | `bin/server.exe`、`bin/syncctl.exe`、`bin/syncclient.exe` | 见下节构建命令。 |

Go 服务端设计为零外部依赖，因此即使完全没有网络，`go build` 也能完成。

## 2. 构建

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物写入 `bin\`。已随包附带一份构建好的 `bin\`（在同一台机器上构建），如评审环境可直接使用，可跳过构建。

## 3. 一键启动（从干净工作区）

```powershell
# 首次（会自动构建缺失的二进制，然后从空状态启动三个进程）
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Fresh

# 不清空数据、沿用已有状态目录重启
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
```

等效一条命令（二进制已在时）：`bin\syncctl.exe up --root run --fresh`

启动后输出三个进程的身份、PID、地址、项目目录与状态目录：

| 角色 | 地址 | 项目目录 | 状态目录 | 日志 |
|---|---|---|---|---|
| 协调服务 | 127.0.0.1:8730 | — | `<运行根>\server-data` | `<运行根>\logs\server.log` |
| 客户端 A | 127.0.0.1:8731 | `<运行根>\project-a` | `<运行根>\a-state` | `<运行根>\logs\a.log` |
| 客户端 B | 127.0.0.1:8732 | `<运行根>\project-b` | `<运行根>\b-state` | `<运行根>\logs\b.log` |

`<运行根>` 默认为工作区下的 `run\`，可用 `--root <dir>` / `-RunRoot` 改到任意空目录。

**退出**：`bin\syncctl.exe down --root run`（强制结束三个进程，数据目录保留）。四个命令全部支持 `--root` 参数放在任意位置，例如 `kill b --root run` 与 `kill --root run b` 等价。

## 4. 运行测试（一条命令）

```powershell
powershell -ExecutionPolicy Bypass -File tests\run_all.ps1
```

9 个用例，每个用例独立运行根、从空状态开始，检查点等待上限 30 秒（与评测说明一致）。逐条断言输出在控制台，证据（转储、哈希、服务端状态、提交日志等）写入 `运行结果\<用例>\`。

| 用例 | 验证内容 |
|---|---|
| U1-boot | 空状态一键启动；三进程身份/路径正确；A、B 初始为空；停止后原路径重启，三角色恢复 |
| U2-files | 示例项目 A→B 字节一致（含题面两个 SHA-256）；A 追加 `25` 后 B 哈希一致；B 改名 `week.txt` A 跟随；A 删除后两端两个路径均消失；`summary.py` 全程与原件一致 |
| U3-events | 导入全部公开事件后在 B 核对 `thread-weather/turn-01` 四条逻辑记录（u1/c1/r1/a1，含 call_id 关联与 delta 拼接）；重放导入后总数、顺序、关联、文本不变；服务端事件数恒为 6 |
| C1-conflict | 同基线双端修改：双方内容在两端均可读（原名 + `.conflict-from-<端>-<batch>` 文件），冲突记录落盘 |
| C2-delete-edit | 删除与修改相遇：可读修改内容保留在原路径，删除意图以 `.deleted-by-<端>-<batch>.json` 墓碑呈现，最终两端收敛一致 |
| C3-fault | 回执丢失注入：服务端已持久提交（version=1、commits=1）但 A 未收到回执；A 保持挂起（重试前暂停）；同 batch_id 重试幂等命中，version/commits 不变，`dup_hits=1`；B 只见一份 |
| C4-kill | 强杀 B 后 A 继续；同数据目录重启 B 收敛；强杀 server 后客户端明确报不可达且变更不丢；重启 server 从日志恢复全部状态 |
| C5-segment-replay | 行段导入（1–3、4–6）拼出完整轮次；全量重放幂等；只读回放 4 条目、`tools_dispatched=0`，回放日志可查 |
| C6-pause | 暂停 B：拒绝出网、本地更改可恢复、状态明确报告待传；恢复后队列收敛、离线期间两端变更字节一致地互通 |

## 5. 手动操作清单（评审者视角）

以下命令假定运行根为 `run`，两个客户端的 daemon 已由 `start_all.ps1` 启动。所有能力也可直接 `curl` 各端点的 HTTP 接口（路径见第 7 节）。

### 文件同步（显式触发）
```powershell
# 修改 A 的项目目录后触发一轮同步（A 推送并拉取）
bin\syncctl.exe sync a
bin\syncctl.exe sync b      # 同 B
bin\syncctl.exe sync all    # 两侧各一轮

# 循环触发直到两端队列清空（默认上限 30 秒），收敛时报告轮数
bin\syncctl.exe converge --timeout 30

# 查看本机待传、冲突、服务端版本
bin\syncctl.exe status                # 三进程健康 + 队列 + 冲突计数
bin\syncctl.exe status --wait --timeout 30   # 轮询直至收敛或超时，超时明确列出仍有待传/冲突的一端
curl http://127.0.0.1:8731/status     # A 的详细状态（版本、待传批次、冲突数、事件数）
curl http://127.0.0.1:8731/files      # A 项目实况（相对路径 + sha256 + 大小）
curl http://127.0.0.1:8731/conflicts  # A 的冲突记录
curl http://127.0.0.1:8732/files      # B 同理（端口换 8732）
```

### 事件输入与查看
```powershell
# 向 A 导入 JSONL（绝对路径；B 端 converge 后即可查看）
$body = '{"file": "D:/.../公开事件.jsonl"}'                    # UTF-8
curl -X POST -d $body http://127.0.0.1:8731/import
# 指定行段（1 起始、含端点）
$body = '{"file": "D:/.../公开事件.jsonl", "from": 2, "to": 5}'
curl -X POST -d $body http://127.0.0.1:8731/import

bin\syncctl.exe converge        # B 拉取事件
curl "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01"          # B 人类可读转储
curl "http://127.0.0.1:8732/dump?thread=thread-weather&turn=turn-01&format=json"   # 机器可读
curl "http://127.0.0.1:8732/dump"   # 不带参数 = 全部线程/轮次
```

### 暂停与恢复
```powershell
curl -X POST http://127.0.0.1:8732/pause    # B 离线：sync 拒绝出网，本地更改照常保存
curl http://127.0.0.1:8732/status           # online=false；待传/冲突计数仍在
curl -X POST http://127.0.0.1:8732/resume   # B 恢复在线
bin\syncctl.exe converge                    # 队列收敛；恢复期间的两侧变更互通
```

### 回执丢失（故障注入，可重复的单次）
```powershell
# 1) 预武装：下一个来自 A 的文件提交，在服务端持久提交之后、回执发出之前被丢弃
bin\syncctl.exe fault arm --mode commit-ack --target A --count 1

# 2) A 保存并提交：服务端已提交，A 收到 "receipt lost"，变更挂起（不自动重试 = 重试前暂停）
echo data > run\project-a\note.txt
bin\syncctl.exe sync a          # 报告 receipt_lost=true, pending_push>=1

# 3) 取证：此时服务端已经持久提交
curl http://127.0.0.1:8730/v1/status    # version=1, commit_count=1（重试前）
bin\syncctl.exe serverlog --limit 10    # 提交日志（含 batch_id、变更明细）

# 4) 重试同一操作：同一个 batch_id 幂等命中
bin\syncctl.exe sync a          # 重试成功，version 不变
curl http://127.0.0.1:8730/v1/status    # version 仍为 1，commit_count 仍为 1，dup_hits=1（重试后）

# 5) 可重复：再次 arm 即可重演；--mode events-ack --target B 可注入事件导入的回执丢失
bin\syncctl.exe fault clear     # 手动解除（单次注入命中后也会自动解除）
```

### 历史回放（只读）
```powershell
# 回放指定轮次；纯文本重组，不存在工具执行路径
$body = '{"thread": "thread-weather", "turn": "turn-01"}'
curl -X POST -d $body http://127.0.0.1:8732/replay
# 输出中每个工具调用标注 [historical record, NOT dispatched]，末尾汇总 tools_dispatched=0
# 每次回放追加一条记录到 <状态目录>\events\replay.log（含 tool_calls_seen 与 tools_dispatched 字段）
```

可核查事实：`client\` 源码中不存在任何进程启动/命令执行路径（未引用 `std::process::Command`）；回放只重组已入库的事件文本，`tools_dispatched` 恒为 0。

### 启动与重启
```powershell
bin\syncctl.exe kill b --root run       # 单独强杀任一进程（Windows TerminateProcess）
bin\syncctl.exe start b --root run      # 用同一数据目录恢复该进程
bin\syncctl.exe kill server --root run  # 协调服务同理；客户端会明确报不可达、变更不丢
bin\syncctl.exe start server --root run # 从提交日志+事件日志重建全部索引后继续
bin\syncctl.exe down --root run         # 全部停止
bin\syncctl.exe up --root run           # 保留数据重启（不加 --fresh）
bin\syncctl.exe up --root run --fresh   # 清空运行根，从空状态重启
```

## 6. 幂等与冲突语义（实现说明）

- **提交幂等**：客户端先把变更与批次号落盘，再携带同一 `batch_id` 出网；服务端按 `batch_id` 去重，重复提交返回首次结果并记入日志（`duplicate: true`）。因此「回执丢失后重试」与「进程重启后重试」都只会产生一份逻辑结果。
- **事件幂等**：`event_id` 在客户端事件库、服务端事件日志两处去重；分批导入、整文件重放、重复传输均不产生第二份。
- **冲突呈现**：拉取端以变更携带的 `base_hash` 与本地状态对比判定并发。修改×修改：本地内容留在原路径，远端内容写 `<路径>.conflict-from-<端>-<batch前8位>`；删除×修改：可读内容留在原路径，删除意图写 `<路径>.deleted-by-<端>-<batch前8位>.json`。冲突文件本身会作为普通文件传播，最终两端都能找回双方内容。冲突的解决是显式的人工动作（改文件后重新 sync 即恢复收敛）。
- **崩溃恢复**：状态目录中的快照（基线、待传批次、游标、冲突记录）先于网络操作落盘；服务端提交/事件日志逐条 fsync。强杀后重启即从磁盘状态与日志重建，不重复应用已确认变更。

## 7. 接口一览

协调服务（127.0.0.1:8730）：

| 方法 路径 | 用途 |
|---|---|
| `GET /v1/status` | 版本、提交数、幂等命中数、事件数、故障注入状态 |
| `GET /v1/log?limit=N` | 提交日志尾部（取证） |
| `PUT /v1/blob/<sha256>` / `GET /v1/blob/<sha256>` | 内容寻址 blob 存取 |
| `POST /v1/commit` | 幂等提交一批变更；故障注入点 |
| `GET /v1/feed?since=&client=` | 增量拉取其他端的变更 |
| `POST /v1/events` / `GET /v1/events/feed` | 事件导入（幂等）/增量拉取 |
| `POST /v1/admin/fault` / `GET /v1/admin/fault` | 武装/解除/查看故障注入 |

客户端 daemon（A=8731，B=8732）：`GET /status`、`POST /sync`、`POST /pause`、`POST /resume`、`GET /files`、`GET /conflicts`、`POST /import {file,from,to}`、`GET /dump?thread=&turn=&format=`、`POST /replay {thread,turn}`。

一次性 CLI（不经 daemon，用于单步排查）：`bin\syncclient.exe sync|status|pause|resume|files|conflicts|import-events|dump-events|replay --server http://127.0.0.1:8730 --project <项目目录> --state <状态目录> [--id A]`。注意：同一状态目录同一时刻只允许一个进程持有（自动互斥锁），daemon 在运行时请走 daemon 接口。

## 8. 实际验证范围

以上第 4 节全部 9 个用例在提交前实际运行通过（输出留存于 `运行结果\`，`summary.txt` 为汇总）。验证覆盖：公开用例 U1/U2/U3 的全部断言（含题面给定的两个 SHA-256、四条逻辑记录、重放不变性）；以及双端冲突保留、删除×修改双方呈现、回执丢失前后版本与提交数对比、三进程逐一强杀并用原数据目录恢复、行段导入、只读回放可观察记录、暂停/恢复收敛。

## 9. 已知限制

- 未接入真实 Codex（任务书允许留待后续）：事件全部来自随题 JSONL 文件；回放为只读重组，不派发任何工具。
- 无图形界面（任务书允许留待后续）：观察与操作全部通过 CLI/HTTP 接口。
- 仅本机回环；未做 TLS、鉴权与多机部署。
- 冲突自动合并（行级三方合并）不在范围内：任务书要求的是「找回双方内容」，实现为保留双方文件与显式冲突记录，由用户决定最终版本。
- JSON 数值键序按字母序输出（serde_json 的 BTreeMap 映射）；题面样本的单键对象（`{"values":[18,22]}`、`{"mean":20}`）不受影响。
- 客户端一次性 CLI 与 daemon 共用状态目录时互斥（防并发写坏快照）；自动化请统一走 daemon 或 syncctl。
- 服务端 feed 拉取为全量扫描日志（演示规模无压力）；blob 上传为整文件传输，未做分块续传。
- `scripts\build.ps1` 中 Rust 构建优先 `--offline`（依赖已在本机 cargo 缓存），若评审机缓存不同会自动回退在线构建。

## 10. 目录结构

```
├─ bin\                        构建产物（server / syncctl / syncclient）
├─ cmd\server\                 Go 协调服务
├─ cmd\syncctl\                Go 进程编排器
├─ internal\{model,store}\     Go 共享类型与持久化
├─ client\                     Rust 客户端核心（engine / events / httpc / store / main）
├─ scripts\                    build.ps1 / start_all.ps1 / stop_all.ps1
├─ tests\                      run_all.ps1 + cases\*.ps1（9 个用例）
├─ 运行结果\                    测试证据输出
├─ .toolchain\go\              随包便携 Go 1.27.4
├─ 任务书.md / 公开测试说明.md / 公开事件.jsonl / 示例项目\   题面与样本原件（未改动）
```
