def current(workspace):
    return {"provider": "deskbell", "workspace": workspace, "topic": "task",
            "renderer": "toast-v3", "label": "任务提醒"}


def owned(row, workspace):
    return ((row.get("provider"), row.get("workspace"), row.get("topic")) ==
            ("deskbell", workspace, "task") or
            (row.get("owner"), row.get("project"), row.get("event")) ==
            ("deskbell", workspace, "task"))


def status(config, workspace):
    matches = [r for r in config.get("subscriptions", []) if owned(r, workspace)]
    if matches == [current(workspace)]:
        return "current"
    return "outdated" if matches else "missing"


def configure(config, workspace, enabled):
    before = status(config, workspace)
    if before == ("current" if enabled else "missing"):
        return False
    rows = [r for r in config.get("subscriptions", []) if not owned(r, workspace)]
    if enabled:
        rows.append(current(workspace))
    config["subscriptions"] = rows
    return True
