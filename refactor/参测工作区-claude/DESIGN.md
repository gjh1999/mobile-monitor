# 设计说明

## 1. 目标与非目标

目标：在本机回环网络上，用 Go 协调服务和两个 Rust 客户端，演示

1. 版本化文件同步与冲突保留；
2. 离线队列、暂停/恢复和重启恢复；
3. JSONL 事件去重、历史投影和只读回放；
4. 在“服务端已持久提交、发起方未收到成功回执”的精确窗口注入一次故障，并证明重试不会产生第二份逻辑提交。

非目标：真实 Codex 接入、工具执行、账号/公网部署、图形界面（题目明确留给后续评测）。

## 2. 进程与数据

```
+----------------------------- 127.0.0.1:7801 ------------------------------+
| coordinator (Go)                                                          |
|  state.json + blobs/ : files/versions/oplog/conflicts/events/applied_ops  |
+-----------------------------+--------------------------------------------+
                              | HTTP/JSON
              +---------------+---------------+
              |                               |
   client A (Rust, :7802)           client B (Rust, :7803)
   project-a/ + state-a/            project-b/ + state-b/
```

- 客户端之间不直接通信，只通过协调服务交换文件版本和事件。
- 每个角色有独立状态目录；重启复用身份、端口和路径。
- 服务端每次状态变更立即原子写入 `state.json`，文件内容写入 `blobs/<sha256>`。

## 3. 文件协议

### 3.1 服务端状态

- `files[path] = {version, deleted, hash, prior_hash, op_id, device_id, updated_at}`
- `next_version`：单调整数，每次成功提交 +1。
- `applied_ops[op_id] = {version, result, path, device_id, base_version, at}`：永久去重表。
- `op_log[]`：所有应用和冲突尝试，用于取证。
- `conflicts[]`：服务端观察到的并发提交冲突。
- 删除是墓碑条目：`deleted=true`、保留 `prior_hash`，不会物理删除版本记录。
- 文件内容以 SHA-256 为文件名存放，先写 blob（fsync），再写状态（临时文件 + fsync + rename）。

### 3.2 推送

`POST /api/push`

```json
{
  "device_id": "B",
  "op": {
    "op_id": "B-<确定性哈希>",
    "path": "data/readings.txt",
    "base_version": 1,
    "deleted": false,
    "hash": "<sha256>",
    "content_b64": "<base64>"
  }
}
```

服务端处理顺序：

1. `op_id` 已存在 → `already_applied`，返回原版本，不新增提交；
2. `base_version != 当前版本` → 记录冲突与冲突尝试，返回 `conflict` 和当前服务端条目（活动文件附 `content_b64`）；
3. 校验路径、base64 和内容哈希；写 blob；分配版本；更新条目、去重表、操作日志；持久化；返回 `applied`。

如果已武装回执丢弃故障，则在**持久化之后**关闭连接、不写 HTTP 响应，并持久化故障已触发的日志。

### 3.3 拉取

`GET /api/pull?device_id=A&since=<last_seen_version>`

返回所有 `version > since` 的路径**最新**条目、`content_b64`（活动文件）和 `server_seq`。客户端把 `server_seq` 记为新的拉取水位。

### 3.4 客户端基线

`client-state.json`：

- `base[path] = {version, deleted, hash}`：最后一次与服务端确认的基线；
- `last_seen_version`：已拉取到的服务端版本；
- `inflight` / `awaiting`：正在发送 / 成功回执丢失的操作；
- `conflicts[]`、`receipt_log[]`、`imports[]`、`event_cache{}`。

本地变更不单独建队列，而是由“磁盘现状 vs 基线”实时推导：

| 基线 | 磁盘 | 待传操作 |
|---|---|---|
| 无 | 有文件 | 新增，`base_version=0` |
| 有、未删除 | 哈希不同 | 修改，`base_version=基线版本` |
| 有、未删除 | 文件不存在 | 删除，`base_version=基线版本` |
| 墓碑 | 无文件 | 无 |
| 墓碑 | 有文件 | 重新新增，`base_version=墓碑版本` |

