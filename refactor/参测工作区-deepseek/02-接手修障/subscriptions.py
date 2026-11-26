def current(workspace):
    return {"provider": "deskbell", "workspace": workspace, "topic": "task",
            "renderer": "toast-v3", "label": "任务提醒"}


def status(config, workspace):
    rows = config.get("subscriptions", [])
    return "current" if current(workspace) in rows else "missing"


def configure(config, workspace, enabled):
    rows = config.setdefault("subscriptions", [])
    wanted = current(workspace)
    if enabled and wanted not in rows:
        rows.append(wanted)
        return True
    if not enabled and wanted in rows:
        rows.remove(wanted)
        return True
    return False
