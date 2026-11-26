# DeskBell 提醒管理器

配置文件由多个工作区和第三方插件共享。订阅按数组次序分发，用户也可以取相同的显示名称。DeskBell 管理自己在指定工作区的订阅；其他工作区、第三方订阅、自定义字段及它们的相对顺序均应保留。配置升级沿用工作区原有文件。

在本目录执行 `python -m unittest discover -s tests`。命令格式为 `python deskbell.py 配置路径 工作区 操作`，操作可选 enable、disable、status。配置不存在时可以直接启用。启用后该工作区收到一次当前版本提醒，停用后收到零次；重复执行已经生效的操作应保留文件原字节。status 输出 current、outdated 或 missing，仅查询状态。

fixtures/upgrade.json 是从旧版升级前导出的合成配置。工作区使用区分大小写的逻辑标识。
