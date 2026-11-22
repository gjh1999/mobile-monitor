# 澜汐 · Codex / Claude Code 桌面工作台

Windows 原生桌面软件，用于集中查看和控制本机 Codex / Claude Code 任务。澜汐在整窗轮换壁纸上以两块半透明毛玻璃展示任务和对话，提供任务接入、指令发送、暂停与继续、单次审批和自动推进。两端使用独立后台和状态文件。钉钉通知与 Stream 指令是可选通道。

源码仓库和本地目录均使用 `mobile-monitor`。[GitHub 仓库](https://github.com/gjh1999/mobile-monitor)

## 快速开始

新 Windows 电脑可直接双击从发布机复制的 `release/笔记本下载/下载澜汐最新版.cmd`，自动加入私人工作区并下载最新版 ZIP。 [新电脑安装说明](docs/PORTABLE_CHECK.md#在另一台-windows-电脑使用)

本机 `release/` 根目录保留当前稳定版 `Lanxi-R47-Windows-Private.zip` 和开发版 `Lanxi.exe`。`release/history/` 另存最近两个被替换的稳定版 ZIP，供回滚使用。 [发布目录维护](docs/RUNBOOK.md#构建-exe)

R47 支持自建项目分组和同步频率。进入本机已有的项目时先显示本地会话，约 5 秒后再排入后台同步。 [项目分组与同步](docs/PERSONAL_SYNC.md#项目分组与同步优先级)

R46 将额度和列表查询移出人工发送路径，增加会话摘要存档与跨机删除。 [桌面操作](docs/DESKTOP.md#压缩并删除-codex-会话)

R44 在 Codex 连接恢复后发送可确认尚未写入的指令。Codex 正文和执行札记各自保留，连续命令也可回看用户输入。 [桌面说明](docs/DESKTOP.md)

R45 支持澜汐退出后的 VS Code 审批弹窗。升级后从托盘更新「VS Code 审批提醒」，重启 VS Code 并重新信任通知 Hook。 [启用步骤](docs/DESKTOP.md#审批窗口)

R42 关闭主窗口缩到托盘后自动交还任务控制权。在托盘菜单查看进度，等交还完成后可从另一台电脑执舟。 [个人版项目占用](docs/PROJECT_CONTROL.md#个人版项目占用)

R41 通过应用升级后，可从 Windows 开始菜单重新打开「澜汐」。Windows 托盘可启用 VS Code 中的 Codex 审批提醒。 [升级与重开](docs/PORTABLE_CHECK.md#升级与首次配置) · [审批提醒](docs/DESKTOP.md#审批窗口)

私人 Windows 便携版首次使用时选择项目母目录，点「连接云服务器」加入私人工作区。来源机上的普通项目可自动初始化 Git 并接入同名私有 GitHub 仓库。已完成的长会话可分块接续。R40 的「合流」显示当前轮次进度，「执舟」显示任务占用原因，托盘模式仍会弹出已接入的审批。 [安装、升级与验收](docs/PORTABLE_CHECK.md)

R35 支持 GitHub 与服务器双份代码同步。从顶栏或托盘的「跨机接续」可选择并下载服务器 ZIP。 [自动同步、合流与下载](docs/PERSONAL_SYNC.md#代码的服务器备份)

R35 修正了母目录下的项目分组和本机 Codex 历史显示。「合流」逐项列出 Git 项目的本机路径和同步状态。 [项目扫描及同步排查](docs/PERSONAL_SYNC.md#手动同步)

- 从托盘「跨机接续」邀请或配对设备。项目按母目录下的相对路径对齐，代码经 GitHub 同步，项目记忆和已完成的 Codex 会话经协调服务接续。「吉光片羽」的随记、待办、归档及手动分组也会同步。 [个人设备自动接续](docs/PERSONAL_SYNC.md)
- R35 的「吉光片羽」可用左栏圆角滑块切换「拾得片羽」「待理诸事」和「每日记录」。随记和待办按日期合成一份，未完成的待办继续留在待办列表。 [吉光片羽使用说明](docs/DESKTOP.md#吉光片羽)
- R35 顶部「设置 → 壁纸管理」可查看当前壁纸、导入本地图片、移出或恢复轮换。 [壁纸管理使用说明](docs/DESKTOP.md#壁纸管理)
- R36 会在同一个桌面窗口汇总本机和同工作区的待审批请求，可同意所选或全部可核对项，并查看逐项回执。左栏显示命令预览，缺少操作详情时跳过同意。来源设备在放行前复核项目控制权。 [审批窗口](docs/DESKTOP.md#审批窗口) · [R36 发布与验收](docs/DELIVERY_STATUS.md#r36-02)
- 同一项目可以在设备间交还和接入，也可通过 VS Code 或 PyCharm 插件上报 IDE 占用。达到 100 MiB 的文件可以查看清单，按需借用和回传。 [项目占用](docs/PROJECT_CONTROL.md#个人版项目占用) · [借用与回传](docs/PORTABLE_CHECK.md#跨机借用与回传大文件)
- 「灯下对谈」支持 Markdown 和 Codex 终端历史。澜汐接入的 Codex 会话会流式显示正文，也会在「执行札记」显示命令进度。R37 测试窗口提供服务器测试、本机自检和项目会话诊断。 [桌面使用说明](docs/DESKTOP.md) · [运行验收](docs/PORTABLE_CHECK.md#运行验收)

关闭窗口后程序留在系统托盘，从托盘选择「退出程序」即可停止后台。独立 Web 工作台的运行与 iPhone 局域网调试见 [Web 工作台说明](docs/WEB.md)。

在 PowerShell 中从项目根目录启动：

```powershell
& './.venv/Scripts/python.exe' './desktop_app.py'
```

`desktop_app.py` 是原生桌面入口，`monitor.py` 在同一进程管理后台。构建、登录启动与故障检查见 [运行与维护](docs/RUNBOOK.md)。

R36 支持窗口顶部菜单。将鼠标移至窗口内的顶边，点左上角「澜汐」即可使用系统托盘的全部命令。 [桌面使用说明](docs/DESKTOP.md#窗口与后台)

## 文档导航

负责人和工程师在[项目工单](docs/DELIVERY_STATUS.md#项目工单)中派工、接单、提问和交付验收结果。

| 要做的事 | 文档 |
|---|---|
| 查看主文档与当前功能入口 | [跨设备开发工作台方案](docs/跨设备开发工作台方案.html#current) |
| 查找全部说明 | [文档索引](docs/README.md) |
| 查看最新交付、候选版和剩余验收 | [跨设备方案交付进度](docs/DELIVERY_STATUS.md) |
| 在云服务器运行设备配对和记忆同步 | [澜汐云协调服务](docs/CLOUD_COORDINATOR.md) |
| 启动、安装依赖、打包、测试、登录启动 | [运行与维护](docs/RUNBOOK.md) |
| 配置键、默认值、环境变量和数据位置 | [配置说明](docs/CONFIGURATION.md) |
| 了解模块职责和后台处理流程 | [架构与文件说明](docs/ARCHITECTURE.md) |
| 预备跨机 Git 工作区、校验 LFS 对象和续接 Codex 线程 | [代码与会话交接](docs/HANDOFF.md) |
| 登记开发机、协调后台写入权及提交硬件计算作业 | [跨设备项目控制](docs/PROJECT_CONTROL.md) |
| 使用桌面窗口、托盘及 EXE | [桌面使用说明](docs/DESKTOP.md) |
| 切换客户端、理解控制与自动推进边界 | [双客户端使用说明](docs/MULTI_CLIENT.md) |
| 配置 Webhook 通知与 Stream 指令 | [钉钉配置](docs/DINGTALK_SETUP.md) |
| 查询过去的需求、计划和验收记录 | [历史文档](docs/archive/README.md) |

## 文件布局

```text
mobile-monitor/
├── README.md                   # 项目入口
├── desktop_app.py              # 桌面启动入口
├── desktop_qt.py               # 完整 Qt 双客户端工作台与托盘
├── desktop_glass.py            # 壁纸轮换、毛玻璃层和客户端滑块
├── desktop_theme.py            # 蓝白主题、字体和消息排版
├── desktop_win32.py            # Win32 回退界面
├── monitor.py                  # 进程内双客户端后台与状态编排
├── *.py                        # RPC、任务控制、调度、审批、通知和消息记录
├── local-config.example.json   # 无凭据的配置示例
├── requirements-*.txt          # 桌面、构建和可选 Stream 依赖
├── docs/                       # 当前使用与维护文档
│   └── archive/                # 历史需求、计划与验收记录
├── scripts/                    # 构建、登录启动、通知检查
├── tests/                      # Python 回归与 Qt smoke
├── schema/                     # Codex app-server 协议参考
├── archive/                    # 原型、诊断、人工维护工具及旧构建快照
├── build-exe/                  # 本机构建输出（忽略）
└── release/                    # EXE 及其独立运行数据（忽略）
```

源码运行的配置、SQLite 数据库和日志位于项目根目录。R21 起，便携 EXE 使用 `%LOCALAPPDATA%\Lanxi\data`，换安装目录也会读回原数据。`.venv/` 和这些本机文件均由 `.gitignore` 排除。 [配置说明](docs/CONFIGURATION.md) · [归档索引](archive/README.md)

## 功能边界

- 发现任务、只读关注、取得控制权和启用自动调度是不同状态。Codex 自动续跑需要本连接拥有控制权、实时空闲、有效目标且无待审批。额度耗尽时保存刷新时间并等待，到点重新查询；服务端确认额度恢复后继续原任务。手动暂停、关闭自动推进或移出会取消自动恢复。
- Claude Code 支持历史查看、手动消息、暂停、继续和单次工具审批。每小时自动推进默认关闭，显式开启后仅在可控制、空闲且无审批或执行错误时发送；任务完成后需关闭。官方剩余额度显示未知。
- 自动审批只匹配显式配置的 Codex 任务、目录与完整命令；其他请求由人工核对并单次处理。
- 钉钉 Webhook 用于出站通知，Stream 指令另需应用凭据和身份白名单。
- 离线测试与界面检查不代替真实模型收发、工具审批往返、系统通知展示、钉钉联网或 Windows 重新登录验收。历史结果仅适用于记录当时的范围。

## 验证

```powershell
& './.venv/Scripts/python.exe' -m unittest discover -s tests -t . -p 'test_*.py' -v
& './.venv/Scripts/python.exe' './tests/smoke_desktop.py' --output "$env:TEMP/monitor-dual-client-smoke"
```

Qt smoke 使用虚构任务和替代后台，点击生产界面并生成截图。验证范围及操作说明见 [运行与维护](docs/RUNBOOK.md)。
