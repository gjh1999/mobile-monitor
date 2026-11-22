# 运行与维护

[文档索引](README.md) · [配置说明](CONFIGURATION.md)

以下命令使用 PowerShell，均从 `mobile-monitor` 项目根目录执行。

## 环境准备

后台通过本机 CLI 工作：Codex 使用 `codex app-server --stdio`，Claude Code 使用 CLI 的 `stream-json` 通道。需要使用哪一端，就先安装并登录对应 CLI；登录和模型配置由 CLI 自己管理。

已有 `.venv` 时可直接使用。首次创建环境：

```powershell
py -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r requirements-desktop.txt
```

| 文件 | 用途 |
|---|---|
| `requirements-desktop.txt` | 完整 Qt 桌面界面及 Qt smoke |
| `requirements-build.txt` | Qt 和 PyInstaller，构建 Windows EXE |
| `requirements-stream.txt` | 可选的钉钉 Stream SDK |
| `requirements-lock.txt` | 既有 Stream 环境的锁定依赖快照，不含 Qt / PyInstaller |

没有配置 Stream 就不会导入该 SDK。首次配置可从 [示例文件](../local-config.example.json) 复制；以下命令只在文件不存在时创建，保留已有私密配置：

```powershell
if (-not (Test-Path './local-config.json')) {
    Copy-Item './local-config.example.json' './local-config.json'
}
```

示例中的受管任务默认关闭。配置含义和路径规则见 [配置说明](CONFIGURATION.md)。

## 启动与停止

源码启动的正式入口：

```powershell
& './.venv/Scripts/python.exe' './desktop_app.py'
```

双击 `release/Lanxi.exe` 打开原生桌面工作台。关闭窗口会缩到托盘，从托盘选择“退出程序”停止后台。源码环境缺少 Qt 时可以使用功能较少的 Tk 或 Win32 回退界面。

桌面进程通过内部模块 `monitor.py` 启动两个客户端的后台工作线程。该模块不提供独立启动服务。`desktop_qt_v2.py` 是保留的兼容转发，日常使用 `desktop_app.py`。

源码运行和 EXE 运行的数据位置不同，见 [桌面说明](DESKTOP.md)。避免同时运行不同版本并控制相同任务；更新前先从托盘退出旧实例。

## 构建 EXE

