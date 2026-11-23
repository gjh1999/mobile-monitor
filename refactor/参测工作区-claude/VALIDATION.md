# 验证记录

日期：2026-11-23。所有测试均由工作区脚本在隔离目录中执行，未修改题目原件。

## 1. 环境

| 项目 | 值 |
|---|---|
| Windows | 11 Pro 10.0.26100 |
| Go | go1.27.4 windows/amd64 |
| Rust / Cargo | 1.100.0 / 1.100.0 |
| PowerShell | 5.1 |
| 运行目录 | `run\<用例名>`（工作区内，生成） |

## 2. 单元测试

命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-all.ps1
```

其中单元测试部分：

| 套件 | 数量 | 结果 |
|---|---:|---|
| Go：幂等提交、拉取、冲突、丢回执重试去重、事件去重、非法路径 | 5 | 全部通过 |
| Rust：公开轮次投影、重放去重/同文段保留、未完成回复、待传推导、基线匹配 | 6 | 全部通过 |

## 3. 公开验收 U1–U3

用例根目录：`run/e2e-20261123-221622`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\e2e-public.ps1
```

结果：**27/27 断言通过**（`run/e2e-20261123-221622/e2e-result.json`）。

覆盖内容：

- U1：三方健康；A/B 项目初始为空；停止后按原路径、原端口、原身份重启；重启后仍为空。
- U2：
  - 初始 `data/readings.txt` 哈希 `748a88ea...a6ada3`，两端一致；
  - A 追加 `25` 后 B 哈希 `91060fb9...df78f3`；
  - B 改名 `data/week.txt`，A 看到后删除，两端两个路径均消失；
  - `summary.py` 两端始终与原文件同哈希。
- U3：
  - 首次导入 6 条事件（accepted=6）；
  - B 的 `thread-weather/turn-01` 恰好 4 条逻辑条目，字段与顺序符合预期；
  - 再次导入 accepted=0、duplicates=6，历史逐字段不变；
  - `/replay` 报告 `tool_dispatch_attempts=0`，回放前后项目目录无变化；
  - 强制/优雅重启 B 后历史不变。

证据文件：

- `evidence/u2-initial-server-status.json`
- `evidence/u3-history-first.json`
- `evidence/u3-history-after-replay.json`
- `evidence/u3-history-after-restart.json`
- `evidence/u3-replay.json`
- `evidence/u3-server-status.json`
- `logs/*.out.log`

## 4. 故障场景 F1–F3（提交自带）

用例根目录：`run/fault-20261123-221631`

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\e2e-fault.ps1
```

结果：**31/31 断言通过**（`run/fault-20261123-221631/e2e-fault-result.json`）。

| 场景 | 验证点 | 结果 |
|---|---|---|
| F1 离线修改 vs 远端删除 | B 离线追加 `37`；A 删除已持久；B 恢复后冲突；修改副本字节哈希正确；原路径未静默恢复；副本同步到 A；重启 B 后仍可找回 | 11/11 通过 |
| F2 截断回复与重叠重放 | 4 行批次显示未完成回复；3–7 行批次去重 2 条；相同文本两条 delta 都拼接；工具结果关联；全量重放 7 条全去重；重启后历史相同；只读回放零派发、无标记文件 | 9/9 通过 |
| F3 已提交但成功回执丢失 | A 在首次重试前暂停；服务端已持久提交；2 秒内无立即重试；强杀三方后按服务端→B→A 恢复；B 文件字节正确；仅一次逻辑提交；A 队列清空；`receipt_log` 有 resolution；再次重启仍正确 | 11/11 通过 |

关键证据：

- `F1/evidence/f1-server-after-delete.json`（删除墓碑）
- `F1/evidence/f1-b-status-conflict.json`（冲突记录与副本路径）
- `F1/evidence/f1-server-oplog.json`
- `F2/evidence/f2-history-interrupted.json`（未完成回复）
- `F2/evidence/f2-history-complete.json`（最终历史）
- `F2/evidence/f2-replay.json`
- `F3/evidence/f3-a-status-receipt-lost.json`（等待回执）
- `F3/evidence/f3-server-after-commit.json`（已持久提交）
- `F3/evidence/f3-a-status-after-retry.json`（resolution）
- `F3/evidence/f3-oplog-after-retry.json`（单条 applied）

## 5. 测试中发现并修复的问题

| 问题 | 影响 | 处理 |
|---|---|---|
| `stop-all.ps1` 把 PID 0 当作存活进程（Windows 的 Idle 进程） | 在只恢复了部分角色的场景中，停止命令误报“仍需停止”并以失败退出 | 所有按 PID 操作的地方跳过 `<= 0`；重测单角色强杀/恢复通过 |
| 评审模拟比较 JSON 时依赖属性顺序 | 测试脚本误报 H2 失败（实现输出与预期逐字段一致） | 改为键排序后的规范化比较；重跑通过 |

## 6. 未验证项

- 大文件（>64 MiB）、海量小文件、长时间运行下的日志/blob 增长。
- 三个及以上设备、并发写入压力、网络分区与高延迟。
- 真实 Codex 接入、真实工具执行、账号、图形界面和公网部署。
- Linux/macOS 上的脚本行为（Go/Rust 源码本身未使用 Windows 特有 API）。
