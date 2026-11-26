# 设计说明 · 文件与消息同步引擎

## 架构

```
A 项目目录 ──scan/diff──▶ Rust 客户端 A ──HTTP /push /pull──▶ Go 服务端（持久状态）
                                                              │  commits.jsonl + blobs/ + conflicts.jsonl
B 项目目录 ──scan/diff──▶ Rust 客户端 B ──HTTP /push /pull──▶ 同一服务端
```

客户端只与服务端通信；服务端是唯一协调点与权威状态。三者状态目录互不重叠（`run/project-*`、`run/state-*`、`run/server-state`）。

## 服务端协议（本机回环 HTTP）

- `POST /push`　body `{client, files[], events[]}`；files 每条 `{op_id,path,kind(put|delete),blob,base,content(base64)}`。服务端校验 `blob == FNV-1a64(content)`，按 `op_id` 去重，追加一个“逻辑提交”到 `commits.jsonl` 并 `fsync`，返回回执 `{seq, results[{op_id,status}]}`（status ∈ applied|conflict|duplicate）。
- `GET /pull?after=N`　返回 `seq>N` 的提交里的事件、当前文件权威清单 `manifest{path→blob}`、`blobs{blob→base64}`、`deleted{path→base}`、冲突列表。
- `GET /fault?kind=drop|crash`　故障注入（见下）。
- `GET /stats`　`head/commits/files/conflicts/events` 计数。

## 文件语义与冲突

- **内容寻址**：blob id = 内容 FNV-1a64（16 位 hex），两端同算法。新增/修改 = `put(path, blob, base)`，删除 = `delete(path, base)`，`base` 是客户端最后一次同步到该路径的 blob（新文件为空）。
- **快进**：`base == 服务端当前 blob` 时直接推进，无冲突。
- **分叉**（双方改同一基线）：第二个提交 `base != 当前` → 记为 `fork` 冲突，保留服务端当前版本为规范路径，提交方本地修改保存为 `<path>.conflict-<hash8>`。
- **删除 vs 修改**：删除方先到 → 墓碑；修改方后到且 `base == 墓碑 base` → `delete_intent` 冲突，保留删除意图（规范路径消失）+ 修改内容（冲突副本）。修改方先到、删除方后到同理。
- **重命名**：建模为“旧路径 delete + 新路径 put（同 blob）”，两端自然收敛，无需专名检测。
- 冲突副本是普通项目文件，随下次同步传播到对端，保证双方都能读到两版内容。

## 消息（事件）语义

事件是本题合成格式，客户端与服务端均**按 `event_id` 去重**：相同 event_id 重放被跳过；文本相同但 event_id 不同的两条事件各自贡献一次。客户端把事件存到 `run/state-*/events.jsonl`，投影按 `(thread_id, turn_id)` 分组、轮次内按 `seq` 排序：

- `user.message` → 用户消息原文。
- `tool.call` / `tool.result` → 工具名+结构化参数 / 结果，按 `call_id` 关联。
- `assistant.delta` → 同一 `item_id` 按 `seq` 拼接；`assistant.done` 标记完成。

**只读回放**：`history` 命令只读 `events.jsonl` 渲染，从不执行工具；工具调用与结果仅作为历史数据保存。校验“零派发”可观察：导入含 `sample.mean` 的事件后运行 `history`，输出里只有调用记录、没有任何副作用执行（见 VALIDATION.md）。

## 回执丢失与崩溃恢复

- **drop**：服务端在 `commits.jsonl` `fsync` 之后、发送回执之前关闭连接（`http.Hijack`）。客户端 `push` 失败，不更新基线，操作保留为待传；重试产生相同 `op_id`，服务端按 `op_id` 去重，`commits` 计数不变。
- **crash**：服务端 `fsync` 后立即 `os.Exit(3)`（不发送回执）。重启后 `load()` 重放 `commits.jsonl` 重建 `opSeen/files/head`，原提交可找回；客户端重试同样被去重，最终只有一次逻辑提交。

## 持久化

- 服务端：`commits.jsonl`（追加+fsync）、`blobs/<blob_id>`（内容）、`conflicts.jsonl`。重启重放日志重建内存索引。
- 客户端：`baseline.tsv`（path→blob，最后一次同步）、`seq.txt`（最后应用的服务端 seq）、`events.jsonl`（去重事件）、`online.txt`、`status.txt`/`daemon.log`。

## 已知限制

- blob 用 FNV-1a64 而非加密哈希：内容比较足以去重/校验，碰撞概率对评测规模可忽略；若要跨机器或防篡改可换成 SHA-256（两端同改）。
- 客户端每轮全量扫描项目目录（`scan_project`），未做增量；大目录/大文件下内存与延迟会上升，留待实测。
- 服务端 `/pull` 每次返回 manifest 全部 blob 内容，规模小（评测）时无碍；大仓库需按需取 blob。
- 冲突副本采用固定命名后缀，同一路径反复冲突会覆盖前一个副本（保留最新）。
- 未接入真实 Codex、账号、公网与图形界面（按题面留给后续评测）。