好处是进程崩溃/强杀后无需修复队列：磁盘和持久基线已经足够恢复待传状态。

### 3.5 同步顺序

1. 扫描项目目录，计算哈希；
2. 按路径排序生成本机待传操作；
3. 逐个推送；每个操作发送前持久化 `inflight`；
4. 收到 ack 后更新基线并持久化；
5. 收到冲突后按第 4 节保留内容并更新基线；
6. 若本轮没有冲突/丢回执，拉取服务端新版本并应用；
7. 拉取/推送完成后更新 `last_seen_version`。

先推送后拉取，使本机未提交修改在遇到远端变化时能被识别为冲突，而不是被静默覆盖。

## 4. 冲突规则

所有冲突都至少在客户端 `conflicts[]` 留一条记录；服务端也记录冲突尝试。

| 情况 | 处理 |
|---|---|
| 远端删除 vs 本机修改 | 修改字节写入 `<path>.conflict-<device>-v<server_version>`；原名删除；记录 `remote-deleted-vs-local-modified`；副本随后作为新文件同步 |
| 双方修改 | 本机字节写入冲突副本；原名采用服务端内容；记录 `both-modified` |
| 本机删除 vs 远端修改 | 远端内容恢复到原名；记录 `local-deleted-remote-modified` |
| 双方删除 | 记录 `both-deleted`，保持墓碑 |
| 拉取时发现本机仍有未提交修改 | 用相同规则保留本机副本，再应用远端状态 |

冲突副本名包含设备和服务端版本，且写入前检查同名文件内容；同内容复用，不同内容追加哈希前缀/序号，避免覆盖。

## 5. 回执丢失与幂等

### 5.1 窗口

服务端在应用操作并 `fsync` 状态后，如果故障已武装：

1. 解除故障标志并写入 `fault_log`；
2. 通过 HTTP `Hijack` 取得连接并直接关闭；
3. 不发送 `applied` 响应。

客户端已经把操作写入 `inflight` 后发出请求；读到空响应/连接断开时归类为 `AfterSend`。

### 5.2 客户端行为

- `AfterSend`：把 `inflight` 移入 `awaiting`，持久化，并**停止重试**。日志打印 `RECEIPT_LOST ... retry blocked`。
- 状态中可见 `awaiting_confirmation`、`retry_blocked=true`、`pending_count>=1`。
- 放行方式：
  - `/retry`：把待重试记录写入 `receipt_log`，清除 `awaiting`，立即触发同步；
  - 进程重启：默认自动放行（`receipt_log.reason=restart-auto-retry`）；加 `--no-auto-retry` 则保持阻塞，等待 `/retry`。
- 重试使用相同的确定性 `op_id`：`sha256(device|path|base_version|put/delete|hash)` 截断。
- 服务端命中 `applied_ops` 后返回 `already_applied` 与原版本，**不新增提交**；客户端更新基线并在 `receipt_log` 写 `resolution`。

### 5.3 证据

- 服务端：`applied_ops` 计数、`files[path].version`、`op_log` 单条 `applied`、`fault_log` 的 arm/fired 记录。
- 客户端：`awaiting_confirmation`（故障时）、`receipt_log`（重试后，含 `dropped_at`/`resumed_at`/`resolution`）。
- 队列：`queue_empty=true` 表示待传及待重试均已清空。

## 6. 事件与历史

### 6.1 存储

- `POST /api/events` 接收事件数组，按 `event_id` 去重；重复事件计入 `duplicates`，不覆盖原内容。
- 事件原文保存在服务端 `events[event_id]`，持久化后返回 `accepted/duplicates/total`。
- `GET /api/events?thread_id=` 按 `(seq, event_id)` 排序返回原始事件。
- `GET /api/threads` 汇总线程、轮次、条目数。

