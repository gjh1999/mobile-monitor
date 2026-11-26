# 匿名导出补丁评审 · REVIEW

## 合入决定

**不建议合入。** 该分页改造引入了未归档行的静默丢失（P0，违反“每条恰好一次”的核心约定），且去掉了原子发布（P1，失败时破坏上次成功文件）。需修复 P0、P1 后重审。P2 为 P0 在归档行参与分页时的具体表现，一并修复。

已核对且正确的行为：排序键 `(updated, id)`、`ensure_ascii=False` 输出、空结果生成空文件、公开测试 `test_visible_rows`（updated 全不同的用例）通过。以下问题均为公开测试未覆盖的边界。

---

## 问题 1（P0 · 数据丢失）分页游标用严格 `updated > cursor`，同时间戳记录跨页被丢弃

- **位置**：`proposed/exporter.py` `pages()` 第 9 行 `page = [r for r in ordered if cursor is None or r["updated"] > cursor][:page_size]`，以及第 13 行 `cursor = page[-1]["updated"]`。
- **触发条件**：`page_size` 小于具有相同 `updated` 值的行数（约定明确“同一时间多条更新是正常业务”）。
- **期望**：每条未归档行恰好输出一次。
- **实得**：页边界之后、`updated == cursor` 的未归档行被静默跳过（不报错、不落盘）。
- **复核方法**：
  ```python
  from proposed.exporter import export
  rows = [{"updated":1,"id":"a","archived":False},
          {"updated":1,"id":"b","archived":False},
          {"updated":1,"id":"c","archived":False}]
  export(rows, "out.jsonl", page_size=2)
  # 实得 2 行（a、b）；期望 3 行（a、b、c），c 被丢
  ```

---

## 问题 2（P1 · 破坏上次成功文件）去掉临时文件 + 原子替换，失败时目标被截断

- **位置**：`proposed/exporter.py` `export()` 第 18 行 `with destination.open("w", encoding="utf-8") as stream:`（直接以写模式打开目标并立即截断），对比 `base/exporter.py` 使用 `.part` 临时文件 + `temporary.replace(destination)`。
- **触发条件**：写出过程中某行序列化/编码失败（如含不可 JSON 序列化的值）。
- **期望**：向调用者报错并保留上次成功的文件（约定：“整次导出成功后才发布新文件；读取或编码失败时保留上次成功的文件”）。
- **实得**：异常抛出前目标已被截断并写入部分行，上次成功文件被破坏。
- **复核方法**：
  ```python
  from proposed.exporter import export
  open("out.jsonl","w").write("PREVIOUS-GOOD\n")
  rows = [{"updated":1,"id":"a","archived":False},
          {"updated":2,"id":"b","archived":False,"bad":object()}]
  export(rows, "out.jsonl")   # 抛 TypeError
  # proposed 实得 out.jsonl = 仅第一行（PREVIOUS-GOOD 被破坏）
  # base 同输入抛 TypeError 后 out.jsonl 仍为 "PREVIOUS-GOOD\n"
  ```

---

## 问题 3（P2 · 数据丢失）归档行消耗分页配额并推进游标，空页被跳过仍丢可见行

- **位置**：`proposed/exporter.py` `pages()` 第 9–13 行（归档行计入 `page_size` 且 `cursor` 取整页最后一行，不区分是否归档）+ `export()` 第 20–22 行 `if not visible: continue`。
- **触发条件**：某页完全由归档行组成（或归档行落在页边界），且这些归档行与后续可见行的 `updated` 相同。
- **期望**：归档行不输出，但不影响其余未归档行的完整输出。
- **实得**：空页被 `continue` 跳过，但游标已推进到该归档行的 `updated`，下一轮 `updated > cursor` 过滤掉与之同值的可见行，造成静默丢失。
- **复核方法**：
  ```python
  from proposed.exporter import export
  rows = [{"updated":1,"id":"a","archived":True},
          {"updated":1,"id":"b","archived":False},
          {"updated":1,"id":"c","archived":False}]
  export(rows, "out.jsonl", page_size=1)
  # 实得空文件；期望输出 b、c 两行
  ```

---

## 修复建议（供作者参考）

- 分页游标改为“上次取到的最后一个 `(updated, id)` 组合”继续（`> (updated, id)` 或记录偏移），使同 `updated` 记录不会因分页丢失。
- 归档行应在排序后、分页前先过滤，再分页（或至少不参与 `page_size` 配额与游标推进）。
- 恢复 base 的 `.part` 临时文件 + `replace` 原子发布；失败时清理临时文件并保留原目标。
