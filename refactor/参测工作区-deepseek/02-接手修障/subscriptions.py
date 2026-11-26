def current(workspace):
    return {"provider": "deskbell", "workspace": workspace, "topic": "task",
            "renderer": "toast-v3", "label": "任务提醒"}


def _identity(row):
    """返回订阅的逻辑标识 (provider, workspace, topic)。

    兼容三版字段命名：
    - 第一版 owner/project/event；
    - 第二、三版 provider/workspace/topic。
    无法识别的第三方订阅返回 None。
    """
    if isinstance(row, dict):
        if "provider" in row and "workspace" in row and "topic" in row:
            return (row["provider"], row["workspace"], row["topic"])
        if "owner" in row and "project" in row and "event" in row:
            return (row["owner"], row["project"], row["event"])
    return None


def _key(workspace):
    wanted = current(workspace)
    return (wanted["provider"], wanted["workspace"], wanted["topic"])


def status(config, workspace):
    rows = config.get("subscriptions", [])
    wanted = current(workspace)
    key = _key(workspace)
    seen_current = False
    for row in rows:
        if _identity(row) == key:
            if row == wanted:
                seen_current = True
            else:
                # 存在旧版记录（即使同时也有新版），报告 outdated
                return "outdated"
    return "current" if seen_current else "missing"


def configure(config, workspace, enabled):
    rows = config.setdefault("subscriptions", [])
    wanted = current(workspace)
    key = _key(workspace)

    if enabled:
        matched = [i for i, row in enumerate(rows) if _identity(row) == key]
        if not matched:
            rows.append(wanted)
            return True
        # 已经是唯一且当前版本的订阅：无需改动，保留文件原字节
        if len(matched) == 1 and rows[matched[0]] == wanted:
            return False
        # 迁移旧版或去掉重复：在首个匹配位置写入当前版本，删除其余同标识行
        rows[matched[0]] = wanted
        for i in reversed(matched[1:]):
            del rows[i]
        return True

    # 停用：删除所有同标识行（含旧版格式），保留其他订阅及其顺序
    kept = []
    removed = False
    for row in rows:
        if _identity(row) == key:
            removed = True
        else:
            kept.append(row)
    if removed:
        rows[:] = kept
    return removed
