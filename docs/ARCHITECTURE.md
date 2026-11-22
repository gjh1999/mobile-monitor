# 架构与文件说明

[文档索引](README.md) · [配置说明](CONFIGURATION.md)

## 正式运行路径

`desktop_app.py` 是正式启动入口，选择原生界面，优先 Qt，随后 Tk / Win32。桌面进程通过 `monitor.start_background_service()` 启动后台工作线程并管理退出生命周期。Qt 托盘退出显式调用应用 quit，不依赖隐藏窗口的 lastWindowClosed；closeEvent、aboutToQuit 和主循环 finally 复用幂等 shutdown，单实例共享锁在主循环结束后的 finally 才释放。`monitor.py` 是内部模块，负责配置、状态与工作线程编排，不提供独立服务入口。

Codex 和 Claude 各有独立 worker、RPC 适配器、任务控制器和本地日志。界面切换改变展示与请求路由，两个 worker 继续工作。它们共用连接绑定的审批集合，公开审批带客户端标识。可选 Stream worker 接收经身份校验的钉钉指令。

正式入口和核心模块保留在项目根目录，保持 CLI、计划任务、资源和状态定位稳定。`desktop_qt_v2.py` 仅为兼容转发，日常使用 `desktop_app.py`。

## 模块职责

| 模块 | 职责 |
|---|---|
| `monitor.py` | 内部配置与状态根目录、双 worker 编排、状态快照和通知编排 |
| `desktop_app.py` | 正式桌面入口、前端选择、Tk 回退、单实例和 worker 生命周期 |
| `desktop_qt.py` | 完整 Qt 双客户端任务、对话、失败弹窗、审批与托盘界面 |
| `desktop_theme.py` | 蓝白主题、字体、消息排版和通用显示样式 |
| `desktop_win32.py` | 缺少 Qt / Tk 时的 Win32 回退界面 |
| `universal_rpc.py` | Codex app-server stdio JSON-RPC 连接、读写、请求响应和通知分发 |
| `rpc_client.py`、`rpc_errors.py` | Codex RPC 兼容导出和共享错误类型 |
| `claude_rpc.py` | Claude CLI stream-json 进程、会话恢复、消息、中断、工具权限和测试草稿 |
| `claude_sessions.py` | 本机 Claude JSONL 增量读取、live 元数据、外部会话活动和对话发现 |
| `task_control.py` | 持久控制意图与操作队列、接入/观察/控制校验、本机任务别名，以及 Codex 调度任务投影 |
| `automation.py` | 连接绑定的审批状态、过期/撤销，以及带持久预留的 Codex 调度器 |
| `quota_recovery.py` | Codex 额度等待、重查刷新结果、原目标恢复和不确定送达防重放 |
| `managed_tasks.py` | 显式配置的 Codex 新线程创建、目标建立、恢复、防重复与配置指纹 |
| `daemon_support.py` | 限定范围的 Codex 命令自动审批策略和 Windows 提醒去重 |
| `reply_log.py`、`conversation.py` | 本地消息/事件记录及只读对话展示 |
| `history_labels.py` | Codex 本机历史会话的标题和路径标签 |
| `personal_sync.py`、`code_sync.py` | 项目登记、自动及手动同步轮次、GitHub 代码同步和项目记忆 |
| `code_backups.py`、`code_backup_server.py` | 生成可下载的代码 ZIP，按私人工作区鉴权，校验后原子替换该设备的最新快照 |
| `codex_watch.py` | 增量读取原生轮次完成，订阅共享 Codex App Server 的待审批会话 |
| `codex_workspace.py` | 同工作区的提醒、待审批项、审批决定和回执 |
| `channels_dingtalk.py` | 签名 HTTPS Webhook、业务返回检查、脱敏和不确定送达处理 |
| `dingtalk_commands.py` | 可选 Stream SDK 接入、身份三元组、消息去重与一次确认码 |
| `attachments.py` | 辅助会话接入/观察类，保留独立测试；当前主控制流程使用 `TaskControl` |
| `windows_notify.ps1`、`app.ico` | 源码及 EXE 共用的运行资源 |

早期 `ClientManager` 和连接示例已保留在 `archive/old-prototypes/`；当前正式入口不导入它们。

## 控制、调度与审批

移出会取消同一任务较早的接入与聚焦请求。手动消息和继续操作在取得传输写锁后再核对暂停与移出。Codex 切换任务后，日志按旧线程和轮次 ID 接收已启动轮次的回复，直到该轮结束。

桌面界面先把操作写入 `TaskControl`，后台再通过对应客户端执行。`queued` 表示已排队；应结合最终操作状态、任务状态和回复记录判断结果。服务在执行中断开时保留 `unknown`，不自动重发。

Codex 调度候选来自配置 `tasks`、`ManagedTasks.sync()` 和界面已托管任务，先去重并排除手动暂停、移出或关闭自动推进的本地目标。常规调度核对实时 idle、显式启用与独占、本连接所有权、active goal、无待审批、版本信息和发送前状态，再写入持久 reservation，最后调用 `turn/start`。它不根据发现的历史任务或单纯配置字段推断所有权。

`monitor.collect()` 先读取官方额度，再调度。`quota_recovery.py` 将 `usageLimited` 或实际耗尽的受管任务写入 `scheduler.sqlite3.quota_waits`，按受限窗口的较晚刷新时间唤醒；只有新额度明确 `ordinaryUsageAllowed=true` 才允许恢复。恢复使用 `thread/goal/set` 的状态与 `origin=automatic`，保留目标、预算和用量；Codex 已自行运行时结束等待，不再补发。发送前再次核对本地停止意图；先持久预留，送达未知或崩溃遗留不自动重放。账号、目标或预算变化时停止恢复并提示。

