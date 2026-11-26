# 实测说明 · 验证范围与结果

构建环境：Windows 11，Go 1.27.4，Rust 1.100.0（GNU 工具链），本机回环。以下为首次交付的实测记录。

## 一键回归（公开用例）

`powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1` —— 14/14 通过：

```
[PASS] U1 server 健康
[PASS] U1 三个进程存活
[PASS] U1 停止后原路径重启
[PASS] U2 初始副本字节匹配  B=748a88ea...a6ada3
[PASS] U2 A 追加后 B 字节匹配  B=91060fb9...9fd78f3
[PASS] U2 B 改名后 A 路径一致
[PASS] U2 删除后两端路径一致
[PASS] U2 summary.py 未被误动 (A)
[PASS] U2 summary.py 未被误动 (B)
[PASS] U3 用户消息 / 工具调用 / 工具结果 / 助手回复
[PASS] U3 重放去重（条目数不变）
===== 共 14 项检查，0 失败 =====
```

## 额外实测（故障与冲突）

| 场景 | 操作 | 观察 | 结论 |
|---|---|---|---|
| 回执丢失 | `/fault?kind=drop` 后 A 推 `note.txt` | push 报错、无回执；`/stats` `commits` 从 5→6（已持久）；重试 `commits` 仍 6 | 已持久但无回执，重试 op_id 去重，仅一次提交 |
| 崩溃恢复 | `/fault?kind=crash` 后 A 推 `crash.txt`，服务端退出 | 重启后 `commits`=7 找回原提交；重试 `commits` 仍 7；B 拉到 `crash.txt` | 崩溃后原提交可找回，重试去重 |
| 分叉冲突 | A、B 同时改同一基线 `shared.txt` | A 快进；B `conflict(fork)`，`shared.txt`=A 版，`shared.txt.conflict-*`=B 版 | 双方内容均可读 |
| 删除 vs 修改 | A 删 `shared.txt`、B 改同基线 | 两端规范路径均消失，B 修改内容存 `*.conflict-*` | 删除意图 + 可读修改内容并存 |
| 事件重放 | 同一 JSONL 二次导入 A | 6 条全部 `skip duplicate`，B 历史仍 4 条 | event_id 去重 |
| 只读回放 | `history` 导入含 `sample.mean` 事件后 | 输出仅含 `[tool.call]`/`[tool.result]` 记录，无函数执行 | 零派发 |

## 已知未覆盖

- 未在真实 Codex 目录、大文件/大目录上压测（扫描与拉取成本见 DESIGN.md 已知限制）。
- 未测并发双客户端同时 push 的极端交错（服务端串行加锁，逻辑安全，但未做并发压力用例）。
- 冲突副本固定后缀，同一路径多次冲突仅保留最新副本。