「代码review以及发版」是项目负责人，也是唯一拥有正式发版权限的角色。以下为本地构建命令，候选提交由项目负责人审核确认。 [项目发布流程](跨设备开发工作台方案.html#personal-release)

```powershell
& './.venv/Scripts/python.exe' -m pip install -r requirements-build.txt
& './scripts/build-exe.ps1'
```

本机 `release/` 根目录保留当前稳定版 ZIP 和开发版 `Lanxi.exe`。稳定版取自私人更新服务已发布并核过摘要的包，开发版由待验证源码构建。目前稳定版为 R47，本机开发版基于 `a9bc851`，使用 R36 版本号。历史稳定版另存于 `release/history/`，最多两个版本，现已保留 R45、R46。下载与配对工具、服务器部署包、本机数据和验收记录另行保留。

默认开发构建输出为 `release/Lanxi.exe`。临时预览使用独立输出目录。

R42 的独立构建中间文件暂存于 `build-exe/verification/portable-r42/final/`。本次递归清理被命令执行策略拦截，发版 ZIP 与历史目录已按保留规则整理。

2026-11-21 本机从构建归档、验收目录和旧工作树的生成目录删除 502 份旧包、重复程序和打包中间文件，合计 14,789,419,617 字节。清理后重新核对了两份保留构建的 SHA-256。旧验收日志中的产物路径反映当时的构建位置。

```powershell
& './scripts/build-exe.ps1' -AppName Lanxi-preview -OutputDirectory (Join-Path (Get-Location).Path 'build-exe/preview')
```

正式便携 ZIP 使用版本化打包命令。`--work` 每次传入一个新目录，`--pycharm` 指向本机用于构建插件的 PyCharm 263 安装目录。

```powershell
& './.venv/Scripts/python.exe' -B scripts/build-portable.py --work build-exe/release-candidate --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3'
```

包名与 `portable_update.py` 的版本一致，打包时在独立工作树中执行上述命令，ZIP 写入该工作树的 `release/`。构建日志和用于发布的 `windows-release.json` 位于 `--work` 目录。主程序内置验收工具，ZIP 根目录还有简明使用说明及可选 IDE 工具包。

正式发布后，负责人将已测 ZIP 放入主工作区的 `release/` 并与线上发布清单核对 SHA-256。核验通过后，将被替换的稳定版 ZIP 原名移入 `release/history/`。每个历史版本留一份，按正式发布日期保留最近两个，超出时删除最早的历史版本。重复构建、验收候选包或更新开发版时均跳过历史轮换，开发版沿用固定文件名。

### 发版审查与交接

角色间统一通过 [交付进度中的工单](DELIVERY_STATUS.md#项目工单) 派工、回执、提交验收材料和反馈审核结果。开发和修复工程师在工单补上提交、变更范围、自测结果、已有候选包与清单的位置。负责人在工单指定待测版本和验收条件，由「测试」报告通过项、缺陷及未验证项，并在修复后复测。承接人交付后在原工单标记「待审核」，并在同一工单提醒项目负责人「代码review以及发版」，附上待审提交或版本、结果与未验证项。负责人在原工单回执，合并并审核代码，结合对应版本的测试结果定版，负责发布标签、上传安装包、生产部署、切换更新清单、核验上线和回滚。

用户有直接指示时按用户指示执行。用户没有直接安排时，「前端开发」和「bug修复」按项目负责人在本项目工单中的安排执行。各位工程师遇到需要人工授权的操作时，在原工单记录操作、原因和发起请求的会话，请项目负责人转达给用户。系统审批在发起请求的会话里处理，工程师回填结果。

构建指定工作区的私人版时，额外传入 `--workspace-grant <服务管理员签发结果的本地文件>`。打包会把加入授权放入主程序。构建输入和私人 ZIP 留在受控目录，通过已认证的更新服务分发。 [授权签发与回收](CLOUD_COORDINATOR.md#在澜汐中连接云服务器)

私人版构建也会生成 `release/笔记本下载/下载澜汐最新版.cmd`。单独更新这个脚本可运行 `.\.venv\Scripts\python.exe scripts/build_private_downloader.py <工作区授权文件>`。将生成的 CMD 单独复制给自己的新电脑即可。Git 只保存生成器和无授权模板。

0.7.3 的后台版本标识为 `0.7.3-desktop-only`，修复先隐藏窗口到托盘、再选择退出时 Qt 主循环残留的问题。退出时显式结束应用循环，清理后台与托盘，并在主循环结束后释放单实例锁。保留 0.7.2 的 Codex 继续按钮修复，以及 0.7.1 的暂停误报与额度恢复功能。新版使用独立文件名交付，旧 EXE 未被覆盖。

构建脚本只在 `release/` 尚无 `local-config.json` 时复制根目录配置；不会同步或覆盖已有的发布配置，也不会复制数据库。分享 EXE 时不要连同私密配置、数据库和会话草稿一起分享。替换正在使用的 EXE 前，先从托盘退出程序。

EXE 已包含 Python 界面依赖，仍需要对应本机 CLI。R21 桌面包的运行状态写在 `%LOCALAPPDATA%\Lanxi\data`，首次可从旧版 EXE 同目录迁移。 [安装、升级与验收](PORTABLE_CHECK.md)

## Windows 登录启动

以下命令会注册或移除当前用户的计划任务，按需执行：

```powershell
& './scripts/install-startup.ps1' -ExecutableName 'Lanxi.exe'
Get-ScheduledTask -TaskName CodexMobileMonitor | Select-Object TaskName, State, Actions
& './scripts/install-startup.ps1' -Remove
```

指定 `-ExecutableName` 时，脚本必须在 `release/` 找到该文件才会更新启动项。省略此参数时优先选 `release/Lanxi.exe`，其次查找旧版 CodexMobileMonitor 桌面程序，最后回退到 `.venv/Scripts/pythonw.exe desktop_app.py`。使用普通用户权限，登录触发，配置失败重启和忽略重复实例。已有计划任务需重新运行脚本才会采用新入口。

从旧版本迁移后，检查已有计划任务的实际 `Actions`；入口不是当前桌面程序时，重新执行安装脚本更新它。注册成功不等于重新登录和故障重启已经实测。设置环境变量后应重启对应进程，使其继承新值。

## 验证

Python 回归测试：

```powershell
& './.venv/Scripts/python.exe' -m unittest discover -s tests -t . -p 'test_*.py' -v
```

`-t .` 将项目根目录加入测试导入路径。测试使用临时存储和假 RPC，不启动真实模型或向钉钉外发。Stream SDK 未安装时，对应离线 SDK 测试会跳过。

Qt smoke 使用虚构会话、替代后台和临时数据，点击双客户端按钮并输出截图：

```powershell
& './.venv/Scripts/python.exe' './tests/smoke_desktop.py' --output "$env:TEMP/monitor-dual-client-smoke"
```

它验证生产 Qt 界面的事件接线和显示状态。实际验收还应检查真实 CLI 收发、模型回复、单次工具审批往返、Windows 通知展示，以及重新登录后的启动行为；钉钉联网按其配置文档单独核验。上述命令是检查方法，不表示每次构建都已完成这些现场验收。

## 常见检查

| 现象 | 检查 |
|---|---|
| `python` 找不到或指向 Microsoft Store | 使用项目 `.venv/Scripts/python.exe` 的明确路径 |
| 没有完整双客户端界面 | 安装 `requirements-desktop.txt`，从 `desktop_app.py` 启动 |
| 托盘退出后没有窗口但仍有旧监控进程 | 0.7.2 及更早版本可能遗留 Qt 主循环；升级到 0.7.3。新程序不会自行清理已残留的旧版本，应确认完整路径和进程树后单独处理，勿按 `codex.exe` 名称批量结束 |
| 再次打开没有新窗口 | 查看系统托盘；桌面单实例机制会阻止重复启动 |
| 登录后没有窗口 | 检查计划任务实际入口、状态和本机 CLI；确认程序是否在托盘中 |
| 修改配置后没有效果 | 确认改的是源码根目录还是 EXE 目录；自动接入和 Stream 身份在启动时读取，修改后重启 |
| 重新接入后 Codex 已运行但不能发消息、“继续”灰色 | 先从托盘退出旧版，再打开 0.7.2；有控制权时点击“继续任务”，只解除本地暂停，不重复启动任务 |
| Codex 没有自动续跑 | 检查自动推进开关、控制权、实时空闲、active goal 和待审批 |
| Codex 额度用完后等待 | 查看“等待额度刷新”的时间；到点会重新查询，服务端确认可用后继续原任务。手动暂停、关闭自动推进和移出会取消恢复 |
| 额度恢复但旧版仍拒绝继续 | 先从托盘退出旧版，再打开 0.7.1；手动继续允许重新尝试，预算受限或已完成目标仍需核对 |
| Claude 没有自动推进 | 默认关闭；检查是否手动开启、已到一小时检查时间，且可控制、空闲、无审批或执行错误 |
| 额度显示未知 | 当前没有有效官方数据，不代表额度为零；Claude 本实现没有官方额度查询能力 |
| 钉钉没有响应 | 分别检查 Webhook / Stream 凭据、Stream SDK、私聊身份三元组，见 [钉钉配置](DINGTALK_SETUP.md) |

备份前退出相应实例，复制其完整状态目录，保留 SQLite 文件及存在的 sidecar 文件。恢复和重新构建时保留私密配置；详细文件清单见 [配置说明](CONFIGURATION.md)。