### 6.2 投影规则

客户端 `projection::project` 是纯只读函数：

- 先按 `event_id` 去重，再按 `seq` 排序；
- 按 `thread_id` 分组，再按 `turn_id` 分组；
- 条目按首次出现的 `seq` 排序；
- `user.message`：一条用户消息；
- `tool.call`：保留 `call_id`、`name`、`arguments`；
- `tool.result`：保留 `call_id`、`result`，与调用通过 ID 关联展示；
- `assistant.delta`：同一 `item_id` 按顺序拼接文本；不同事件即使文本相同也各拼接一次；
- `assistant.done`：把状态置为 `completed`，保留已有文本；
- 未识别类型原样透传，避免丢数据。

### 6.3 只读回放

`GET /replay?thread_id=&turn_id=` 返回：

```json
{
  "history": { "...": "..." },
  "replay_mode": "read-only-projection",
  "tool_dispatch_attempts": 0,
  "tool_call_items_observed": 1
}
```

客户端和协调服务都没有工具执行器；事件导入路径只做解析、上传、缓存，历史路径只做 JSON 扫描和拼接。因此工具调用只可能被“看见”，不会被派发。

## 7. 暂停/恢复

- `/pause` 设置持久标志；同步循环继续扫描本地目录、计算待传，但跳过所有网络请求。
- `/resume` 清除标志并立即触发同步。
- 暂停期间提交的导入请求进入 `pending_imports`，恢复后执行。
- 进程被强杀后，暂停标志仍在状态文件中，重启保持离线，直到显式恢复。

## 8. 持久化与崩溃恢复

- 协调服务：每次提交/冲突/事件批次/故障状态变化后，`state.json` 通过临时文件 + fsync + rename 原子替换；blob 先落盘再引用。
- 客户端：状态文件同样原子替换；项目文件写入使用同目录临时文件 + rename。
- 强杀任意进程后：
  - 已确认基线、拉取水位、冲突、`awaiting`、事件缓存均在磁盘；
  - 服务端提交与去重表在磁盘；
  - 项目目录中的用户内容不会被删除（除冲突解决时按规则保留到副本后删除原名，或应用已确认的远端删除）。
- 启动时如果发现 `inflight` 但无响应，按“回执可能丢失”处理；默认放行一次重试，服务端去重保证安全。

## 9. 代码地图

| 文件 | 职责 |
|---|---|
| `server/main.go` | 状态模型、提交/拉取/事件/故障注入/状态与日志接口 |
| `server/httputil.go` | JSON 响应与请求解析 |
| `server/server_test.go` | 幂等、冲突、丢回执、事件去重、路径校验 |
| `client/src/main.rs` | 参数、启动恢复、同步循环 |
| `client/src/sync.rs` | 扫描差分、推送/拉取、冲突保留、导入、状态接口 |
| `client/src/projection.rs` | 纯只读历史投影与工具调用计数 |
| `client/src/control.rs` | 本地控制 HTTP 服务 |
| `client/src/http.rs` | 最小 HTTP/1.1 JSON 客户端，区分连接失败/已发送后失败 |
| `client/src/state.rs` | 客户端持久状态与回执日志 |
| `client/src/util.rs` | 哈希、base64、原子写、扫描、URL 编码、冲突路径 |
| `scripts/*.ps1` | 构建、三进程管理、单进程恢复、控制、公开/故障端到端测试 |

## 10. 安全与边界

- 仅绑定 `127.0.0.1`，无认证/TLS；不得直接暴露公网。
- 服务端拒绝绝对路径、盘符、`..` 路径片段。
- 请求体上限 64 MiB；文件整体读入内存。
- 没有工具执行入口；只读投影不产生文件副作用。
- 演示规模假设：单协调服务、两个设备、小项目、单机回环。