Claude 不调用 Codex 的 `Scheduler.tick()` 或 `ManagedTasks.sync()`。其显式开启的自动推进由 `TaskControl.tick_auto_progress()` 实现，使用控制库持久化周期、版本和发送前预留；工作线程调用该方法，复用既有 CLI 发送接口而不改消息协议。实时空闲、本监控所有权、无审批/错误和未暂停都满足后才发送；不确定送达会关闭开关，崩溃遗留的预留不自动重放。扫描和接入既有会话不发起模型请求；外部仍打开的会话只观察。手动消息和暂停的协议保持不变，额度仍未知。

审批绑定原连接和 request ID，处理过期、服务端撤销和断连。自动规则只覆盖显式允许的 Codex 完整命令；其余请求由桌面或经身份校验的 Stream 单次处理。文件审批缺少具体变更详情时无法批准，不把旧客户端弹窗当作本服务请求。

### 全局 Codex 监控与审批队列

独立的 `codex-watch` worker 增量读取当前 Codex 用户目录下的原生会话完成事件。它也收集 RPC 完成事件，按会话和轮次发出一次 Windows 完成提醒。共享 App Server 已运行时，后台通过本机 `app-server proxy` 发现等待审批的会话并订阅实时请求，用户无需在澜汐接入该任务。独立进程中的原生审批仍由它自己的客户端处理。后台只通过原请求连接处理可核对的审批请求。

连接云服务器时，各台运行澜汐的电脑通过现有私人工作区记录通道交换完成事件和待审批项。记录包含来源设备和审批详情，协调服务按工作区鉴权。离线记录通过心跳判定过期。跨机决定带有独立请求 ID，来源电脑逐项检查有效期、详情和权限后执行，并发布实际结果。原请求重复投递沿用原结果。

R36 在共享连接发出同意应答前，使用来源设备的当前配置向协调服务复核控制权。本机决定和跨机决定走同一检查。已交还会话即使仍有其他本机任务占用同一项目，也会返回 `control_lost`。审批复核只读取控制状态。已显式登记的项目还核对写入者版本与当前控制版本。

前端可用 `monitor.approval_snapshot()` 获取 `pending`、`decisions`、`sharedCodex` 和 `workspace`。`pending` 中的 `token` 标识具体请求，`canAllow` 表示当前可同意，`deviceName` 标识来源，`decisionQueued` 表示跨机决定待回执。`decisions` 是近期跨机决定的结果字典。`sharedCodex` 为 `shared` 或 `own_connection`。`workspace` 为 `connected`、`retrying` 或 `local`。

用 `monitor.decide_approvals(tokens, allow)` 提交显式选中的令牌列表，返回逐项结果。全部同意时，前端提交当前窗口内可同意的全部令牌。`queued` 表示已安排发往来源电脑，实际结果随后写入 `decisions`。Web 对应的认证接口为 `GET /api/approvals` 与 `POST /api/approvals/batch`，后者接受 `{"tokens":["…"],"allow":true}`，单批最多 256 项。原单项接口继续可用。

桌面的 `desktop_approvals.py` 渲染统一队列和原始详情；`desktop_qt.py` 后台提交本次批次的令牌，并在后续队列刷新时以 `decisions` 更新跨机结果。等待回执的项不可重复操作。近期结果保留五分钟。 [桌面审批操作](DESKTOP.md#审批窗口)

## 通知

Claude 继续在回复库维护待通知队列。Codex 使用真实轮次完成事件发提醒。应用启动之前已完成的原生历史跳过，来自 RPC、原生日志或同工作区设备的同一完成按会话和轮次去重。R50 接收端默认仅显示本机完成提醒，开启跨机完成提醒后显示启用时间之后的轮次。审批队列继续跨机同步，桌面在请求有效期内记住已经自动弹出的请求。

Windows 通知使用分客户端的 SQLite 去重，正文显示摘要；完整内容保存在本地日志。调用通知 API 成功与系统实际显示通知是两个验收层次。

当前主循环中的 Webhook 发送路径用于待审批提醒，使用 `notifications.sqlite3` 先预留再发送，防止跨重启重复外发。独立 `scripts/notify_cli.py` 只提供手动检查与测试，去重限单进程。Claude worker 使用 Windows 提醒；Stream 可查看共用审批队列，但“状态”命令目前返回 Codex 顶层快照。

## 目录维护规则

- `tests/` 收纳 Python 回归和虚构会话 Qt smoke；从根目录按运行文档执行，Python discovery 使用 `-t .`。
- `scripts/` 收纳构建、登录启动、通知检查；开发辅助工具放 `scripts/dev/`。工具按自身位置定位根目录，不依赖启动目录。
- `docs/` 维护当前说明；阶段需求、计划、部署回执和过往测试结果放 `docs/archive/`，标明日期。
- `archive/` 保留原型、诊断脚本、人工维护工具和旧构建快照。它们不参与应用导入、测试发现和正常构建；目录内说明标记原有路径与适用范围。
- `schema/` 保留协议参考快照，运行时不逐文件加载这些 schema。CLI 升级后的协议能力应重新核对，不由旧 schema 推断。
- 配置、SQLite 和 CLI 自己的会话数据不随目录整理迁移；持久路径规则见配置文档。
