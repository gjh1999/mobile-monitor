# 实施与验收记录

> 历史验收日志。各条结论只描述记录当时的版本和环境；旧文件名与路径的迁移见 [整理记录](REORGANIZATION_2026-11-14.md)，当前操作见 [运行与维护](../RUNBOOK.md)。

## 2026-11-22 R50 审批弹窗与跨机提醒复验

- Windows 11、Python 3.14.7、PySide6 6.12.0，候选提交 `365520d`。`.venv\Scripts\python.exe -B -m unittest tests.test_codex_approval_alerts tests.test_codex_watch tests.test_monitor tests.test_desktop_storage -q` 通过 21 项，耗时 1.268 秒。覆盖同一数据目录只保留当前 Hook、第三方 Hook 保留、跨机完成提醒本机设置及旧事件跳过。
- `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/approval-reminders-r50/qt-final` 通过。模拟两套设备身份，接收端关闭审批窗后丢失并恢复同一待审批列表，自动弹窗维持关闭，可以在托盘手动打开，新增本机请求自动显示。用例保留跨机提交及实际回执验证。
- 使用已安装 VS Code 扩展随附的真实 Codex 与隔离用户目录，连接回环模型夹具。生成一次原生审批请求，在澜汐窗口打开、托盘运行、完全退出三种状态分别看到一次提醒，最后在原生连接拒绝该夹具请求。运行 `build-exe/verification/approval-reminders-r50/native-desktop.py window`、`native-desktop.py tray` 及 `build-exe/verification/portable-r45/check_standalone_approval.py`。各模式日志位于 `build-exe/verification/approval-reminders-r50/`。另一台实体电脑的现象待核对。 [验收工单](../DELIVERY_STATUS.md#approval-reminders)

## 2026-11-22 R49 本地消息响应与原生标题验收

- 环境为 Windows 11 26100、Python 3.14.7、PySide6 6.12.0。使用隔离会话和合成指令验证 `fa3616f` 的窗口响应。在 Codex 和 Claude 测试中，后台入队和后续投递各阻塞 5 秒，测量从 Qt Enter 事件到待发送气泡绘制的时间，100 毫秒内通过。最终复跑测得 Codex 6.3 毫秒、Claude 7.9 毫秒，期间覆盖继续输入、切换会话和连续相同文本逐条去重。测量止于本机反馈，没有测量模型响应时间。
- 基线从 `git show R48:desktop_qt.py` 提取 `MainWindow.operate`，后台 `submit` 注入 `time.sleep(5)`。R48 的输入框在 5000.5 毫秒后清空，当时的发送流程没有待发送气泡。R49 使用 `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/r49-timing-confirm` 复现，截图写入指定目录，日志位于 `build-exe/verification/portable-r49/desktop-smoke.log`。
- 隔离的 Windows Codex 会话验证连续改名后读取原生标题与重启列表。离线改名后另外修改原生标题可进入冲突，处理后重新读取 Codex 确认结果。文档入口见 [桌面操作说明](../DESKTOP.md) 和 [验收工单](../DELIVERY_STATUS.md#即时发送标题同步与项目介绍阅读模式)。

## 2026-11-21 本机发布目录整理

- 稳定版沿用已发布的 R36 私人 ZIP，整理后复核 SHA-256 与线上发布记录一致。从 `a9bc851` 的 R36 源码构建本机开发版 `release/Lanxi.exe`，SHA-256 为 `7b20d52592147404c614fbc6cf7df1b793883f22d9b6c94a844384fce27d9199`。
- Windows 构建使用 `scripts/build-exe.ps1 -AppName Lanxi`。首次指定相对构建目录时，PyInstaller 从 spec 所在目录寻找验收助手，未生成开发版。重跑时将 `-BuildDirectory` 与 `-OutputDirectory` 分别设为项目中 `build-exe/release-cleanup-r36/dev-build` 和 `dev-output` 的绝对路径。执行该构建的 `Lanxi-Verify.exe --exe <dev-output/Lanxi.exe> --mode local --output build-exe/release-cleanup-r36/local-check.json`，六项通过，13.86 秒。替换后重新核对开发版摘要。
- 移入 `build-exe/release-archive/20261121/` 的旧桌面产物共 53 项，包括 22 个 EXE、15 个 ZIP 和 16 个解压目录。R20 目录内的历史数据库随目录归档。服务端部署 ZIP、下载和配对工具、根目录运行数据仍在原位置。此次整理范围为本机工作区。

<a id="r36-release"></a>
## 2026-11-21 R36 私人 Windows 版与协调服务发布

- 基线为 R35。负责人复核 `af6047b` 的共享审批控制权检查及 `762dbbf` 的 ZIP 成员校验，补审发现大中央目录的旧备份可能无法枚举与替换，追加 R36-01。修复提交 `41743e4`。负责人在 Windows 执行 `.venv\Scripts\python.exe -B -m unittest tests.test_coordination tests.test_codex_watch tests.test_code_backups -q`，19 项通过，3.572 秒。原修复版本的全量源码回归复用 R35-04 结果，258 项通过、3 项跳过。
- 客户端在独立工作树 `build-exe/r36-src` 的 `210bde1` 构建。使用项目虚拟环境执行 `scripts/build-portable.py`，`--work` 为项目绝对路径下的 `build-exe/verification/portable-r36-20261121/windows`，PyCharm 2026.3，复用 R33 私人工作区授权文件。产物为 `release/Lanxi-R36-Windows-Private.zip`，95,239,953 字节，SHA-256 为 `4321740483e1374c91ffd4730b1038f71b14a12ca4bf0c93c3c6bea9d6de5110`。服务端单独使用 `41743e4`，这个提交在客户端构建后增加了服务器兼容性修复和相应回归。
- 运行 `build-exe/verification/portable-r36-20261121/verify_package.py`。R35 更新器校验并解包 R36，实包代码与构建提交比对通过，内置验收在隔离 Windows 数据目录运行，6 项通过，13.79 秒。测试工程师在独立目录 `build-exe/verification/r36-02-qt-20261121/packaged-widgets-01/` 对包内 Qt 代码进行原生交互，4.393 秒通过审批、壁纸和每日记录。 [R36-02 独立验收](../DELIVERY_STATUS.md#r36-02)
- 部署脚本、发布脚本与线上检查位于 `build-exe/verification/portable-r36-20261121/`。第一次部署暂存清单采用 Windows 行尾，远端摘要检查失败，发生在服务切换之前。改为 LF 后部署成功，生产容器健康，两份运行模块摘要通过比对，原数据卷与旧镜像保留。两套配对身份通过真实 HTTPS 上传、下载合成项目并逐字节比较，随后清理示例数据。
- 服务器复核 ZIP 摘要后切换私人更新清单。两套 R35 配对身份均查到 R36，另以笔记本配对身份通过生产 HTTPS 完整下载并复核大小和 SHA-256。配对身份验证在同一台 Windows 电脑执行；两台实体电脑与真实 Codex 客户端的审批往返仍待现场验证。 [安装与升级](../PORTABLE_CHECK.md)

<a id="r36-01-backup-metadata"></a>
## 2026-11-21 R36-01 既有 R35 备份兼容性

- Windows、Python 3.14.7。使用临时回环协调服务器与两个配对设备，放入由 `ZipFile` 正常生成的 1,500 成员 ZIP 模拟 R35 既有副本。测试将新上传的中央目录上限设为 128 KiB，旧 ZIP 的中央目录超过该上限。修复前执行 `.venv\Scripts\python.exe -B -m unittest tests.test_code_backups.CodeBackupTests.test_list_and_replace_backup_written_before_directory_limit -v`，列表返回 HTTP 500，用时 0.604 秒。
- 修复提交 `41743e4`。查询旧副本从文件尾部读取 ZIP 版本注释，最多读取 65,557 字节。新上传继续读取并校验中央目录及成员。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_code_backups tests.test_coordination tests.test_temporary_files -v`，18 项通过，用时 4.745 秒。既有包可列出与原样下载，同一大目录包作为新上传被拒，错误的待替换版本返回 HTTP 409，提交正确旧版本后新 ZIP 可以替换。临时 ZIP 随测试目录清理。服务器候选部署与上线验证待负责人审核安排。 [R36-01 工单](../DELIVERY_STATUS.md#r36-01)

<a id="r35-backend-ticket"></a>
## 2026-11-21 R35-01 共享 Codex 审批复核

- 基线为 R35 与负责人补审提交 `e068d55`。已发布的共享连接缺少普通连接拥有的项目控制检查。修复提交 `af6047b` 在发送应答前按当前来源设备复核控制权。显式登记项目的写入凭据随版本失效。个人工作区已交还的会话即使同一设备仍有另一个任务占用项目，也会拦截原审批。
- Windows、Python 3.14.7。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_codex_watch tests.test_coordination tests.test_personal_project_lock tests.test_automation tests.test_monitor tests.test_web tests.test_codex_activity tests.test_codex_desktop tests.test_task_control tests.test_personal_flow`，86 项通过，用时 37.716 秒。覆盖持有、释放、转移及 IDE 开关后旧写入版本失效。SQLite 连接资源警告与 Starlette 弃用警告仍有输出。
- 执行 `.venv\Scripts\python.exe -B -m tests.smoke_cloud_coordinator --output build-exe/verification/r35-01-approval-control/https-final.json`，隔离 HTTPS 通过，用时 14.67 秒。使用临时 Git 仓库、两个同工作区身份、一个隔离工作区身份和模拟 App Server 的原应答管道。持有控制权时收到原请求 ID 的同意应答。释放、转移后没有发出原应答，远端收到 `control_lost`。保留了首次同通道通过的报告 `https.json`，用时 14.51 秒。
- 实际客户端审批、实体双机、候选包和线上发版由独立复测与发布流程继续。个人工作区复用既有接口，配置了显式项目的协调服务需使用含写入版本字段的源码。 [R35-01 工单](../DELIVERY_STATUS.md#r35-01)

<a id="r35-02-backup-validation"></a>
## 2026-11-21 R35-02 备份成员校验

- Windows、Python 3.14.7。输入为回环协调服务临时目录中的有效 ZIP，以及替换 ZIP 的一个成员内容损坏、传输 SHA-256 与上传字节一致的隔离夹具。修复前执行 `.venv\Scripts\python.exe -B -m unittest tests.test_code_backups.CodeBackupTests.test_rejected_backup_preserves_previous_restorable_bytes -v`，旧服务接收损坏包，预期拒绝的测试失败，用时 0.588 秒。
- 修复提交 `762dbbf`。上传在替换前逐成员分块读取并核对长度和 CRC。ZIP 中央目录最多 8 MiB、成员最多 10 万、总解压大小最多 4 GiB。每个服务进程同时校验至多两份上传。校验结束后再次核对服务器当前版本再执行原子替换。
- 修复后执行 `.venv\Scripts\python.exe -B -m unittest tests.test_code_backups tests.test_personal_flow tests.test_project_library tests.test_coordination tests.test_temporary_files`，32 项通过，用时 32.469 秒。损坏包及超过展开大小或文件数限制的包均被拒绝。另一配对设备下载上一份 ZIP 后，字节与首次上传完全一致，成员内容成功恢复。测试数据和下载结果在测试独立临时目录中，结束后随夹具清理。R35-04 的独立源码复测已在工单回执，待负责人复审及部署。 [工单](../DELIVERY_STATUS.md#r35-02)

<a id="r35-frontend-ticket"></a>
## 2026-11-21 R35-03 前端验收

- 输入为 `release/Lanxi-R35-Windows-Private.zip`，SHA-256 为 `6cf1aa158b855b1c744579c655a607aea8e987cbfe0d0eac2225a6b5fe00c3d1`，完整 ZIP 的 CRC 检查通过。Windows、Python 3.14.7 与 PySide6 6.12.0，使用隔离资料目录及模拟 RPC。
- 执行 `.venv\Scripts\python.exe -B build-exe/verification/r35-frontend-ticket-20261121/verify_widgets.py packaged-widgets-01`。从 EXE 的 PYZ 直接加载包内模块到测试进程，用 Qt 点击审批、壁纸和每日记录界面，交互测试通过，用时 2.949 秒。覆盖审批勾选、全部同意、单项拒绝、未收到与收到回执、缺详情跳过；壁纸导入与恢复；每日记录三段滑块、日期合并和过日刷新。截图和报告保存在 `build-exe/verification/r35-frontend-ticket-20261121/packaged-widgets-01/`。
- `desktop_qt`、`desktop_approvals` 与 `d4361ee` 的代码对象相同，与 `42e5395` 不同。R35 实测审批左栏只显示类型与任务，缺详情项的状态仍是「待审批」，勾选沿用深色方框。原命令可在右栏读到。源码比较还确认 R35 缺少关闭窗口后发送结果信号时的异常保护，该项待候选包独立复测。
- 执行 `.venv\Scripts\python.exe -B build-exe/verification/r35-frontend-ticket-20261121/verify_exe_local.py`。包中 `Lanxi-Verify.exe` 用隔离本机模式启动原 R35 EXE，6 项通过，用时 13.92 秒。输出 `exe-selftest-01/portable-local.json`。原 EXE 进程实测覆盖启动及自检，界面交互由包内模块测试覆盖。真实 Codex 审批和实体双机待验证。 [工单 R35-03](../DELIVERY_STATUS.md#r35-03)

<a id="r35-owner-review"></a>
## 2026-11-21 R35 负责人补审

- 输入为 Windows 工作区 `15e4d00` 及已发布的私人 ZIP。以项目虚拟环境执行 `python -B -m unittest discover -s tests -t . -p "test_*.py" -q`，207.576 秒运行 259 项，257 项通过、2 项跳过。输出中有 SQLite 连接资源警告和 Starlette 弃用警告。
- 同一环境执行 `python -B tests/smoke_desktop.py --output build-exe/verification/release-review-r35-20261121/qt`，Qt 源码交互通过。集中审批、壁纸管理和每日记录的截图在该独立目录；Qt 总耗时未测量。
- 本轮重新计算服务器及本地 ZIP 的 SHA-256，并用 `ZipFile.testzip()` 检查本地包。通过 PyInstaller 的 `CArchiveReader` 读取 ZIP 中 EXE 的 PYZ，比对送审源码编译出的代码对象。审批窗口及主窗口与 `d4361ee` 匹配，其余抽查结果及两项退修见[补审单](../DELIVERY_STATUS.md#r35-review)。
- 用 `python -B build-exe/verification/release-review-r35-20261121/live_readonly.py` 从本机通过两套配对身份查询生产 HTTPS；两者均可看到 R35 更新清单、读取备份列表。现场实体双机、真实审批往返及此轮实包界面交互待验收。

## 2026-11-21 R35 GitHub 与服务器代码备份

- 以已发布的 R34 为基线。连接私有协调服务时，自动同步和手动「合流」均执行 GitHub 同步与服务器备份，分别报告结果。服务器按项目和设备保存最新的校验成功 ZIP。恢复工作流从「跨机接续」下载 ZIP。GitHub 失败时仍可备份本机已保存的有效文件。
- Windows、Python 3.14.7。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_code_backups tests.test_personal_flow tests.test_project_library tests.test_desktop_projects tests.test_coordination tests.test_temporary_files`，33 项通过，32.448 秒。新增回环测试验证两种同步触发、GitHub 故障时保存未提交源码、未变化重复轮次、仓库凭据与忽略文件过滤、空间隔离、下载内容和错误上传保留旧副本。初次夹具缺少 `projects` 配置，第二次比较未兼容 Windows 的 CRLF。修正后以本机原始字节比较。Qt smoke 使用 `build-exe/verification/code-backups-20261121/qt` 输出。复跑 `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/code-backups-20261121/qt-destinations` 通过，检查 GitHub 与服务器显示各自的成功和失败，总耗时未单独测量。
- `build-exe/verification/code-backups-20261121/server_rollout.py` 在原云协调镜像上安装两个模块，保留旧镜像和数据卷。部署后两套已配对身份都能查询备份。`live_check.py` 用同一真实 HTTPS 服务上传合成 Python 文件，从另一设备身份下载并逐字节比较，清理了测试项目的 ZIP。真实项目尚未执行首次 R35 备份。
- 使用 `scripts/build-portable.py --work build-exe/verification/code-backups-20261121/windows --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3' --workspace-grant build-exe/verification/portable-r33-20261120/lanxi-private-workspace.join` 构建私人版。ZIP 大小 95,230,904 字节，SHA-256 为 `6cf1aa158b855b1c744579c655a607aea8e987cbfe0d0eac2225a6b5fe00c3d1`。`verify_package.py` 验证包清单、R34 更新器校验解包和内含备份模块，隔离的实包自检 6 项通过，17.69 秒。`publish.py` 校验服务器收到的包并激活清单，两套已配对 R34 身份的更新检查均可见 R35。 [安装与更新](../PORTABLE_CHECK.md) · [使用说明](../PERSONAL_SYNC.md#代码的服务器备份)

## 2026-11-21 桌面集中审批窗口

- 以 `babdfdb` 的审批后端为基线。Qt 窗口使用主页面的壁纸和双栏毛玻璃，通过现有队列显示本机与跨机请求。在临时私人记录库中用两套设备身份和模拟 RPC 验证单项勾选、混合来源全部同意、缺少详情跳过、拒绝当前项，以及收到回执前保持等待状态。
- Windows、Python 3.14.7、PySide6 6.12.0。`.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/approval-queue-20261121/run-03` 通过。首轮审批用例关闭窗口后紧接键盘用例，桌面窗口焦点未及时恢复。调整验证顺序后复跑通过。截图为 `run-03/approval-queue.png` 和 `run-03/approval-awaiting-receipt.png`，Qt 总耗时未单独测量。
- `.venv\Scripts\python.exe -B -m unittest tests.test_codex_watch tests.test_monitor tests.test_web tests.test_automation tests.test_codex_activity tests.test_codex_desktop tests.test_task_control tests.test_coordination -q` 的 72 项回归通过，用时 8.127 秒。`tests.test_desktop_shutdown` 复验主窗口和托盘退出，通过 1 项，用时 2.206 秒。真实客户端、两台实体电脑和便携版待验收。 [桌面使用](../DESKTOP.md#审批窗口)

## 2026-11-21 壁纸管理

- 以 R34 的本机图库和内置壁纸轮换为基线。从「设置 → 壁纸管理」查看当前壁纸信息，导入本地图片，立即显示，移出或恢复轮换。原始文件保留，图片副本和移出记录随实例数据保留。
- Windows、Python 3.14.7、PySide6 6.12.0。执行 `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/wallpaper-manager-20261121/run-03`。首轮旧测试仍读取轮换整数索引，改为核对当前壁纸后通过。最终测试点击真实菜单和窗口按钮，用临时横版、竖版、同名及损坏图片验证导入和预览，核对当前壁纸移出后切换、全移出后的纯色背景、重新读取设置、恢复和固定模式。截图在 `run-03/wallpaper-manager.png`，总耗时未单独记录。
- `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_storage -q` 的 4 项通过，用时 0.131 秒，确认迁移保留图片副本和移出记录。便携包待发布。 [桌面使用说明](../DESKTOP.md#壁纸管理)

## 2026-11-21 Codex 全局监控与跨设备审批后端

- 以 R34 和开发开始时的源码 `899a67e` 为基线。目标是独立于任务接入状态收集会话完成事件和可见的审批。多台电脑加入同一工作区时，可查看同一待审批队列、选择部分或全部同意，实际决定由会话所在设备回复。需求与前端分工见[主文档](../跨设备开发工作台方案.html#ownership)。
- Windows，Python 3.14.7，已安装 Codex CLI 0.171.0。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_codex_watch tests.test_monitor tests.test_web tests.test_automation tests.test_codex_activity tests.test_codex_desktop tests.test_task_control tests.test_coordination`。72 项通过，耗时 7.836 秒，包含未接入任务的原生日志增量读取、历史完成过滤、共享连接审批订阅与原请求 ID、勾选和全部同意、工作区切换隔离和已失效审批。
- 执行 `.venv\Scripts\python.exe -B -m tests.smoke_cloud_coordinator --output build-exe/verification/codex-watch-20261121/https-final.json`。同机启动带真实 TLS 的隔离协调服务，两套设备身份在同组，第三套在另一组。两项审批中只同意所选的一项，来源端使用原请求 ID 回执，另一项保持待确认，另一工作区读取记录返回 404。完成事件经同一通道往返。审批输入使用模拟 RPC。沙箱内复跑时 Windows DPAPI 加密临时凭据返回 WinError 2，经授权后通过，最终耗时 12.87 秒。报告在 `build-exe/verification/codex-watch-20261121/`。
- 默认 Codex 共享 socket 的只读探测在沙箱内未能连接，原生 Codex 审批使用协议夹具验证。两台实体电脑、真实 Codex 客户端审批、Windows 提醒显示、集中审批窗口和便携版发布待验收。现有服务器 API 支持该通道，无本次服务端部署。 [后端接口](../ARCHITECTURE.md#全局-codex-监控与审批队列)

## 2026-11-21 R34 合流、项目发现与本机会话排查

- Windows，Python 3.14.7。本机 R34 的项目母目录为 `D:\VscodeProject`，目录自身带 `.git`。基线扫描在母目录即停止。首轮递归方案识别出了仓库里的内嵌依赖和测试仓库，调整为已建立的 Git 仓库作为项目边界。用 `project_library.discover(Path('D:/VscodeProject'), {})` 只读扫描，最终列出 13 个本机项目入口，耗时 0.039 秒，包含 `mobile-monitor` 和 `lanxi-commercial` 的原始路径。项目入口和 GitHub 同步资格分别取决于本机项目标记与仓库配置。
- 从本机 Windows 登录用户对 9 个登记项目执行只读 `git ls-remote --symref origin HEAD` 核查。`MCCAI` 当时与远端一致，`mobile-monitor` 当时有未推送提交。6 个仓库在非交互环境中无法取得 GitHub 凭据。`Image_Registration` 在 `git rev-parse --show-toplevel` 遇到仓库所有者与当前 Windows 用户不同。未对其余项目运行提交、拉取或推送。
- `.venv\Scripts\python.exe -B -m unittest tests.test_project_library tests.test_desktop_projects tests.test_personal_flow tests.test_monitor tests.test_desktop_shutdown` 通过 25 项，31.645 秒。临时项目回归覆盖母目录自身带 `.git`、现有克隆复用、无效登记隔离及未匹配项目的离线会话。两项临时 Git 测试复跑通过，3.692 秒，新增验证接收端母目录自身也带 `.git` 时可按来源相对路径克隆。
- 全量命令 `.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -p 'test_*.py'` 首轮运行 254 项，用时 233.777 秒。项目用例的虚构仓库缺少分支引用，修正夹具后通过；三个退出子用例的构造签名已由同期更新修正。第二轮运行 257 项，用时 208.262 秒，跳过 2 项，有 1 项 Web 用例失败，原因是同期新增的 `codex-watch` 后台线程还未计入旧测试断言。断言更新后单测复跑通过，0.816 秒，未再次执行完整套件。
- `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/sync-projects-20261121/qt-groups` 通过，覆盖「合流」中的成功项目、冲突项目、本机路径和大文件差异，并在未启用个人同步的界面验收带 `.git` 母目录下的两个独立项目组。测试使用隔离数据和模拟后台，总耗时未单独测量。便携版及两台物理电脑现场待验，笔记本本机历史文件尚未核对。 [主文档](../跨设备开发工作台方案.html#current)

## 2026-11-21 吉光片羽每日记录

- 以 R34 的随记、待办和逐条同步格式为基线。左栏新增三个分类的圆角矩形滑块，复用工作台顶部的点击、拖动、回位与系统减弱动画逻辑。「每日记录」按本机日期将当日随记和待办合在同一页。未完成待办继续留在待办列表，跨天变更时保留旧值，复用已有个人同步存储传递历史。
- Windows、Python 3.14.7、PySide6 6.12.0。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_notebook_sync -v`，2 项通过，覆盖旧库、跨天修改与办结、两套临时数据库的逐条同步和重复同步。
- 执行 `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/notebook-daily-20261121/run-02`。验证点击和拖动滑块、每日列表去重、只读日期页、未完成待办续留、临界日期刷新及顶部工作区切换。首次测试在虚拟跨日结束后未还原时钟，后续步骤访问了已清理的测试数据库。修正隔离并复跑通过。新截图在 `run-02/daily-records-desktop.png`，总耗时未单独记录。便携包与两台实体电脑待验收。 [使用说明](../DESKTOP.md#吉光片羽)
- 整仓 `unittest discover -s tests -t . -p 'test_*.py' -q` 运行 254 项，用时 230.137 秒，跳过 2 项。退出测试的旧桩未接收 R33 新增的工作区参数，三个退出场景触发同一参数错误。更新测试桩后，重跑 `tests.test_desktop_shutdown tests.test_notebook_sync` 的 3 项测试全部通过，用时 2.764 秒。

## 2026-11-20 R34 顶栏整理与手动同步

- 以 R33 和源码 `370f36a` 为基线。主窗口顶部统一提供「连接云服务器」「测试」「合流」。点击「合流」会唤醒现有同步线程，代码经 GitHub，记忆和大文件清单经协调服务。已有同步时排队，重复点击共用同一轮结果。结果只使用当前轮的文件清单，并显示冲突及本机缺少或大小不同的文件。
- Windows、Python 3.14.7。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_personal_flow tests.test_large_inventory tests.test_temporary_files`，14 项通过，41.932 秒。新增测试使用临时 Git 仓库和私人共享记录，源端 `VSCode/A` 与接收端 `PyCharm/A` 采用同一 origin，两台分别配置各自的共同父目录。手动请求排在进行中的后台轮次后，接收端在原目录取得新代码和新记忆。接收端未克隆第二份项目。
- `.venv\Scripts\python.exe -B tests/smoke_desktop.py --output build-exe/verification/portable-r34-20261120/qt` 通过，验证顶栏入口、等待与执行状态、项目结果、冲突及文件清单显示。Qt 总耗时未单独测量。 [项目目录和手动同步说明](../PERSONAL_SYNC.md)
- 执行 `.venv\Scripts\python.exe -B scripts/build-portable.py --work build-exe/verification/portable-r34-20261120/windows --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3' --workspace-grant build-exe/verification/portable-r33-20261120/lanxi-private-workspace.join`。复用 R33 的私人工作区加入授权。`release/Lanxi-R34-Windows-Private.zip` 为 95,153,530 字节，SHA-256 为 `232a7c47ee3384180008471360c4768164e89d52cd9e13a1ed4e2e75be571488`。
- 同目录 `verify.py` 验证 ZIP 校验值、文件清单和内置授权，使用 R33 更新器解包并运行内置自检，6 项通过，15.95 秒。`publish.py` 将同一 ZIP 发布到私人更新服务，已连接的 R33 更新检查可见 R34。本机解压目录 `release/Lanxi-R34-Portable/` 的 EXE 与验收副本一致。实际两台实体电脑的合流操作待现场验证。

## 2026-11-20 R33 一键连接云服务器

- 以 R32 和源码 `4045823` 为基线。私人包每次先选「连接云服务器」或「本机使用」，未设置过项目母目录才弹目录选择器。顶部任务栏、主窗口左下角及测试窗口统一使用「连接云服务器」。已连接设备沿用原凭据，本机模式只在当前运行中暂停澜汐的云同步和云更新。包内授权绑定既有私人工作区，各设备先后加入且各有独立凭据。R32 的两台限制和邀请码流程继续供旧客户端使用。
- Windows、Python 3.14.7。执行 `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_storage tests.test_monitor tests.test_personal_flow`，20 项通过，22.831 秒。`tests/smoke_desktop.py --output build-exe/verification/portable-r33-20261120/qt` 通过，覆盖启动两种选择、已有目录复用、已有凭据的本机模式和顶部按钮。未单独测量 Qt 总耗时。
- `.venv\Scripts\python.exe -B -m tests.smoke_cloud_coordinator --output build-exe/verification/portable-r33-20261120/cloud.json` 用隔离 HTTPS 验证四份设备配置、同一空间的记录、重复加入、撤回授权和服务重启，14.49 秒。限制私人包下载范围后，以 `cloud-scoped.json` 为输出复跑，14.42 秒，另确认缺少范围标记的新版包不可下载，另一工作区无法查看发布清单或按版本下载。
- `.venv\Scripts\python.exe -B scripts/build-portable.py --work build-exe/verification/portable-r33-20261120/windows --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3' --workspace-grant build-exe/verification/portable-r33-20261120/lanxi-private-workspace.join` 构建 `release/Lanxi-R33-Windows-Private.zip`。大小 95,150,111 字节，SHA-256 为 `6e04106ac7c5b785a317f9f600174a8c51525a6ea5184197261e75e7617814ba`。同目录 `verify.py` 使用 R32 更新器校验并解压同一包，检查包内授权位于主程序资源，在未连接的隔离环境运行包内自检，6 项通过，16.08 秒。 [连接说明](../CLOUD_COORDINATOR.md#在澜汐中连接云服务器)
- 私人 HTTPS 服务已部署加入接口和按工作区下载隔离，保留原数据卷和镜像。第二阶段首次部署指令因拼接缺换行在本机解析失败，修正后服务通过健康检查，错误授权返回 403。发布服务器复核同一 ZIP 的 SHA-256，已连接的 R32 更新检查可见 R33。四台验证用隔离配置，物理电脑的现场连接待实测。

## 2026-11-20 R32 双机自动配对

- 以 R30 手工配对为基线。R32 两台设备用同一份私有设置文件申请。文件自签发可发起申请 30 分钟，等待中的设备无权访问个人空间。第二台申请后服务端在同一事务登记两个独立设备。重复请求继续使用原设备身份，第三台及过期文件被拒绝。手工配对码流程保留。
- Windows、Python 3.14.7。执行 `.venv\Scripts\python.exe -B -m tests.smoke_cloud_coordinator --output build-exe/verification/portable-r32-20261120/cloud.json`。首次因测试进程直接调用仓库子目录的管理脚本导致导入失败；第二次测试临时 SQLite 连接未关闭，Windows 清理失败。修正测试运行入口和连接关闭后，隔离 HTTPS 验收 11.41 秒，通过等待时拒绝访问、重试、完成配对、拒绝第三台及过期文件、重启读回，也经过原手工配对和升级测试。Qt `tests/smoke_desktop.py --output build-exe/verification/portable-r32-20261120/qt` 通过，覆盖设置文件载入、等待状态和取消等待。
- 执行 `.venv\Scripts\python.exe -B scripts/build-portable.py --work build-exe/verification/portable-r32-20261120 --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3'`。便携 ZIP `release/Lanxi-R32-Windows-Private.zip` 为 95,145,244 字节，SHA-256 为 `a1f5955cdead805852ae1276af0a88e4ac6100772123c5ae14d7b1ea1655a0ae`。ZIP 文件清单、CRC、起步说明和配对模块均已核对。 [双机配对说明](../CLOUD_COORDINATOR.md#双机自动配对)
- 验收目录内 `deploy_server.py` 将已校验的四个模块部署到私人 HTTPS 协调服务，保留原数据卷和旧镜像。实际服务健康，未授权设置请求返回 403。`publish.py` 发布同一 ZIP，已配对的 R30 更新检查可见 R32，管理员脚本成功在桌面签发设置文件。两台物理电脑尚待现场导入。设置文件和凭据留在本机。

## 2026-11-20 R30 首次配置项目母目录

- 以 R29 和源码 `27ab71d` 为基线。源头电脑选择全新开始后，原流程先显示配对窗口，需要点「稍后」才进入目录选择。R30 直接显示「设置项目母目录」，可本机开始或填写已有配对码。主窗口和测试窗口均有配对按钮。空目录记录会再次提示配置，已有项目目录继续使用。未配对时运行本机自检，已经配对时额外验证云连接。
- Windows、Python 3.14.7、PyCharm 263。`.venv\Scripts\python.exe -B -m unittest tests.test_desktop_storage tests.test_project_library` 通过 6 项，0.127 秒。`tests/smoke_desktop.py --output build-exe/verification/portable-r30-20261120/qt-final` 通过。Qt 用临时中文目录及模拟配对检查了首启的两条路径、主窗和测试窗的配对入口、自检模式随配对状态切换。原始 Qt 流程及仅含首次目录调整的首批候选保留在 `build-exe/verification/portable-r30-20261120/`。
- `.venv\Scripts\python.exe -B -m tests.smoke_cloud_coordinator --output build-exe/verification/portable-r30-20261120/cloud.json` 首轮在沙箱内调用 Windows DPAPI 返回 WinError 2，未生成通过报告。授权后用隔离 HTTPS 服务重跑，9.07 秒，配对、持久化及同步通过。实际私人协调服务也成功签发首台设备配对码，由本机管理员脚本复制到剪贴板。此次未替用户领取配对码或配对物理设备。
- 用 `.venv\Scripts\python.exe -B scripts/build-portable.py --work build-exe/verification/portable-r30-20261120-final --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3'` 构建最终候选。`release/Lanxi-R30-Windows-Private.zip` 为 95,143,115 字节，SHA-256 为 `458131cfbfad816593451e35fac3d28a7165f279274788a71e71e60b3df28046`。ZIP 的三项文件和 CRC 校验通过。最终目录内的 `verify.py` 用 R29 更新器校验并解压发布包，在没有配对配置的隔离环境运行内置验收，6 项通过，14.05 秒。 [使用说明](../PORTABLE_CHECK.md)

- 用最终目录的 `publish.py` 将同一 ZIP 发布到私人协调服务。服务器 SHA-256 校验通过，已配对的 R29 更新检查可见 R30。两台真实电脑的配对待现场完成。

## 2026-11-20 R29 主窗口测试与便携包整理

- 以 R28 和源码 `6db575a` 为基线。笔记本已通过下载脚本取得 R28，测试入口仅位于托盘。R29 在 Codex 和 Claude Code 工作区左下角加入「测试」，与托盘共用验收窗口。便携 ZIP 顶层由 11 项减为 3 项，验收 EXE 内置，IDE 插件和上报工具合为一个可选 ZIP。首次候选构建后发现测试输出仍引用早期随包文档和菜单编号，更新文案后在独立目录重新构建并保留首版候选。
- Windows、Python 3.14.7、PyCharm 263。执行 `.venv\Scripts\python.exe -B scripts/build-portable.py --work build-exe/verification/portable-r29-20261120-final --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3'`。最终包 `release/Lanxi-R29-Windows-Private.zip` 为 95,140,224 字节，SHA-256 为 `67d2cd6fb1782d8842664560f8aff63823139938a20fc0e9fb842d85f530d36a`。R28 为 95,167,375 字节。最终包及内层 IDE 工具包的文件清单与 CRC 均通过。
- 执行 `.venv\Scripts\python.exe -B -m unittest tests.test_desktop_storage tests.test_desktop_shutdown tests.test_ide_activity tests.test_personal_flow`，19 项通过，28.648 秒。`tests/smoke_desktop.py --output build-exe/verification/portable-r29-20261120/qt-smoke` 通过，未单独记录总耗时。最终目录的 `verify.py` 在隔离数据和回环协调服务中点击生产 Qt 按钮并运行从发布包取出的内置测试程序，7 项通过，17.34 秒。R28 更新器用相同候选 ZIP 验证并解压成功，已有数据标记保留。托盘与主窗口打开同一个测试窗口。
- `publish.py` 首次在沙箱内无法访问 SSH，授权后上传同一 ZIP。服务端复核 SHA-256 后切换更新清单，已配对的 R28 客户端能够看到 R29。`verify_live.py` 用隔离的笔记本配对和未修改的下载脚本从阿里云下载，36.50 秒通过大小、哈希和 ZIP 校验。内置验收程序使用同一台 Windows 电脑的两套配对发起和回传，3.13 秒通过。实际笔记本启动 R29 及两台物理机器同时操作待现场确认。脚本、报告和构建输出在 `build-exe/verification/portable-r29-20261120-final/`。 [使用说明](../PORTABLE_CHECK.md)

## 2026-11-20 笔记本最新版下载脚本

- 旧脚本用限时 SSH 凭据读取固定版本。新脚本从当前 Windows 用户的本地 HTTPS 配对配置读取证书和 DPAPI 凭据，用发布清单识别最新版本并校验完整 ZIP。验收时 Windows 11 自带 PowerShell 5.1，服务器最新包为 R28。
- 验证目录 `build-exe/verification/download-latest-20261120/`，命令 `.venv\Scripts\python.exe -B build-exe/verification/download-latest-20261120/check.py`。首次用 LF 行尾运行，`cmd.exe` 误读嵌入的 PowerShell 正文。将脚本固定为 CRLF 后，用带中文和空格的路径启动，隔离的配对配置经阿里云下载 95,167,375 字节的 R28，大小和 SHA-256 一致，用时 29.14 秒。完整包复用、同名坏包保留、证书不匹配拒绝均通过。实际笔记本待运行。 [操作说明](../PORTABLE_CHECK.md)

## 2026-11-20 R28 按需借用与回传大文件

- 以 R27 为基线。借用端从配对设备清单选项目和文件，来源端在线后分块上传。两端用 SHA-256 验证原版和回传版，台式机原文件发生变化时保留两份本机内容。成功或冲突回执写入 SQLite 后清除协调服务器临时目录。正常后台同步继续只登记 100 MiB 及以上文件的清单。
- Windows Python 3.14.7 执行 `python -B -m unittest tests.test_temporary_files tests.test_large_inventory tests.test_notebook_sync tests.test_personal_project_lock tests.test_coordination tests.test_monitor`。首次测试发现字面量回执路由被变量路由遮挡，调整路由后 23 项通过，3.843 秒。覆盖错设备拒绝、分块续传、回传确认后清除字节、源端冲突保留双方文件。
- 输出目录 `build-exe/verification/file-exchange-r28-20261120/`。用两套隔离配置从 Windows 经真实阿里云完成 100 MiB 借用、客户端下载、修改回传、源端应用，88.64 秒。生产容器确认测试记录仍存在且临时文件目录已清理。R28 便携包本机六项验收通过。更新 ZIP 大小 95,167,375 字节，SHA-256 为 `bc849c56a57ac67e6650aea3b414c9e45ec895ae62d2429444f6f881beba343b`。两台物理机器及真实项目文件的现场验收待测。 [借用与回传](../PORTABLE_CHECK.md)
- 已发布 R28 至阿里云。Windows 上运行 `python -B build-exe/verification/file-exchange-r28-20261120/verify_update.py`，以配对笔记本身份模拟 R24 客户端，读取正式更新清单，下载、校验及解压 R28 共 16.27 秒，已有配置和历史的测试文件保留。SSH 标准输入方式未执行发布脚本，改为上传脚本并按路径执行后，服务器包哈希与更新接口均通过复核。

## 2026-11-20 R24 至 R27 项目占用、软件内更新、吉光片羽与文件清单

- 以 R23 为基线。R24 服务端按私人空间和 GitHub 仓库标识分配控制权。通过两套临时的已配对身份，在实际阿里云验证不同设备竞争同一项目、IDE 窗口报到与归还、交接检查点不匹配时拒绝接入。VS Code 0.2.0 VSIX 与 PyCharm 263 插件构建通过，IDE 原生 Codex 聊天仍应遵守上报提示。未在两台物理电脑同时运行 IDE 验收。
- R24 接入软件内更新，旧版需要手动安装一次 R24。用隔离 Qt 窗口确认并从真实阿里云下载 R25，校验后将 EXE 放进独立版本目录，旧用户设置保留。首次发布清单因 Windows 到 Linux 传入的行尾附带回车，文件名异常导致接口 404。校正后同一下载链路通过。旧 R23 服务镜像保留，原 SSH 协调容器继续运行。
- R25 随记、待办、完成状态、归档和手动分组逐条同步。两端同时改同一正文时保存冲突副本，不传整个数据库。模型自动分组在本机生成。R25 同时引入托盘「测试」窗口。首次实包诊断七项通过但窗口未正确更新结束状态，修正 Qt 信号回调命名后复跑七项通过，11.13 秒。
- R26 增加大文件清单。通过真实阿里云上传临时测试文件的相对路径和大小，远端显示文件缺失，且接收目录未出现文件本体。验证 HTTPS 更新接口实际下载 R26 与本机包哈希一致。阿里云系统盘 `/dev/vda3` 容量 40 GB，检查时已用 5.1 GB、可用 33 GB。
- R27 按用户确认将代码同步和清单边界设为单文件 100 MiB。使用旧门槛创建部分克隆后，以同一 Git 提交运行新门槛迁移，旧过滤范围内新允许的文件可恢复。实包 `验收.cmd --mode local` 证明不足 100 MiB 的对象检出，恰好 100 MiB 的对象仍留源机。云笔记单条记录仍使用原有上限。
- 回归命令 `python -B -m unittest tests.test_large_inventory tests.test_notebook_sync tests.test_personal_project_lock tests.test_ide_activity tests.test_coordination tests.test_task_control tests.test_monitor tests.test_personal_flow tests.test_desktop_storage`，65 项通过，31.426 秒。原桌面验收在 Qt 离屏平台的 Esc 断言处停止，改用 Windows 平台后发现旧测试直接检查本地分组缓存，已改为核对同步存储和实际界面归组，复验通过。验证目录为 `build-exe/verification/{project-lock-r24,notebook-r25,inventory-r26,threshold-r27}-20261120/`。
- 未开通 OSS。大文件本体只在用户发起按需借用时考虑临时传输。 [项目控制](../PROJECT_CONTROL.md) · [便携包操作](../PORTABLE_CHECK.md)

## 2026-11-20 R23 首次配对向导

- 以 R21.2 为基线。首次打开应用，同屏填写配对码和项目母目录。台式机可在托盘邀请其他设备，笔记本保存自己的 Windows DPAPI 凭据。服务端用 SQLite 记录配对码哈希及单次领取状态，十分钟后过期。同一台设备可重试尚未超时的请求。配对成功后托盘报告连接与重试状态。
- Windows Python 3.14.7 运行 `python -B -m tests.smoke_cloud_coordinator --output build-exe/verification/cloud-pairing-20261120/coordinator.json`，5.05 秒通过。验证证书约束、令牌落盘保护、同设备重试、不同设备重复领取拒绝、既有配置合并及 HTTPS 服务重启后凭据继续有效。
- 新入口以独立 Compose 服务部署在阿里云 TCP 443，TLS 证书含公网 IP SAN，只挂载个人同步与配对路由。新旧容器共用持久卷，现有回环 SSH 方式继续可用。实际 HTTPS 上用两个临时 Windows 配置打开 Qt 配对窗口，随后由首台设备签发第二台的配对码，测试记录收发通过。输出 `live.json`。笔记本现场登录、网络切换和真实仓库同步尚待验证。 [部署与配对](../CLOUD_COORDINATOR.md)
- 构建 `release/Lanxi-R23-Portable/`，真实便携程序本机自检六项通过，10.38 秒。旧 R20 升级测试与本机自检并行时首次遇到 SQLite 只读错误，串行复跑 5.17 秒通过，验证配置、路径、随记、待办和回复均保留。输出 `portable.json` 和 `upgrade.json`。R23 私人 ZIP 大小 78,222,469 字节，SHA-256 为 `60a465a589104ecc70258cae9bf796e7c8b7359396144ec95c8347f1825c052a`。
- 运行 `python -B -m unittest tests.test_coordination tests.test_personal_flow tests.test_monitor tests.test_model_groups tests.test_desktop_storage`，修正测试新增用例的位置后 33 项通过，22.843 秒。下载脚本从阿里云取回同一 ZIP，SHA-256、CRC 和六份包内文件清单全部一致。
- 验收目录为 `build-exe/verification/cloud-pairing-20261120/`。首次配对码由服务器管理员按需生成，正式配对完成后从澜汐托盘生成新码。真实设备配置、下载凭据和 TLS 私钥未写入 Git。

## 2026-11-20 R21.2 阿里云验收菜单与笔记本报告

- 笔记本的 R21.1 本机自检六项通过，耗时 25.29 秒。三份云接收报告的检查项均为空，原因都是 Windows 用户数据目录缺少 `local-config.json`。失败发生在 SSH 和服务器检查之前。新版将缺少该文件报告为「本机尚未配置阿里云配对」，并指向随包的配对步骤。原始现场报告保留在本机。
- 便携版验收移除共享目录源端与接收端模式以及 `--share`。菜单为 1 本机自检、2 阿里云源电脑、3 阿里云接收电脑。Windows 下重新打包 `Lanxi-Verify.exe`，便携包仍采用 R21.1 已验收的主程序。使用最终包运行 `验收.cmd`，按回车后 10.36 秒通过六项。旧 `--mode receiver` 被拒绝。在临时空目录复现缺配置失败，新提示及退出码符合预期。
- 用同一台 Windows 电脑的两套独立配置，经 SSH 连到实际阿里云，菜单 2 生成测试码，菜单 3 回传成功。同一设备身份被拒绝。工具与报告保存在 `build-exe/verification/portable-cloud-menu-20261120/`。笔记本的真实设备配对仍需在该设备上完成。
- 便携 ZIP 为 `release/Lanxi-R21.2-Windows-Private.zip`，大小 78,190,541 字节，SHA-256 为 `205f2e1e12960f2c7eefb8198fd24357ff4d6d526a2b098a833876eebbe0d9ff`，ZIP CRC、清单及新版随包说明验证通过。从阿里云运行新版下载脚本取回全部字节，SHA-256 与上传文件一致。 [使用和配对步骤](../PORTABLE_CHECK.md)

## 2026-11-20 R21.1 笔记本候选包：项目相对目录与单文件下载

- 复现原 R21 的问题：两端选择不同的项目母目录，源端的项目在 `客户甲/工具组/项目 A`，接收端却按 `checkoutRoot/lanxi-check/fixture` 克隆。预期是保留源端全部相对层级，在各自真实工作区直接修改代码。审查结论和产品规则分别维护在 [交付进度](../DELIVERY_STATUS.md) 和 [工作台方案](../跨设备开发工作台方案.html#personal-release)。
- 在 Windows、Python 3.14.7、Git 2.56.0.windows.4 上，以 R21 为基线，在两个隔离配置中使用可过滤的测试 Git 远端。运行 `python -B -m unittest tests.test_personal_flow tests.test_project_library tests.test_desktop_storage`，15 项通过，耗时 20.721 秒。源端未手工登记项目时仍会发布相对目录，接收端按该路径克隆，第二次编辑在原目录回传。已有单机绑定继续使用。旧清单没有相对路径时可沿用原布局。
- 用 `scripts/build-exe.ps1` 相同的 PyInstaller 参数构建桌面应用；首轮隔离构建使用相对资源路径，PyInstaller 从 spec 目录查找资源失败，改为项目绝对路径后成功。完整候选目录为 `release/Lanxi-R21.1-Portable/`。运行其中的 `验收.cmd --mode local`，10.40 秒通过六项，包括三级中文目录、代码、项目记忆、全局规则、会话文件及 12 MiB 文件过滤。
- 实包升级使用 `python -B -m tests.smoke_desktop_upgrade --old release/Lanxi-R20-Portable/Lanxi-Glass-R20-Portable.exe --new release/Lanxi-R21.1-Portable/Lanxi-Glass-R21-Portable.exe --output build-exe/verification/notebook-relative-20261120/upgrade.json`，5.77 秒通过。另用上一版 R21 建立隔离用户数据后，两次从另一目录打开候选程序，实测配对、项目母目录、随记和既有工作区都保留。输出 `previous.json`。
- 单文件下载脚本由 `scripts/issue-portable-download.py` 生成，私下交付 `release/笔记本下载/下载澜汐.cmd`。服务端强制只读指定 ZIP，七天后凭据失效。首次连接被 OpenSSH 8.0 的到期时间格式拒绝，按服务器本地时间登记后，使用同一份脚本真实下载成功。重复运行复用了已有 ZIP。文件 78,192,492 字节，SHA-256 为 `828e813e03268f2fa19929490b247a2ecf86e98623a37f7f44310c3603f06f6c`，下载副本的 CRC 及六个包内哈希全部符合。下载脚本含专用凭据，仅将无凭据的模板和生成工具纳入 Git。
- 输出目录 `build-exe/verification/notebook-relative-20261120/` 含本轮日志、JSON 和下载副本。本次属于私人候选包验收。两台物理电脑现场联测、托盘人工操作及正在开发的前端整合均未验收；正式版本标签待整体验收后创建。
- 随后将候选包对应的源码提交 `4a14cb1` 与前端流式显示提交 `bf91ae4` 合并为 `750ef02`。合并源码执行 `python -B -m unittest tests.test_codex_activity tests.test_monitor tests.test_project_library tests.test_personal_flow tests.test_desktop_storage`，25 项通过，19.977 秒，输出 `integration.log`。上述私人下载仍对应原候选包哈希。

## 2026-11-20 Codex 流式回复与命令过程气泡

- 基线 `1975aa1`，Windows 11 x64、Python 3.14.7、PySide6 6.12.0、Codex CLI 0.170.0。原版正文等单条消息完成后才显示，命令过程未进入对话。新版按会话、轮次和运行项合并已有消息和增量，命令以「执行札记」呈现。用 `codex app-server generate-json-schema --out ../schema --experimental` 对照 CLI 事件。桌面每 200 毫秒检查所选会话是否有新增量，输出最多保留 6,000 字符并显示末尾 30 行。命令过程只使用本机内存和原生会话已有的完成记录。[Codex 原生历史写入策略](https://github.com/openai/codex/blob/rust-v0.170.0/codex-rs/rollout/src/policy.rs)
- 隔离验收目录为 `build-exe/verification/lanxi-live-output`。在其 `worktree` 设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output ../windows-01`。以虚构命令和两次更新验证「运行中 → 已完成」，同一个气泡保留并接收尾部输出，正文保持 Markdown。首轮实测收到命令增量到界面可见约 202.7 毫秒。已核对灰蓝色过程气泡、失败退出码数据、CLI 完成记录与实时事件合并、旧会话去重、输出边界及 Web 读取隔离。
- 使用 `python ../appserver_roundtrip.py ../appserver-03` 从真实 Codex App Server 接回环模拟供应商，观察到 `item/agentMessage/delta`。Windows 只读沙箱拒绝启动试验 PowerShell，三次均未得到真实命令增量。各轮保留在 `appserver-01` 至 `appserver-03`，依次使用嵌套 PowerShell、直接输出和只读两行文件。命令增量的界面验收采用符合 CLI Schema 的合成事件。真实供应商耗时和独立终端的实时增量未测。
- 复跑 `python tests/smoke_desktop.py --output ../windows-final` 通过，实测命令增量到界面可见约 63.7 毫秒，时间随 200 毫秒检查周期的相位而变，两次采样未测分位数。运行 `python -m unittest -q tests.test_codex_activity tests.test_codex_history tests.test_conversation tests.test_daemon_support tests.test_monitor tests.test_task_control tests.test_desktop_shutdown tests.test_claude tests.test_web`，首轮有一项测试仍断言旧接口参数，更新夹具后通过。补充旧终端记录去重后最终 81 项通过，耗时 10.522 秒。开发方案校验 19 个 ID 和 19 个页内链接。
- 用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R22-LiveOutput`。首包保存在 `package-first`，最终构建约 36.25 秒。交付 `release/Lanxi-Glass-R22-LiveOutput.exe`，61,625,714 字节，复制前后 SHA-256 一致。用 `python -m tests.smoke_desktop_upgrade --old <R20 EXE> --new <R22 EXE> --output ../r22-upgrade.json` 执行隔离升级验收，5.66 秒通过。已核对旧配置、配对项目路径、随记、待办及回复保留，并从另一个目录再次打开同一 Windows 用户的固定数据。

## 2026-11-20 R21 方案审查与私人分发

- 基线 `1edf06e`，Windows、Python 3.14.7、Git for Windows 2.56.0。按原方案逐项核对七个里程碑，核对部署容器内协调、设备控制、个人记录、计算队列、产物及配对管理六个模块与仓库文件的 SHA-256。现网使用私人 SSH 配置。已记录项目母目录与克隆目录脱节、隧道手动恢复、索引全量读取等问题。 [审查结果](../DELIVERY_STATUS.md#2026-11-20-个人版与云服务审查)
- 验证输出均在 `build-exe/verification/workbench-review-20261120/`。运行 `python -B -m unittest -v tests.test_coordination tests.test_compute_queue tests.test_compute_worker tests.test_personal_flow tests.test_desktop_storage tests.test_project_library`，46 项通过，38.890 秒。运行 `python -B -m tests.smoke_cloud_coordinator --output build-exe/verification/workbench-review-20261120/server-https.json`，5.33 秒通过，覆盖 HTTPS、设备隔离、并发冲突、重启持久化与记录大小边界。运行 `python -B -m tests.smoke_personal_flow --output build-exe/verification/workbench-review-20261120/native.json`，两个独立环境往返三轮，真实 CLI 和应用服务沿用同一线程，模型为本机回环服务。
- 旧包仅支持本机模拟或共享目录双机测试。本次重新构建 `Lanxi-Verify.exe`，更新随包指引。运行 `验收.cmd --mode local`，10.34 秒通过，验证新工作区克隆、Git 提交及同路径记忆往返、全局规则、会话文件与 12 MiB 大文件排除。两套独立临时配置分别运行 `--mode cloud-source` 和 `--mode cloud-receiver`，通过 SSH 访问实际阿里云。源端 2.51 秒、接收端 0.32 秒，同设备误配被拒绝。输出 `pack-local.json`、`pack-cloud-{source,receiver,same}.json`。此次使用一台物理 Windows 电脑，笔记本上的登录、断线重连和真实 GitHub 端到端时延仍待测。
- 分发包 `release/Lanxi-R21-Windows-Private.zip` 共 78,192,263 字节，内含两个 EXE、启动验收脚本、三个使用文档及文件哈希。主程序沿用 R21。SSH 上传至服务器账号的私人目录后下载为 `downloaded.zip`，两端 SHA-256 与下载副本均为 `738ebb09568a41d8cb249b7ed4450d82901cc88e777c28cef89d21b280463ecd`，ZIP CRC 和六份清单条目逐一通过。 [笔记本验收步骤](../PORTABLE_CHECK.md)

## 2026-11-20 阿里云无域名私人协调服务

- 以 `0bb30a6` 为基线，在杭州轻量应用服务器上部署私人协调服务。Alibaba Cloud Linux 3.2104 U10、x86_64、2 核、2 GiB、Docker 26.1.3、Compose 2.27.0。使用上一版服务器包和更新的 Compose 配置，上传后逐一核对 SHA-256。共享的协调服务配置移到 `compose.coordinator.yaml`，公网 HTTPS 和私人 SSH 两种模式共用挂载、健康检查及 `coordinator-data` 持久卷。首轮私人配置引用原公网文件时触发域名变量校验，拆出共享配置后两种模式均通过 Compose 检查。 [部署和配对](../CLOUD_COORDINATOR.md#无域名的私人部署)
- 通过 `docker compose -f compose.ssh.yaml up -d --wait` 运行实际服务器。容器报告健康，`ss` 确认只监听 `127.0.0.1:8770`。在一台 Windows 电脑使用 OpenSSH 转发到 `127.0.0.1:18770`，健康检查通过。匿名读取个人索引收到 403，两份不同的设备令牌均可读。从本机直接连接公网 8770 未成功。本次验证了一个真实 Windows 设备的 SSH 链路以及两份令牌，第二台物理电脑和常驻重连待验。
- Windows 上执行 `python -B -m unittest -v tests.test_coordination tests.test_personal_flow`，19 项通过，24.023 秒。执行 `python -B -m tests.smoke_cloud_coordinator --output build-exe/verification/lanxi-aliyun-sas/cloud-regression.json`，6.40 秒通过，覆盖回环 HTTP 配对、拒绝公开 HTTP、原有 HTTPS、鉴权和模拟双客户端同步。实际验收输出 `private-ssh.json` 和回归日志保存在同一输出目录。

## 2026-11-20 R21 用户数据迁移、项目发现与原会话接续

- 以 `c4b1564` 和上一版 R20 便携包为基线。R20 从 EXE 目录读取配置和数据库。R21 首次启动可迁移旧数据，此后从 `%LOCALAPPDATA%\Lanxi\data` 加载，旧文件保留。SQLite 迁移测试覆盖已提交的 WAL 数据、旧版仍在使用时等待退出、失败后重试及更换程序目录。后台约每五秒扫描项目母目录，按项目显示本机路径、记忆位置和原 Codex 会话，并为新发现的项目创建可启卷的案头条目。 [跨设备开发工作台方案](../跨设备开发工作台方案.html#overview) · [安装、升级与验收](../PORTABLE_CHECK.md)
- Windows 11、Python 3.14.7、Git 2.56.0.windows.4、Codex CLI 0.170.0。输入使用隔离的 Windows 用户数据目录、R20 生成的数据库、合成设置、项目和 Codex 记录。用 `python -B -m unittest discover -s tests -t . -p 'test_*.py' -v` 跑完整回归，235 项通过、2 项跳过，182.937 秒。跳过项分别需要可用的 Windows DPAPI 用户配置和可选钉钉 Stream SDK。首轮新增项目后台后，Web 测试的固定线程数断言失败，改为检查实际需要的四个工作线程后通过。用 `python -B tests/smoke_desktop.py --output build-exe/verification/desktop-upgrade-20261120/qt-r21` 验证真实 Qt 界面选择母目录、显示新项目和打开原会话条目。
- 按 `scripts/build-exe.ps1 -AppName Lanxi-Glass-R21-Portable` 的 PyInstaller 参数隔离构建。首轮有一处壁纸文件名拼写错误，修正后成功。旧版迁移首轮超时，复验时给隔离启动增加错误报告。便携自检随后报告测试用用户目录落在旧程序目录内部，改用独立同级目录后通过。正式目录 `release/Lanxi-R21-Portable/` 包内有逐文件 `SHA256.txt`，应用 SHA-256 为 `c4df2b8161980c2c0e56e33f4c96828b479a3e31e0a6d8001ff11df031aa520c`。
- 用 `python -B -m tests.smoke_desktop_upgrade --old release/Lanxi-R20-Portable/Lanxi-Glass-R20-Portable.exe --new release/Lanxi-R21-Portable/Lanxi-Glass-R21-Portable.exe --output build-exe/verification/desktop-upgrade-20261120/upgrade.json` 运行实包迁移及异目录重启，5.19 秒通过。验证配置、配对、路径、随记、已完成待办、回复及新写入数据能继续读取。在最终目录执行 `验收.cmd --mode local --output build-exe/verification/desktop-upgrade-20261120/portable-r21.json`，6 项通过，10.13 秒。验证空工作区克隆、代码和记忆双向更新、全局规则、会话记录及 12 MiB 文件过滤。
- 用 `python -B -m tests.smoke_personal_flow --output build-exe/verification/desktop-upgrade-20261120/terminal-lanxi-terminal.json` 验证从终端开始，经真实 Codex App Server 恢复并写入下一轮，再返回终端续写。使用两个独立项目路径、两个隔离 Codex 主目录和回环模型，三轮会话 ID 一致，第二写入者被锁挡住，最终 Git 提交相同。日志及报告位于 `build-exe/verification/desktop-upgrade-20261120/`。真实第二台电脑及其网络条件尚待现场验收。

## 2026-11-19 云协调服务与 HTTPS 部署

- 基线 `70e320a` 已包含 R20 的个人同步协议和协调接口。本轮增加云端独立构建、Caddy HTTPS、设备登记工具、健康检查和持久卷。已有 Windows 客户端沿用 HTTPS 设备配置。服务启动默认沿用原本机地址。Compose 的 Python 实例在内部网络监听，并将并发请求上限设为 24。设备令牌由管理命令生成，首次登记失败或重复登记时保留已存在的设备文件。 [部署和验收说明](../CLOUD_COORDINATOR.md)
- Windows 11、Python 3.14.7、Git 2.56.0.windows.4。输入为合成设备、临时证书、临时 SQLite、一个普通 Git 大文件夹具、两个不同的 Windows Codex 主目录。执行 `python -B -m tests.smoke_cloud_coordinator --output build-exe/verification/cloud-coordinator-20261119/windows-https.json`，8.82 秒通过。验证错误令牌与不同空间隔离、旧版本写入拒绝、服务重启后读取旧记录、两客户端项目自动发现、克隆、代码和相对目录记忆回传，以及同一个 Codex 会话记录恢复。10 MiB 减 1 字节的记录可双端读写，达到 10 MiB 回应 HTTP 413。
- Ubuntu WSL、Python 3.14.4。使用 `python3 -m pip install --target build-exe/verification/cloud-coordinator-20261119/linux-deps -r requirements-web.txt` 安装到独立输出目录。用相同测试分别验证源码后端与解包后的后端，耗时 3.47 和 3.88 秒。Linux 验收覆盖服务端协议，原生 Windows 会话恢复由上一项验证。运行 `python -B -m unittest -v tests.test_coordination tests.test_personal_flow tests.test_compute_queue tests.test_compute_worker`，40 项通过，44.643 秒。
- 沙箱内首次查看 WSL 被 Windows 拒绝，授权后复验成功。获取上游 Compose 和 Caddy 验收工具的 GitHub 附件超时，随后用 Ubuntu 软件包下载并解包到输出目录。复制发行包后用 Docker Compose 2.40.3 的 `config --quiet` 和 Caddy 2.6.2 的 `validate --adapter caddyfile` 验证生产配置。未启动 Docker 容器，未向公网申请证书，未接入真实阿里云服务器。
- 发布包为 `release/Lanxi-Server-HTTPS.zip`，包含 22 个部署源文件，SHA-256 为 `5977eb8f7fe7e014eaf65dc44f3a3aa6eeb2d033140994d43dd9e3e60a70413f`。本轮输出位于 `build-exe/verification/cloud-coordinator-20261119/`，核心证据包括 `windows-https.json`、`linux-https.json`、`packaged-linux-https.json` 和 `regression.txt`。

## 2026-11-19 R20 便携分发与会话 ID

- 从主线 `94da2b2` 增加 Windows 便携分发和验收入口。在「案头诸事」右键点击任一 Codex 会话，可查看完整 ID 并复制。本地及已同步到本机的会话适用同一入口。实际续接使用 `codex resume -C "本机项目路径" "会话 ID"`。 [使用和验收](../PORTABLE_CHECK.md)
- 首轮 R19 试验在受限环境下无法关闭临时测试进程，清理临时 SQLite 时报告文件占用。正常环境复验又发现同一 Windows 会话中的日常实例占用了单实例锁。R20 验收通过独立临时运行目录、Qt offscreen 和测试实例标记启动，并用 Windows Job 对象限定临时进程的清理范围。验收只写测试仓库、分享目录中的本次测试子目录及本机 JSON 报告。第一轮通过后检查重定向的中文输出，改为显式 UTF-8 后再次打包和复验。
- 在 Windows、Python 3.14.7、Git 2.56.0.windows.4 环境，运行 `python -B -m unittest -v tests.test_desktop_shutdown tests.test_codex_history tests.test_coordination tests.test_personal_flow`，24 项通过，21.055 秒。运行 `python -B tests/smoke_desktop.py --output build-exe/portable-check-20261119/desktop-r20` 通过，验证从右键命中的会话读取 ID，弹窗和剪贴板收到同一值。
- 用和 `scripts/build-exe.ps1 -AppName Lanxi-Glass-R20-Portable` 一致的应用构建参数在独立目录打包。用 `python -m PyInstaller --onefile --console --name Lanxi-Verify --paths . tests/smoke_personal_exe.py` 打包验收程序。交付在 `release/Lanxi-R20-Portable/`。应用 SHA-256 为 `18cf363f3bcf2308df70833d25b68c14880be7890abecbbf162f6541d4e52c6b`，验收程序为 `6cbb79c8ec59643bfc0a6e3228ed2fb74839b365243f614997431f07210698f2`。文件夹内 `SHA256.txt` 包含每个分发文件的校验值。
- 在本机运行交付文件夹中的 `验收.cmd`，本机自检 6 项通过，10.36 秒。在同一台机器上同时运行源端和接收端，使用带空格的共享测试路径和两个独立程序实例，分别通过 2 项和 5 项，10.40 秒和 7.47 秒。验证了空工作区克隆、12 MiB 对象过滤、项目相对目录、全局规则、会话记录落盘和双向回传。真实两台电脑的 SMB 连接及 GitHub 权限未在本轮实测。
- 产物和复现脚本位于 `build-exe/portable-check-20261119/`。双端程序的命令行复现入口为 `python -B build-exe/portable-check-20261119/verify-bundle.py`，汇总见 `portable-local-final.json`、`portable-source.json`、`portable-receiver.json`。R19 的测试基线与完整回归见下节。

## 2026-11-19 三路代码集成审查

以个人同步版 `c590e2b` 为基线，核对三个任务的交付记录、GitHub 引用和本机工作区。远端只有 `main`，查询时未发现 PR 引用。以下提交均已是该基线的祖先。

| 来源 | 交付提交 | 集成复核 |
|---|---|---|
| 前端 | `36cf30f` | Codex、Claude 的 Markdown 对谈和已有 Qt 操作 |
| 后端 | `144990b`，较早的产物传输为 `241605a` | 原生会话历史、协调项目接续和项目同步 |
| 壁纸 | `b289163`，合并点 `a478b96` | 本机 macOS 图库、内置图库、轮换与固定模式 |

- 用 `git worktree list --porcelain` 和 `git merge-base --is-ancestor` 核对了审查开始时的 20 个工作区。四个有未提交改动的目录是 R10 至 R13 的旧发布快照。在独立 Git 索引重建其工作区内容，分别与 `b4b65c6`、`9253b24`、`6f462d4`、`de12251` 三方试合并，所得目录树与各自已交付提交完全相同。两份壁纸 autostash 的代码和测试已进入主线，文档差异为旧版本交付措辞。另一份 stash 新增的原路径切换记录已逐行存在。原工作区与三份 stash 均保留。合并判定见 `build-exe/verification/three-agent-review-20261119/git-audit.json`。
- 修正个人接续与项目交接同时启用时的次序。工作区正在切换或必需资源未就绪时沿用原有等待逻辑；核对通过后沿用 R18 映射到当前设备的会话目录。个人通道配置只有设备、没有受控项目时，原生续接直接使用已恢复的本机记录。桌面「启卷」等待期间移除项目会结束等待，Claude 的项目查找使用自己的目录，防止与同 ID 的 Codex 会话串到另一个项目。
- 多版本共存时，登录启动脚本可通过 `-ExecutableName` 明确选择 R19。无参安装继续使用原来的默认包与源码回退顺序。用拦截 `New-ScheduledTaskAction` 的测试替身核对默认包、明确指定、缺包停止、源码回退四种结果。第一次测试替身的捕获结果停留在被调用脚本作用域，修正采集方式后全部通过。`verify-startup.ps1`、`startup.txt`、`startup-02.txt` 保存在本轮输出目录，未注册现场计划任务。
- Windows、Python 3.14.7、Git 2.56.0.windows.4、Codex CLI 0.170.0；测试使用独立源码目录、临时项目和本机回环模型。执行 `python -B -m unittest tests.test_codex_history tests.test_coordination tests.test_personal_flow -v`，23 项通过，21.125 秒。执行 `python -B -m unittest discover -s tests -t . -p 'test_*.py' -v`，**231 项中 229 项通过、2 项跳过**，197.220 秒。跳过项为沙箱用户的 DPAPI 和可选钉钉 Stream SDK。增加目录归属断言后，协调专项 11 项再通过，0.839 秒。上一版 R18 的 230 项结果在下一节。
- 执行 `python -B tests/smoke_desktop.py --output ../qt`，验证 Markdown、托盘、任务操作和「启卷」；`python -B -m tests.smoke_codex_history --output ../history` 验证三轮六条可见消息；`python -B -m tests.smoke_personal_flow --output ../native.json` 验证两套 Codex 主目录间三轮同线程续接、本机工作目录和写入锁。另在本轮源码目录以 Qt offscreen 运行主仓库既有的 `build-exe/verification/applewalls-r14-integrated/verify_rotation.py`，逐张解码现有 74 张壁纸和模糊层，循环回到 6016 × 3384 的 Mavericks。固定模式关闭计时器。轮换通过主动推进验证，没有等待 74 个半小时。
- 使用与 `scripts/build-exe.ps1 -AppName Lanxi-Glass-R19-Integrated` 相同的 PyInstaller 参数构建隔离源码，发布到 `release/Lanxi-Glass-R19-Integrated.exe`。大小 61,598,364 字节，SHA-256 为 `910F4BA99E0A4ABF06D4E4C177278AAA5F75F838C3220C60EC292A2AE9D2A2C3`。执行 `python -B -m tests.smoke_personal_exe --exe ../package/Lanxi-Glass-R19-Integrated.exe --output ../exe-flow.json`，两个隔离 EXE 配置在 8.47 秒内完成后台克隆、项目记忆原相对目录恢复及修改回传。`python build-exe/verify-lanxi-launch.py Lanxi-Glass-R19-Integrated.exe` 在 1.52 秒内检测到独立副本的窗口和 SQLite。该轮验收没有切换日常运行实例，也未实测物理机间网络延迟。

本轮独立输出在 `build-exe/verification/three-agent-review-20261119/`，包括 `targeted.txt`、`full.txt`、`coordination-final.txt`、`qt/`、`history/result.json`、`native.json`、`wallpapers.txt`、`package.txt` 和 `exe-flow.json`。

## 2026-11-19 个人后台同步与项目相对记忆目录

- 基线 `0b07317` 的「启卷」只打开当前设备已有的项目目录。个人版增加后台发现项目、按大小过滤克隆、追随远端分支和自动发布已保存的代码。笔记、原生长期记忆记录和已完成会话按 GitHub 仓库标识和项目相对路径同步。全局规则保存在各机 Codex 主目录。 [操作与配置](../PERSONAL_SYNC.md)
- Windows、Python 3.14.7、Git 2.56.0.windows.4、Git LFS 3.8.1、Codex CLI 0.170.0。输入为两个独立仓库路径、两个独立 Codex 主目录、私人共享目录或带设备鉴权的回环协调服务。Git 测试使用 URL 重写，将测试用 GitHub 地址指向启用过滤能力的本地 bare 仓库。生产上限 10 MiB，默认轮询间隔 10 秒。为检查临界行为，专项将 Git 上限缩到 16 KiB，用 32–34 KB 随机内容验证对象在接收端 Git 对象库中缺失。
- 复现命令 `python -B -m unittest tests.test_personal_flow -v`。首轮 3 项中 1 项因测试 SQLite 连接未关闭而无法清理，7.458 秒。第二轮 6 项中 1 项失败，14.924 秒。大小互换时 Git 快进默认生成差异统计，尝试读取已过滤的大对象。关闭合并差异统计后，单项复验 2.471 秒通过。加入 HTTP 双端验收后的专项 7 项全部通过，18.885 秒，覆盖空目录克隆、双向保存、非冲突合并、冲突与暂存保留、LFS 指针、远端无过滤能力、鉴权和版本校验、相对记忆目录变更、笔记与长期记忆删除。项目清单和全局规则另经真实 HTTP 协调端点验收。
- `python -B -m tests.smoke_personal_flow --output ../native-06.json` 使用真实 CLI 和回环模型，在两个 Codex 主目录间完成笔记本 → 台式机 → 笔记本三轮往返，线程 UUID 保持一致，最终代码提交相同。真实 App Server 在接收机用本地项目目录恢复线程，持有期间拒绝第二个原生写入锁。首轮接收机尚无 Codex 主目录，已修正创建顺序。随后三次 CLI 往返均通过。新增 App Server 探针首次因测试机未配置回环模型提供方而失败，两端各配置测试提供方后通过，该次命令墙钟约 4.55 秒。模型登录配置保持各机独立。
- `python -B -m unittest discover -s tests -t . -p 'test_*.py' -v` 中途基线回归 225 项，223 项通过、2 项跳过，179.016 秒。加入 HTTP 验收后的回归 229 项，227 项通过、2 项跳过，186.508 秒。跳过项为当前沙箱用户配置不支持 DPAPI 和缺少可选钉钉 Stream SDK。源码 Qt 验收 `python -B tests/smoke_desktop.py --output ../desktop-02` 通过，实际点击「启卷」，验证后台未准备好时等待、完成后按本机路径仅启动一次 VS Code；编辑器进程由测试替身接收。首次通用桌面回归也通过。
- 使用项目构建脚本相同的 PyInstaller 参数，首轮隔离构建得到 `Lanxi-Glass-R18-Flow.exe`，大小 61,599,462 字节，SHA-256 为 `255023ECE12F06C6F5370DEF88107673B8AAEFF5CC2F4D349DA9CAFAA7A9BA3A`。可从项目根执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R18-Flow` 复建。用 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R18-Flow.exe` 启动独立副本，在 1.42 秒内检测到原生窗口和本地 SQLite。
- 输出位于 `build-exe/verification/personal-flow-20261119/`，包括 `targeted-01.txt` 至 `targeted-03.txt`、`sparse-03.txt`、`full-01.txt` 与 `full-02.txt`、`native-*.txt/json`、`desktop-01/`、`desktop-02/` 和 `build-01.txt`。本轮使用独立测试数据，没有测量真实两台物理设备的 GitHub/SMB 网络时延、项目规模下的常驻资源消耗和长期离线恢复时间。现场配对和运行状态仍待两台设备接入。

- 补查项目笔记写入中断时的恢复，先写完新内容，再切换原文件。注入磁盘写失败、文件链接失败均保留原文。专项最终 8 项通过，20.326 秒，见 `targeted-04.txt`。完整回归 **230 项，228 项通过、2 项跳过**，188.088 秒，见 `full-03.txt`。真实 CLI、桌面工作目录和原生写入锁复验通过，见 `native-07.json`。
- 最终发布包 `release/Lanxi-Glass-R18-Flow.exe` 为 61,598,404 字节，SHA-256 为 `635BBDBC927EB9DB989B9E17AD787BB8FEBD03813DFF35EA112CB0663CAB8F44`，独立桌面副本在 1.41 秒内显示窗口并创建数据库。额外使用两个隔离的 EXE 配置运行完整后台。首次同时启动触发同一 Windows 会话的单实例约束，清理时遇到文件占用。改成笔记本 → 台式机 → 笔记本顺序运行后，8.24 秒完成零项目端自动克隆、两端 `notes/Codex/context.md` 归位、全局规则恢复和编辑回传，两端代码提交一致。复现命令 `python -B -m tests.smoke_personal_exe --exe ../package-final/Lanxi-Glass-R18-Flow.exe --output ../exe-flow-02.json`。该时间包含三次本机启动和关闭，使用两秒轮询及本地 Git 传输，不能用于估算真实跨机延迟。最终打包日志在 `build-02.txt`。

## 2026-11-19 澜汐与 Codex 终端历史接续

- 基线 `d127e44` 的桌面对谈只显示 `replies.sqlite3`，集成底座为含本地壁纸轮换的 `a478b96`。环境为 Windows 11 24H2、Python 3.14.7、Codex CLI 0.170.0、PySide6 6.12.0。桌面读取相同 `CODEX_HOME` 的原生可见消息，与 SQLite 实时记录按轮次及消息 ID 去重。参考旧版可见事件字段，并核对真实本地测试会话的已完成消息格式。 [Codex 旧版事件协议](https://github.com/openai/codex/blob/rust-v0.100.0/codex-rs/protocol/src/protocol.rs)
- `python -m unittest -v tests.test_codex_history tests.test_conversation tests.test_monitor tests.test_web` 首轮 19 项、2 项失败，1.475 秒。合成 JSON 行超过缩小版测试上限，且 SQLite 夹具使用实际时间，与固定的原生事件时钟不一致。修正夹具后，同命令加入 `tests.test_task_control tests.test_claude`，68 项通过，8.656 秒。覆盖身份校验、错误和内部上下文过滤、未写完整的行、原地替换、旧版事件、回退轮次以及 Web 对话权限范围。日志在 `build-exe/verification/codex-history-20261119/targeted-01.txt` 和 `targeted-02.txt`。
- 在独立源码目录先后执行 `python -m tests.smoke_codex_history --output ../native-01` 和 `python -m tests.smoke_codex_history --output ../native-02`。真实 CLI 通过回环模型夹具开同一会话，澜汐的 App Server 接续一次，最终 CLI 读到澜汐回复上下文后再追加一轮。桌面正常读取入口显示三轮六条消息，并核对仅桌面轮次进入运行日志。未调用真实模型或读取用户会话。合成长日志有 64 段各 512 KiB 的工具数据与 50 条可见消息，共 33,576,591 字节。每轮各测 5 次，16 MiB 窗口冷读、缓存命中及追加一条的中位数依次为 50.226、0.232、6.580 毫秒，复验为 43.765、0.240、5.441 毫秒。旧版只读 SQLite，未测同口径原生读取。各轮结果在上述输出中的 `result.json`。未测用户实际历史库规模与峰值内存。
- 设置 `QT_QPA_PLATFORM=windows` 运行 `python tests/smoke_desktop.py --output ../desktop-smoke`，10.745 秒通过。用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R17-History`，构建用时 45.807 秒。检查归档确认 `codex_history`、`desktop_qt` 及 `desktop_glass` 均内置，输出复制前后 SHA-256 一致。发布文件为 `release/Lanxi-Glass-R17-History.exe`，61,545,474 字节。桌面功能测试使用源码入口，未切换现有运行实例。 [本机会话使用说明](../MULTI_CLIENT.md#codex-桌面与-vs-code-终端续接)
- 在同一独立源码目录使用主项目虚拟环境运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v`，215 项中 213 项通过，224.488 秒。DPAPI 测试因沙箱用户配置不可用跳过，另有 1 项可选 Stream SDK 跳过。继续保留 `regression-01.txt` 原始日志。

## 2026-11-19 macOS 高清壁纸库与 R14 实机切换

- Windows 11、Python 3.14.7、PySide6 6.12.0。基线有三张内置背景。本次核对 AppleWalls 的 16 个合集、89 个条目。下载按钮成功返回 20 张后报告当日限额已达。从独立档案取得 56 张对应画面，来源为 512 Pixels（47）、iDownloadBlog（4）、Basic Apple Guy（4）和 iClarified（1）。保留发布方文件，逐图解码，核对宽高与来源并计算 SHA-256。独立版本的文件格式或分辨率可与 AppleWalls 不同。
- 当次验收保存了 76 张，共 746,602,598 字节。74 张用于桌面，另有 2 张竖版。图片位于 Windows「图片」的 `澜汐/macOS`，同目录的 `下载清单.csv` 记录每项来源、尺寸、字节数和待续传状态。还有 Mac OS 9 的 Sage Foam 及 macOS 27 Beta 6 的 12 项待补。已登记 Windows 任务 `Lanxi-AppleWalls-20261119`，首次触发时间为本地 11 月 20 日 00:05，按服务端 Retry-After 续传，全部保存后自动注销。后续实际完成时间以清单为准。
- 在应用内沿用 30 分钟轮换和 0 分钟固定 Mavericks 的设置。运行 `python tests/smoke_desktop.py --output build-exe/verification/applewalls-r14-integrated`，Windows Qt 桌面回归通过。用 Qt offscreen 运行 `python build-exe/verification/applewalls-r14-integrated/verify_rotation.py`，手动触发完整一轮，74 张原图和模糊层均加载，回到首张 6016×3384 Mavericks，固定模式计时器关闭。结果见同目录 `local-library.json`。未等待真实的 30 分钟间隔。
- 在 `def5d64` 和本次壁纸改动上运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R14`，生成 61,531,754 字节的 `release/Lanxi-Glass-R14.exe`。切换时未检测到 R13 进程。首次从临时命令启动后进程退出，随后通过资源管理器启动，独立复核了 R14 的实际进程路径、窗口标题「澜汐」和响应状态。
- 两次短启动后未检测到持续运行的窗口，Windows 应用错误日志中未发现 R14 崩溃条目。最后通过当前登录用户的交互会话独立启动，确认窗口标题、响应状态与会话 ID。该启动项随程序退出自动注销。

## 2026-11-19 计算产物回传与断点续传

- 基线 `8f6d6de` 只上报退出码与清单散列。环境为 Windows 11 24H2、Python 3.14.7、SQLite 3.50.4 与 Git 2.56.0.windows.4。测试使用虚构设备、独立 Git 仓库和回环协调服务。任务在独立工作目录生成可复现的 9 MiB 二进制文件及一个零字节文件。上传先落盘 4 MiB，模拟协调中心回包丢失，执行端重启后只上传余下 5 MiB。下载落盘 4 MiB 后模拟中断，复跑只取余下 5 MiB。
- `python -m unittest -v tests.test_compute_worker tests.test_compute_queue tests.test_coordination` 首轮 29 项通过，22.603 秒。覆盖执行凭据、只有原提交设备能下载、回执散列匹配、清单与分块大小、越界路径、大小写冲突、重复分块、错误散列及中心重启。开始时一条 PowerShell 内联建库命令因引号解析未运行成功，后续回环测试正常创建实例。原始日志为 `build-exe/verification/compute-artifacts-20261119/targeted-01.txt`。
- 设 `LANXI_MEASURE_COMPUTE_ARTIFACTS=1` 后运行三次 `python -m unittest -v tests.test_compute_worker.WorkerTests.test_artifacts_resume_upload_and_download_through_the_http_service`。复测也从 CLI 下载入口读取同一结果，并通过改动协调中心测试文件验证同长度数据损坏会被拦截。剩余 5 MiB 上传与完成回执分别用时 107.73、67.50 和 113.11 毫秒，下载与落盘分别为 74.77、66.40 和 38.05 毫秒。复用的 4 MiB 未再次传输。日志为同目录的 `artifact-timing-1.txt` 至 `artifact-timing-3.txt`。旧版没有对应的产物网络传输基线。未测真实网络、并发吞吐、峰值内存或 128 MiB 完整作业。
- 运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v`，211 项中 208 项通过，3 项分别因独立目录无本地适配器虚拟环境、DPAPI 用户配置不可用及缺少可选 Stream SDK 跳过，耗时 178.982 秒。合入前端的 Markdown 提交 `36cf30f` 后，专项 29 项再通过，21.553 秒。设 `QT_QPA_PLATFORM=windows` 后运行 `python tests/smoke_desktop.py --output ../integrated-smoke`，10.760 秒通过。输出位于上述目录的 `regression-01.txt`、`integrated-targeted.txt` 和 `integrated-smoke.txt`。
- 回传只处理成功作业已登记的产物，下载到独立目录，失败和取消依旧保留本机日志。自动按版本写回原工作树、检查点、实际 GPU 探测及物理设备联调待完成。 [回传和下载配置](../PROJECT_CONTROL.md#回传成功作业的文件)

## 2026-11-19 个人版 Python 后端审查

审查基线为 `b3f7e9f`。当时已有的 131 项定向回归通过。下列问题来自对实际调用路径的检查。

| 优先级 | 位置 | 触发条件与后果 |
|---|---|---|
| P1 | `ScopedPolicy.allows` | 匹配命令白名单时忽略审批类型和网络上下文，可能自动批准需要人工决定的请求。 |
| P1 | `TaskControl.submit / _drain` | 同一轮先接入后移出时，优先执行的移出会被旧接入覆盖。 |
| P1 | `TaskControl._perform` | Codex 手动消息或继续操作等待写锁期间，用户新提交的暂停或移出可能晚于最后一次发送检查。 |
| P2 | `ReplyLog.record` | 切换任务后旧线程失去控制权，已启动轮次的最终回复也被日志过滤。 |
| P2 | `coordination_client.terminal` | 子进程结束时遇到断网，关闭事件丢失，协调中心持续保留 CLI 占用。 |
| P2 | `monitor.poll / poll_claude` | 每轮都读取全部历史助手回复并逐条检查通知去重，轮询成本随历史增长。 |

**修复与复核**

- 实施基线 `d127e44`。普通命令审批同时核对审批类型与网络上下文。两个客户端的任务队列在移出时取消旧接入，执行中的接入也要核对最新意图。Codex 手动消息、插队及继续在传输写锁内复核停止意图，跨机项目在取得写入许可后再检查一次。
- 回复日志按线程和轮次识别尚未结束的工作，已启动轮次切换后仍可记录回复。CLI 与 IDE 复用持久关闭队列，正常退出先落盘，后台或下次登记的终端启动时补发。两端的回复通知均按每批 64 条处理，旧库首次登记历史回复，实时回复优先，重启沿用既有通知去重键。
- 环境为 Windows x64、Python 3.14.7、SQLite 3.50.4。使用临时 SQLite、虚构会话、模拟 RPC 和本机回环协调服务。原始结果放在项目的 `build-exe/verification/python-backend-review-20261119/`，以下命令从项目源码根目录执行。
- `python -B -m unittest -v tests.test_daemon_support tests.test_task_control tests.test_coordination tests.test_handoff tests.test_monitor tests.test_ide_activity tests.test_quota_recovery`。首轮 81 项，79 项通过、1 项失败、1 项跳过，29.247 秒。新增断言误将已接入会话的写入权视为可立即释放，改为验证消息未写出及空闲连接正常退出后释放。第二轮 82 项，81 项通过、1 项跳过，48.069 秒。记录为 `targeted-01.txt` 与 `targeted-02.txt`。增加接入前的传输检查后，`python -B -m unittest -q tests.test_task_control tests.test_auto_progress tests.test_coordination tests.test_daemon_support` 共 49 项通过，5.604 秒，见 `control-final.txt`。
- 空闲通知轮询使用 2,000 条已通知的合成助手回复，每条正文由 `fixture reply ` 重复 12 次组成。预热后交替执行旧版全表读取及逐条去重、修复版本的空队列读取，各测 5 次。初版中位数分别为 69.683 和 0.100 毫秒。加入历史迁移期间的实时回复优先级后复测，分别为 37.153 和 0.113 毫秒。复现命令为 `python -B -m tests.bench_reply_notifications`，逐次读数见同目录 `idle-01.json`、`idle-02.json`。未控制并行测试负载，耗时仅涵盖本地通知轮询。真实模型收发、物理设备断网、系统弹窗与用户历史库未实测。

- 首轮全量执行 `python -B -m unittest discover -s tests -t . -p 'test_*.py' -v`，218 项中 215 项通过、3 项跳过，214.218 秒，见 `full-01.txt`。独立工作树缺少适配器约定路径下的解释器，另外两项依赖当前 Windows 用户的 DPAPI 和可选钉钉 Stream SDK。
- 集成 `144990b` 的本地会话历史读取后，回复库同时保留历史查询索引、按轮次结束事件的索引和通知队列。用仓库内的基准脚本再次测量，旧路径和新路径的空闲轮询中位数为 39.540 和 0.075 毫秒，原始五次读数见 `integrated-idle.json`。
- 对集成版本 `283d957`，在独立工作树运行 `python -m venv --without-pip .venv` 为 PowerShell 适配器提供约定的解释器，再使用项目主虚拟环境执行相同的全量测试命令。222 项中 220 项通过、2 项跳过，耗时 173.410 秒。终端适配器与历史会话回归通过，跳过项为当前 Windows 用户的 DPAPI 和可选钉钉 Stream SDK。完整日志为 `integrated-01.txt`。

## 2026-11-19 对谈 Markdown 排版

- 基线 `8f6d6de`，Windows 11 x64、Python 3.14.7、PySide6 6.12.0。在现有消息气泡内使用 [Qt Markdown 解析](https://doc.qt.io/qt-6.12/qtextdocument.html#setMarkdown)，复用后台排版和最近会话缓存。用虚构的中英文消息验证标题、加粗、嵌套列表、表格、引用、围栏代码和链接。代码保持缩进并随可用宽度换行。
- 在 `build-exe/verification/lanxi-markdown/worktree` 设置 `QT_QPA_PLATFORM=windows`，运行 `python tests/smoke_desktop.py --output ../windows-final`。回归通过，覆盖小窗口无横向滚动、点击网页链接和不触发本地文件链接，原生截图为 `windows-final/markdown-desktop.png`。首轮 `windows-01` 通过，预览只显示上半段内容。随后 `windows-release` 和 `windows-verified` 的旧滑块固定等待断言遇到时序波动，改为等待滑块到达目标。最终截图的代码行连续使用浅蓝底。源码专项 `python -m unittest -q tests.test_codex_desktop tests.test_desktop_projects tests.test_desktop_shutdown tests.test_claude tests.test_web` 共 41 项通过，8.493 秒。开发方案通过 17 个 ID 和 18 个页内链接检查。
- 设置 `QT_QPA_PLATFORM=offscreen`，运行 `python tests/bench_task_switch.py --output ../baseline`，初版和复测分别以 `../markdown-01` 和 `../markdown-final` 为输出。夹具为每个任务 35 条长回复及固定 140 毫秒读取延时。前两次未缓存切换，基线用时 178.45 和 183.04 毫秒，初版 198.78 和 213.71 毫秒，最终 192.98 和 190.04 毫秒。最近三次缓存切换最终为 8.19、10.39 和 7.88 毫秒，基线为 6.81、10.17 和 9.92 毫秒。三轮的事件循环等待中位数分别为 1.88、2.55 和 1.46 毫秒。首次任务的预加载命中不固定，按相同序号比较未缓存切换。未测真实模型流式输出或用户历史库。
- 用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R16-Markdown`。首包保存在 `package-first`，最终构建约 40.72 秒。交付 `release/Lanxi-Glass-R16-Markdown.exe`，61,534,734 字节，复制前后 SHA-256 一致。运行 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R16-Markdown.exe`，于 1.63 秒在 `lanxi-folder-package-gvotn0lu` 验证新包启动、原生窗口和 SQLite。

## 2026-11-19 计算作业远程取消

- 基线 `def5d64`，Windows 11 24H2、Python 3.14.7、SQLite 3.50.4、Git 2.56.0.windows.4。使用临时项目仓库、独立 SQLite 与回环协调服务。合成任务和子进程持续等待，子进程原计划在四秒后落盘；断线与租约到期用测试时钟模拟。取消沿用五秒续租，监督进程每 250 毫秒检查本机取消信号。
- `python -m unittest -v tests.test_compute_worker tests.test_compute_queue tests.test_coordination`，26 项通过，19.158 秒。覆盖仅提交设备可取消、未开始立即释放、旧凭据拒绝、旧数据库升级、取消请求重放、自然完成与取消交错、失联期间保留执行槽、重连终止进程树及丢失回执重报。输出为 `build-exe/verification/compute-cancel-20261119/targeted-01.txt`。
- 设 `LANXI_MEASURE_COMPUTE_CANCEL=1`，运行 `python -m unittest -v tests.test_compute_worker.WorkerTests.test_remote_cancel_stops_tree_after_reconnect_and_retries_receipt` 三次。开始重连处理到进程树退出并得到本地清单用时 82.98、60.63、77.96 毫秒，随后模拟丢失回执并成功重报用时 30.36、42.79、49.50 毫秒。原始数据为上述目录的 `cancel-timing-1.txt` 至 `cancel-timing-3.txt`。未测从用户发出取消到执行端下一轮续租的等待、真实网络、硬件 GPU 或峰值内存。
- 在独立目录运行 `python -m tests.bench_compute_queue --output ../queue-bench-01`，从源码目录执行，以 0 条及 10,000 条历史与排队记录分别测 30 次。本机 SQLite 成功领取及无可领轮询中位数，空库为 8.088 和 1.312 毫秒，10,000 条时为 9.141 和 2.271 毫秒。旧版同口径为 9.569、1.562 和 12.510、3.194 毫秒。两轮为独立实测，未控制主机负载。结果为 `queue-bench-01/result.json`。
- 在独立源码目录运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v`，208 项中 205 项通过、3 项跳过，196.690 秒。跳过项分别依赖工作区本地虚拟环境的 PowerShell 适配器、Windows 用户配置中的 DPAPI 和可选 Stream SDK。日志为 `build-exe/verification/compute-cancel-20261119/regression-01.txt`。
- 本机验证了取消后的退出码和清单散列回执。产物回传、检查点、实际 GPU 使用和设备联调待完成。 [执行和取消命令](../PROJECT_CONTROL.md#硬件计算队列)

## 2026-11-19 桌面改动复核与壁纸解码耗时

- Windows 11 24H2、Python 3.14.7、PySide6 6.12.0。复核主工作区 `e658b17` 加未提交的 R14 本地壁纸改动，以及独立工作区 `19a1f20` 所含的 `de12251` 顶部淡入菜单。设 `QT_QPA_PLATFORM=windows`，分别运行 `python tests/smoke_desktop.py --output <对应输出目录>`。主工作区 9.305 秒通过，独立工作区 10.261 秒通过。截图核对了待办和展开的顶部菜单。测试使用虚构会话，主工作区烟测替换了本地图库发现，未连接真实模型或提交审批。
- 主工作区设 `QT_QPA_PLATFORM=offscreen`，运行 `python tests/bench_task_switch.py --output build-exe/verification/desktop-review-20261119/task-switch`。35 条长消息、首次读取模拟等待 140 毫秒，连续切换 6 次。主线程事件循环等待中位数 1.57 毫秒，上次同口径为 1.62 毫秒。两次冷读耗时 176.25 和 176.68 毫秒，四次缓存命中为 7.14 至 9.40 毫秒。全 6 次历史显示中位数 8.56 毫秒，上次为 13.47 毫秒。未测真实大型会话库、实际接入延时或 EXE 性能。
- 同一主工作区和 Qt offscreen 下运行 `python build-exe/verification/desktop-review-20261119/wallpaper_probe.py`。空测试图库使用应用内置图片，和临时图库中的两张 7680×4320 合成 PNG 分别测 5 次。`OceanBackdrop` 初始化中位数为 22.72 和 134.78 毫秒，界面线程进入轮换到返回分别为 37.92 和 112.53 毫秒。渐变期间四张解码图像合计占 41,022,400 和 265,465,600 字节，未测进程总内存、绘制帧率和真实照片。当前图像解码与轮换在 GUI 线程同步执行。
- 运行输出与复现脚本位于 `build-exe/verification/desktop-review-20261119/`，含 `smoke/`、`remote-smoke/`、`task-switch/result.json`、`wallpaper_probe.py` 和 `wallpaper.txt`。这次复核只更新验收记录。

## 2026-11-19 Windows 计算执行与结果恢复

- 基线 `a317cc9` 已提供持久化计算队列与领取协议。本轮用 Windows 11 24H2 x64、Python 3.14.7、Git 2.56.0.windows.4 和独立的回环协调服务，验证本地批准的程序在固定提交上执行。临时仓库包含两份普通文件，原工作区保留未提交修改。合成任务读取固定输入，计算约 3 MiB 数据的 SHA-256，写出 64 字节产物，并输出比 2 MiB 日志上限多 85 字节的内容。超时夹具实际启动子进程并设定 3 秒超时。
- 运行 `python -m unittest -v tests.test_compute_worker tests.test_compute_queue`，首轮 12 项通过，12.079 秒。收到 HTTP 409 后出现未关闭返回体的警告，随后显式关闭响应。加入真实 `--once` CLI 进程退出后的补报测试后，再运行 `python -m unittest -v tests.test_compute_worker tests.test_compute_queue tests.test_coordination`，21 项通过，20.304 秒。已核对固定输入、令牌环境隔离、日志上限、产物损坏拦截、开始或完成回执丢失、超时子进程停止、过期领取与失败现场保留。日志为 `build-exe/verification/compute-worker-20261119-first.txt` 和 `compute-worker-20261119-targeted.txt`。
- 设 `LANXI_MEASURE_COMPUTE_WORKER=1` 后，运行 `python -m unittest -v tests.test_compute_worker.WorkerTests.test_pins_input_caps_logs_and_retries_finish_after_restart` 三次。恢复启动请求到本机监督进程创建分别为 34.34、34.39、35.68 毫秒。监督进程检出 Git、运行任务和落盘分别为 657.54、636.31、711.50 毫秒。重启后复验本机日志、产物并重试回执分别为 13.27、12.21、36.40 毫秒。各次原始结果为 `build-exe/verification/compute-worker-20261119-timing-1.txt` 至 `compute-worker-20261119-timing-3.txt`。专项与全量并行时另一轮监督进程耗时 2623.99 毫秒。旧版 SQLite 领取基准未包含 HTTP、Git、进程启动或实际计算，口径不同。
- 运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v`，首轮 200 项中 199 项通过，1 项可选 Stream SDK 跳过，225.826 秒。在独立工作区合入 `de12251` 后用主仓库的 Python 复验，201 项中 199 项通过，2 项跳过，193.674 秒。独立工作区缺少适配器测试用的本地 `.venv` 路径。随后在配置完整的主工作区运行 `python -m unittest -v tests.test_handoff.HandoffTests.test_bare_powershell_command_and_passthrough`，1 项通过，2.583 秒。三个日志为 `build-exe/verification/compute-worker-20261119-full.txt`、`compute-worker-20261119-integrated.txt` 和 `compute-worker-20261119-terminal.txt`。
- 计算端目前以 5 秒间隔空闲检查和续租。已知监督进程退出时可立即处理结果。日志和产物存本机，中心保存散列及退出码。峰值内存、真实 GPU、跨设备网络和长时间作业未测。产物回传、远程取消、检查点和物理设备联调待完成。 [计算执行配置](../PROJECT_CONTROL.md#启动-windows-执行端)

## 2026-11-19 硬件调度队列与设备选择

- 基线 `fedaeaf` 已有项目协调和设备认证。本轮在原协调数据库增加计算队列、执行心跳和按固定提交的 CPU/GPU 路由。作业以本机前台活动、登记运行配置和完整提交标识提交。环境为 Windows、Python 3.14.7、SQLite 3.50.4。设备、资源和提交均使用合成测试数据。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_compute_queue tests.test_coordination` 首轮 14 项通过，1.727 秒。加上已确认停止的作业恢复后，15 项通过，1.916 秒。覆盖项目与设备授权、前台活动、CPU 优先本机、GPU 优先家里、单卡显存、输入提交匹配、并发领取、过期凭据、协调重启和完成回执去重。日志为 `build-exe/verification/compute-queue-targeted-01.txt` 和 `compute-queue-targeted-02.txt`。
- 专项末轮 16 项通过，2.838 秒。真实 CLI 进程通过本机回环 HTTP 向协调服务提交并查询作业，返回同一个固定提交和作业 ID。输出为 `build-exe/verification/compute-queue-targeted-03.txt`。
- `.\.venv\Scripts\python.exe -m tests.bench_compute_queue --output build-exe/verification/compute-queue-01`。各测 30 次成功分配和无可领取作业时的轮询。空库的成功领取和空轮询中位数分别为 9.569 和 1.562 毫秒。预填 5,000 条排队及 5,000 条已完成记录时分别为 12.510 和 3.194 毫秒。测试直接调用本机 SQLite 队列，包含事务和写入时间。结果见 `result.json`。未测 HTTP 网络、真实 CPU/GPU 执行时长、并发端到端吞吐及内存。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 全量运行 196 项，195 项通过，1 项可选 Stream SDK 跳过，177.412 秒。输出为 `build-exe/verification/compute-queue-regression-01.txt`。
- 当前阶段交付队列和执行端协议。家庭执行进程、输入准备、取消与检查点、日志和结果文件校验及交付仍待实现。 [计算队列配置与调用](../PROJECT_CONTROL.md#硬件计算队列)

## 2026-11-18 必需 LFS 对象纳入交接

- 基线 `7141cd9` 提供独立的 LFS 预取命令。本轮把已提交且登记的 LFS 指针纳入签名交接，接收后按所需对象校验本机就绪状态，缺件时保留预备目录。环境为 Windows、Python 3.14.7 和 Git 2.56.0.windows.4。测试使用生成的 Git 仓库、会话和回环 HTTP 服务。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_handoff_lfs tests.test_lfs_assets` 首轮 8 项，4 项失败或出错，用时 5.699 秒。Windows 上 `lstat` 与 `fstat` 给出的创建或变化时间不同，初版因此误拒绝合法缓存。改用同接口比较读前和读后的路径时间，并校对文件句柄身份后，8 项通过，14.527 秒。补测跨版本复用及改动回执后 9 项通过，22.040 秒。日志为 `build-exe/verification/handoff-lfs-targeted-01.txt` 至 `handoff-lfs-targeted-03.txt`。已核对缺件、坏对象、签名清单与本机回执不一致、未提交 LFS 改动、待齐后切换和切换后缓存丢失。
- 运行 `.\.venv\Scripts\python.exe -m tests.bench_handoff_lfs --output build-exe/verification/handoff-lfs-64m-01`，数据为单个 64 MiB LFS 对象和少量普通工作区变更。封存与交换目录复制用时 1.638 秒。首轮扫描在 16 MiB 断线，耗时 1.095 秒且原路径未切换。第二轮只收剩余 48 MiB，续传、复验、合并与自动切换共 5.395 秒。已就绪后的重复扫描耗时 0.029 秒，LFS 内容散列次数为 0。结果见 `result.json`。上一版单独预取与本轮端到端口径不同。未测真实网络、并发任务切换或峰值内存。
- 运行 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 共 187 项，186 项通过，1 项可选 Stream SDK 跳过，182.704 秒。日志为 `build-exe/verification/handoff-lfs-regression-01.txt`。
- 同参数复验至 `build-exe/verification/handoff-lfs-64m-02`。封存与交换目录复制为 1.402 秒，中断扫描为 0.957 秒，续传与自动应用为 4.812 秒，就绪后的扫描为 0.028 秒，LFS 内容散列仍为 0 次。确认原路径可准备同一线程后，原生 Git LFS 3.8.1 从已校验缓存检出工作副本，用时 0.747 秒，散列相同且未增加下载请求。结果见该目录 `result.json`。
- 预备和原路径共用对象时在同卷复用硬链接，异卷使用校验后的复制。后台根据文件身份和时间复用本进程的校验结果，实际切换或续接仍复核内容。当前工作副本由原生 Git LFS 按需检出。家庭 Gitea、真实 VPN 和 iPhone 真机继续待验。 [交接配置](../HANDOFF.md#把必需-lfs-对象纳入交接)

## 2026-11-18 Git LFS 按需预取与续传

- 基线 `cf0f3d9` 的交接流程只支持普通 Git 文件。本轮增加指定提交及路径的 LFS 缓存预取和离线检查。环境为 Windows、Python 3.14.7、Git 2.56.0.windows.4 和 Git LFS 3.8.1。测试服务器仅监听回环地址，数据为确定性生成的测试对象。两个路径指向同一个对象，验证批量请求和对象去重。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_lfs_assets` 首轮 5 项通过，3.545 秒。补验完全下载但未发布的临时对象后，6 项通过，3.445 秒。覆盖缺件、错误 SHA-256、缓存损坏、错误 Range、服务器忽略 Range、拒绝跨站或跳转、并发去重与纯本机检查。日志为 `build-exe/verification/lfs-targeted-01.txt` 和 `lfs-targeted-02.txt`。
- `.\.venv\Scripts\python.exe tests/bench_lfs_assets.py --output build-exe/verification/lfs-64m-01` 首轮下载与续传通过。原生检出断言失败，夹具缺少本地 Git LFS 过滤器且辅助 Git 命令设置了跳过 smudge。该轮未生成 `result.json`。夹具在本地注册过滤器，随后以标准 Git LFS 命令复验。
- 上述基准命令换用 `build-exe/verification/lfs-64m-02`。64 MiB 全量预取 0.522 秒。在 16 MiB 强制断开时用时 0.329 秒，缓存未就绪。下一次请求为 `Range: bytes=16777216-`，实际传输 48 MiB，用时 0.486 秒。该次重试比从头重试少传 16 MiB。随后离线检查 0.493 秒，关闭服务后由原生 Git LFS 检出并校验文件用时 0.648 秒。都是单次本机墙钟时间，包含命令进程启动。结果见输出目录的 `result.json`。未测远程吞吐和峰值内存。
- 在包含并行桌面提交 `9253b24` 的源码上，运行 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 共 184 项，183 项通过，1 项可选 Stream SDK 跳过，153.310 秒。日志为 `build-exe/verification/lfs-regression-01.txt`。
- 家庭 Gitea、真实 VPN 传输、iPhone 真机和 LFS 缓存与工作区交接的就绪联动待验或开发。 [Git LFS 配置及复验](../HANDOFF.md#按需预取-git-lfs-对象) · [Gitea LFS 服务](https://docs.gitea.com/administration/git-lfs-setup/)

## 2026-11-18 空闲工作区自动更新

- 基线 `a096c6d` 需要手动选择合并目录和原路径切换。本轮把已签名的接收、按需合并和原路径切换接入同一个同步扫描。策略默认关闭，启用后绑定设备、线程和本机分支。环境为 Windows、Python 3.14.7、Git 2.56.0.windows.4 与 Codex CLI 0.169.1。仓库、设备和回复均为本机测试数据。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_handoff_auto tests.test_handoff` 首轮 18 项通过，55.631 秒。覆盖重复扫描和状态文件缺失后继续去重、IDE 占用、本机修改、分支不匹配、预备文件被修改、本机会话领先、切换前收到新版、签名错误和等待恢复。核对同一版本后续扫描的自动应用路径没有 Git 合并或协调查询，也核对完成新本机轮次前阻止自动回传。输出为 `build-exe/verification/handoff-auto-targeted-01.txt`。
- 执行 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --roundtrip --merge --diverged --apply --automatic --stock-files 768 --output build-exe/verification/handoff-auto-native-48m-01`。共享基线为 768 个 64 KiB 文件，共 48 MiB。已预备后触发的首轮自动扫描用时 4.963 秒，包含签名校验、分叉合并、原路径切换及进程启动，更新 4 个工作区文件。下一轮扫描为 0.217 秒，复用原路径回执。上一版的手动切换为 1.606 秒和 1.996 秒，另有独立合并步骤，阶段口径不同。结果见输出目录 `result.json`，C、D 盘符模拟往返，协调及回复服务使用回环地址。未测峰值内存及跨机传输吞吐。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 全量运行 178 项，177 项通过，1 项可选 Stream SDK 跳过，151.239 秒。日志为 `build-exe/verification/handoff-auto-regression-01.txt`。使用相同参数在 `handoff-auto-native-48m-02` 复验，首次自动应用为 5.292 秒，重复扫描为 0.265 秒，真实 CLI 续写同一线程并再次封存。
- 在 Qt offscreen 下运行 `.\.venv\Scripts\python.exe tests/bench_task_switch.py --output build-exe/verification/task-switch-after-auto`，35 条长消息、首次读取模拟等待 140 毫秒。六次切换的事件循环等待中位数为 1.62 毫秒，历史显示中位数为 13.47 毫秒。上一版同口径为 1.65 毫秒和 9.14 毫秒。结果为输出目录 `result.json`，测试与实际合并分别运行。
- 工作区已存在本地修改时继续保留预备快照。同版本持续应用失败或预备目录被修改后，60 秒内复用上次结果，保留可重试的现场。中断的事务通过 `--recover` 处理。正式启用前需要真实 Syncthing、两台物理设备及两种 IDE 联调，并统计实际额外操作数。 [空闲时自动应用](../HANDOFF.md#空闲时自动应用到原路径)

## 2026-11-18 Windows 原路径切换与中断恢复

- 基线 `1bd5157` 支持在独立目录打开合并结果。本轮为已登记的独立 Git 仓库增加原路径应用和恢复。试验使用 Windows、Python 3.14.7、Git 2.56.0.windows.4 与 Codex CLI 0.169.1，项目、配置、设备身份和模型回复均为测试环境生成。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_handoff_apply tests.test_coordination` 首轮 12 项，4 项错误，25.135 秒。临时 Codex 根目录未初始化，Windows 原生锁目录创建失败。增加首次运行目录初始化后，第二轮 13 项，2 项错误，26.421 秒。Git 拒绝把 `symref-verify HEAD` 和更新其分支放在同一 Files 引用事务。进一步尝试 `no-deref` 后，2 项复验仍因重复锁定 HEAD 失败，用时 9.421 秒。最终用索引锁防止普通检出，并以预期旧对象 ID 校验后更新指定分支。专项第 3 轮 13 项通过，24.424 秒。三轮存档为 `build-exe/verification/handoff-apply-targeted-01.txt` 至 `handoff-apply-targeted-03.txt`。
- 执行 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --roundtrip --merge --diverged --apply --output build-exe/verification/handoff-apply-native-01`，再用 `--stock-files 768 --output build-exe/verification/handoff-apply-native-48m-01` 复验。测试用真实 CLI 和回环协调服务，在 C、D 盘符内模拟往返。在小仓库与 768 个 64 KiB 共享文件的 48 MiB 仓库中，原路径切换命令分别为 1.172 秒和 1.606 秒，每次更新 4 个工作区文件。时长包含进程启动、Git 对象导入与协调请求，独立合并、封存和模型往返另行执行。旧基线没有同路径切换可比数据。未测峰值内存、并行任务切换或实际网络。两轮的 `result.json` 和再次封存的 `merged-onward.zip` 保存在各自输出目录，临时源目录在退出时清理。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 首轮 171 项，170 项通过，1 项可选 Stream SDK 跳过，111.298 秒。已加入已完成切换但协调服务回复丢失后的恢复测试。输出为 `build-exe/verification/handoff-apply-regression-01.txt`。
- 快进到并行的桌面提交 `ff3f703` 后，同命令第一轮集成回归 171 项，170 项通过，1 项跳过，107.024 秒。把索引复核移到取锁之后，并核对刚移入备份的文件后，集成复验 172 项，171 项通过，1 项跳过，113.769 秒。最后用 `-m unittest -v tests.test_handoff tests.test_handoff_apply.ApplyTests.test_new_worktree_with_old_local_history_cannot_be_published` 补验旧历史配新工作区的发布拦截，14 项通过，33.645 秒。日志分别为 `handoff-apply-integrated-01.txt`、`handoff-apply-integrated-02.txt` 和 `handoff-apply-history-final.txt`。同一 48 MiB 参数在 `handoff-apply-native-48m-02` 再验，原路径切换用时 1.996 秒，成功续接并再次封存。
- 使用 Windows 实际文件句柄模拟占用，验证已移动文件回滚。模拟在新文件、提交与索引已安装后中断，恢复到原索引字节和原目录状态。也验证已登记 IDE、协调后台、原生线程写入、未跟踪路径碰撞及本机已跟踪修改等切换拦截条件。自动更新策略、断电恢复和真实双机 Syncthing 与 IDE 联调仍待完成。[原路径切换与恢复](../HANDOFF.md#应用到本机原路径) · [Git 校验与更新引用](https://git-scm.com/docs/git-update-ref)

## 2026-11-18 不同提交工作区合并与往返续接

- 基线 `f39661a` 支持同 HEAD 的独立预备合并。本轮验证本机领先、接收端领先和历史分叉。存在祖先关系时使用较新的提交。分叉合并成功时，只在预备目录生成双父提交，源端暂存和未暂存修改分别叠加。环境为 Windows、Python 3.14.7、Git 2.56.0.windows.4、Codex CLI 0.169.1。输入为临时 Git 仓库和虚构会话。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_handoff_merge` 首轮 12 项通过，30.926 秒。覆盖双向快进、双父提交、已提交内容与暂存层的冲突报告、无共同祖先、浅历史、私有路径拦截、大小写冲突、源索引字节和续接回执。输出为 `build-exe/verification/handoff-diverged-targeted-01.txt`。对象格式不兼容随后并入全量回归。
- `.\.venv\Scripts\python.exe tests/bench_handoff_merge.py --output build-exe/verification/handoff-same-48m-03` 和同脚本的 `--diverged --output build-exe/verification/handoff-diverged-48m-01` 顺序运行。每轮为 768 个 64 KiB 的共享基线文件，共 48 MiB，外加少数文本修改。分叉样本另在两端各提交一个不同文件。同 HEAD 成功合并为 3.142 秒，未暂存层冲突为 1.267 秒。上一轮同 HEAD 为 3.334 秒和 1.288 秒。分叉样本为 2.870 秒和 1.334 秒。结果分别保存于两个目录的 `result.json`。成功各调用一次检出和对象打包，冲突均无检出或打包。打包限制两个线程。封存和还原分别为 14.141 秒和 13.979 秒，单独计时。以上均为单次本机墙钟结果，未测峰值内存和网络吞吐。
- `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --roundtrip --merge --diverged --output build-exe/verification/handoff-diverged-native-01` 通过。真实 CLI 在 C、D 盘间模拟往返，核对了分叉合并的两个父提交、双方文件内容、暂存层、原生历史锁及同线程续写。新一轮与合并提交再次封存到 `merged-onward.zip`。具体线程和轮次见 `result.json`。传输用本机目录复制，回复用回环服务，单轮耗时未独立记录。
- `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 共 164 项，163 项通过，1 项可选 Stream SDK 跳过，用时 77.332 秒，日志为 `build-exe/verification/handoff-diverged-regression.txt`。
- 使用 Qt offscreen 运行 `.\.venv\Scripts\python.exe tests/bench_task_switch.py --output build-exe/verification/task-switch-after-diverged`。输入为 35 条长消息、每次首次读取人为延迟 140 毫秒。6 次切换的事件循环等待中位数为 1.65 毫秒，历史显示中位数为 9.14 毫秒。两次冷读显示约 180 毫秒，四次缓存命中为 7.54 至 10.14 毫秒。上次同口径的两个中位数为 1.51 和 8.39 毫秒。实验未同时运行合并，结果见 `result.json`。
- 原路径安全切换、两台物理设备、Syncthing 和两种 IDE 的联调仍待完成。实际使用的额外操作数待测。 [工作区合并与交接说明](../HANDOFF.md)

## 2026-11-18 同提交工作区合并与性能验证

- 基线 `26c0d7e` 可恢复两端各自的目录。本轮加入按需工作区合并，保持两端的 Git 暂存和工作层，以版本 1 的 JSON 报告冲突。运行环境为 Windows、Python 3.14.7、Git 2.56.0.windows.4 和 Codex CLI 0.169.1。测试输入由临时 Git 仓库和虚构会话生成。
- 初次专项运行 4 项，7.520 秒，出现 1 项误报冲突。Windows 的两份工作区采用 LF 和 CRLF，初版按原始字节生成 Git 文本对象。调整后按输入仓库的 Git 换行规则读取跟踪文件，在隔离配置中进行 Git 操作。专项 4 项复测全部通过，8.519 秒。再补合并后才生效的内容过滤器和中途保存检查，6 项通过，15.103 秒。额外核对自定义合并驱动隔离、文本和二进制冲突、原索引字节及显式纳入的新文件。
- 执行 `.\.venv\Scripts\python.exe tests/bench_handoff_merge.py --output build-exe/verification/handoff-merge-48m-02`。数据为 768 个 64 KiB 基线文件，共 48 MiB，加少量暂存和未暂存文本变更。`handoff-merge-48m-01` 的合并成功和冲突用时分别为 3.659、2.054 秒，末次为 3.334、1.288 秒。末次成功只调用 1 次 `merge-tree`、1 次检出和 1 次对象打包。冲突路径执行 2 次 `merge-tree`，检出和打包均为 0 次。同批输入的既有封存与恢复为 13.994 秒，属于独立步骤。结果保存在各轮的 `result.json`，时长为单次本机墙钟实测，未测峰值内存及网络吞吐。
- 执行 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --roundtrip --merge --output build-exe/verification/handoff-merge-native-01`，真实 Codex CLI 往返后在合并目录续写同一线程。已核对 A 独有的暂存文件、B 文件的暂存版和工作版，以及原生写入锁和旧历史备份。文件传输采用本机目录复制，模型回复来自回环测试服务。此前无合并参数的往返复测也通过，用时 8.28 秒。
- 首轮全量回归运行 156 项，56.442 秒，1 项跳过。快进到并行桌面更新 `16f4206` 后的集成复测，156 项中有 1 项 Windows 临时库清理失败，耗时 58.554 秒，日志在 `handoff-merge-integrated-regression.txt`。原因是对话读线程偶尔晚于窗口关闭。改为退出时收尾当前读任务后，运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v` 共 157 项，156 项通过，1 项可选 Stream SDK 跳过，53.059 秒，结果在 `build-exe/verification/handoff-merge-final-regression.txt`。
- 用与前次相同的 35 条长消息和 140 毫秒读取等待，运行 `python tests/bench_task_switch.py --output build-exe/verification/task-switch-after-merge`。界面事件循环等待中位数为 1.51 毫秒，显示历史中位数为 8.39 毫秒。上一版同口径记录分别为 1.58 毫秒和 8.34 毫秒。本轮 `tests/smoke_desktop.py --output build-exe/verification/handoff-merge-desktop-smoke` 通过，使用 Qt offscreen 模式。
- 当前覆盖同 HEAD 的独立合并结果。不同提交的分支合并、原目录内应用和两台设备的网络验收仍待完成。 [工作区合并与交接说明](../HANDOFF.md) · [Git 三方树合并](https://git-scm.com/docs/git-merge-tree)

## 2026-11-18 跨盘往返续接与原生写入锁

- 基线 `b3b8dbf` 已支持自动发布和接收目录，但返回源端时，早期同线程历史会被判为冲突。本轮验证完整字节前缀的原生 JSONL 快进与重复发布。环境为 Windows、Python 3.14.7、Git 2.56.0.windows.4 和 Codex CLI 0.169.1。输入为虚构的独立仓库、互不共享状态的两个 Codex 目录和本地固定回复服务。
- 用 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --roundtrip --output build-exe/verification/handoff-roundtrip-01` 执行首轮，7.54 秒通过，第二轮 `handoff-roundtrip-02` 为 7.42 秒。第一次快进被真实 app-server 的活动写入锁拒绝。进程退出后，旧历史被独立备份，再原子替换并续写同一线程。模拟发布失败和本机日志独立变动的单测核对了备份字节，也覆盖压缩同名记录、未验证 CLI 版本和未登记的预备目录。
- 用相同命令更换输出为 `build-exe/verification/handoff-roundtrip-paired-01` 验证最终配置。真实 CLI 按 C→D→C 完成三个轮次，最终回复由回环服务在看到 B 的回复后返回。B 的暂存和未暂存两版分别回到 A 的新预备目录，A 的原工作文件保留。B 第二次发布复用第一次接收时的配置，从已登记仓库解析 `origin/main`，自动跟随本次已恢复目录。结果在 `result.json`，原历史及续写后历史各保留一份测试副本。本轮与回归并行，未另计端到端墙钟时间。
- 专项 `python -m unittest -v tests.test_handoff tests.test_coordination` 首轮运行 17 项全部通过，22.461 秒。运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -v` 的两次全量回归分别为 149 项、39.534 秒，以及最后的 150 项、45.620 秒。末次 149 项通过、1 项可选 Stream SDK 跳过，输出为 `build-exe/verification/handoff-roundtrip-paired-regression.txt`。
- 当前快进支持 Windows 上已验证的未压缩原生格式。物理双机、实际 Syncthing、交互 IDE 以及现用工作树合并仍待验，网络时延和用户额外操作数未测量。 [代码与会话交接](../HANDOFF.md) · [Codex 0.169.1 写入锁源码](https://github.com/openai/codex/blob/rust-v0.169.1/codex-rs/rollout/src/writer_lock.rs)

## 2026-11-18 桌面任务切换延迟

- 复现于 Windows、Python 3.14.7、PySide6 6.12.0，基线为 `1a3617d`。用三份虚构任务和每份 35 条长消息，给读取增加 140 毫秒固定等待。执行 `tests/bench_task_switch.py --output build-exe/verification/task-switch-final`，使用 `QT_QPA_PLATFORM=offscreen`。基线用相同脚本和独立归档源码，输出于 `task-switch-baseline-final`。
- 初次基线记录为 337.41 毫秒事件循环等待中位数。初版异步测试连续使用 `QTest.qWait`，Python 工作线程无法及时获得执行时间，三次达到 8 秒超时。改用真实 Qt 事件循环轮询完成状态，异步初次复测的等待中位数为 1.52 毫秒。随后桌面 smoke 的同步断言未等到异步历史读取，改为按条件等待，并覆盖迟到历史不会覆盖当前任务。
- 最终两组串行测量中，六次任务选中后的事件循环等待中位数从 338.19 毫秒降为 1.58 毫秒，显示对话的中位数从 344.38 毫秒降为 8.34 毫秒。新版本首次选中未缓存任务约 178 毫秒显示历史，返回已缓存任务约 7–9 毫秒。测量为本机合成负载，不含远端接入或真实用户数据库。改动将记录读取和富文本排版放入后台，保留最近五个视图的渲染文档与滚动位置。
- `tests/smoke_desktop.py --output build-exe/verification/task-switch-desktop-smoke-02` 通过。全套 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 运行 147 项，146 项通过、1 项可选 Stream SDK 跳过，用时 37.993 秒，见 `build-exe/verification/task-switch-regression.txt`。
- 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R8`，PyInstaller 完成构建约 40.89 秒。包位于 `release/Lanxi-Glass-R8.exe`，61,417,833 字节，同目录 R7 为 54,104,555 字节。归档核对包含新版桌面模块、交接后台和 Qt Windows 插件。切换测试执行的是源码窗口，新 EXE 的实机启动和操作耗时未测。

## 2026-11-19 大模型 API 分组与统一弹窗

- 以 `0e018a1` 的 R14 顶边菜单为基线。菜单按「澜汐、任务、显示、设置、窗口」组织，新增「设置 → 大模型 API」。设置、审批、额度及提示弹窗与主界面共用海蓝色、字重和控件样式。配置后对 Codex、Claude Code、拾得片羽和待理诸事分别自动分组，随记与待办支持拖到组名下，手动选择优先。自动分组以同一内容的缓存结果复用为先，每批最多 8 项，接口超时 12 秒，失败后间隔 60 秒再试。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。用虚构任务、随记和待办请求回环的 `/chat/completions`。运行 `python -m unittest -q tests.test_model_groups tests.test_desktop_projects tests.test_codex_desktop tests.test_claude tests.test_web`，42 项中 41 项通过，耗时 7.648 秒。隔离进程读取不到 DPAPI 用户配置，跳过 1 项。改用当前登录用户重跑 `python -m unittest -v tests.test_model_groups`，2 项通过，耗时 1.243 秒。覆盖手动分组优先、重启读回、API 密钥落盘保护，以及换地址时清除旧密钥。配置、临时文件及分组数据库通过 `git check-ignore` 检查。实际外部供应商接入及网络耗时未测。
- 在 `build-exe/verification/lanxi-model-groups/worktree` 设置 `QT_QPA_PLATFORM=windows` 后执行 `python tests/smoke_desktop.py --output ../windows-verified`，完整原生窗口验收通过。用模拟拖放事件覆盖随记待办分组逻辑和待办勾选状态，核对案头自动分组、菜单和设置保存。历史轮次有一次斜杠菜单时序失败，另一次复用了已清理的测试数据库，修正夹具后独立复跑。截图位于 `windows-verified`。
- 使用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R15-ModelGroups`，PyInstaller 构建约 49.72 秒。交付 `release/Lanxi-Glass-R15-ModelGroups.exe`，61,530,386 字节，复制前后 SHA-256 一致。执行 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R15-ModelGroups.exe`，于 1.53 秒在 `lanxi-folder-package-pdbr9caf` 验证原生窗口和 SQLite 启动。

## 2026-11-19 顶边淡入菜单栏

- 基线为 R13。窗口内顶边保留 4 像素触发区，菜单栏使用原有顶部留白。指针进入后淡入 190 毫秒，离开等待 100 毫秒后淡出 210 毫秒。反向移动从当前透明度继续，系统关闭动画时立即切换。复用「运行与额度」、待审批和收起到托盘入口。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。用虚构会话在 `build-exe/verification/lanxi-upper-menu/r13-release` 设置 `QT_QPA_PLATFORM=windows` 后执行 `python tests/smoke_desktop.py --output ../windows-release`。首轮直接向中央控件注入移动，未命中实际顶边层，改从原生窗口派发。第二轮的顶边测试通过，但打开详情抢占了后续焦点测试所需的活动窗口，将顶边检查放到最后。再次运行时既有斜杠菜单一度未弹出，完整复跑通过。已核对顶边命中、透明度反向、留在栏内继续显示、关闭系统动画后的切换，以及滑块几何位置保持一致。
- 设置 `QT_QPA_PLATFORM=offscreen` 运行 `python -m unittest -q tests.test_codex_desktop tests.test_claude tests.test_desktop_projects`，32 项通过，5.382 秒。HTML 解析检查核对了开发方案的 17 个 ID 与 18 个页内链接。
- 使用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R14`，约 60.51 秒。现有 R14 文件名已被并行构建使用，交付另存为 `release/Lanxi-Glass-R14-TopMenu.exe`，61,446,468 字节，复制前后 SHA-256 一致。使用 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R14-TopMenu.exe`，在 `lanxi-folder-package-82rp78sd` 于 1.32 秒验证原生窗口和 SQLite。菜单交互使用原生源码窗口验收。

## 2026-11-19 Codex 斜杠模型与权限

- 基线 R12 的输入框仅发送普通消息。增设 `/` 弹出 `/model` 和 `/permissions`。从当前 Codex 连接读取模型、权限档位与现有值，把用户选择通过控制队列交给 `thread/settings/update`。权限选择沿用当前审批策略，设置面向所选会话的后续轮次。已有 IDE 写入权与托管限制仍由现有控制链检查。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0、Codex CLI 0.169.1。用 `codex app-server generate-json-schema --out ../schema --experimental` 核对方法。第一次 CLI 探测把新建线程当成既有会话接入，未取得控制权，复测改用应用自己的测试任务创建入口。在隔离目录 `settings-native-retry-home` 的真实 app-server 返回 11 个模型和 3 个权限档位，切换模型及只读权限后收到两条 `thread/settings/updated`，两次都命中同一线程。间隔含进程启动为 0.707 秒，未发送模型轮次，结果见 `result.json`。
- 在 `build-exe/verification/lanxi-codex-slash/worktree` 设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output ../windows-keyboard`。第一次 UI 测试发现回车未打开二级菜单，显式处理选择键后通过。最终同时验证上下键、前缀筛选、回车、鼠标选择及不可选的权限档位，截图在 `windows-keyboard`。在 R12 交付基线的 `r12-release` 设置 `QT_QPA_PLATFORM=offscreen`，运行 `python -m unittest -q tests.test_task_control tests.test_coordination tests.test_codex_desktop tests.test_claude tests.test_web`，68 项全部通过，9.329 秒。
- 用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R13`，保留 `package-first`。最终构建约 55.97 秒，交付包 `release/Lanxi-Glass-R13.exe` 为 61,442,675 字节，复制前后 SHA-256 一致。运行 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R13.exe`，于 1.32 秒在隔离目录 `lanxi-folder-package-2l77mnbc` 核对原生窗口和 SQLite 启动。未使用线上模型发送任务。
- 合并并行的 Git LFS 交接提交 `5bb6edc` 后，用相同命令执行源码专项，69 项全部通过，8.997 秒。

## 2026-11-18 对话气泡圆角与底色

- 以 R11 的对话区为基线，保留正文排版、左右对齐和内边距。普通消息使用 11 像素圆角和更透的浅蓝底。助理及用户气泡的背景 alpha 分别为 132 和 172，色值为 `#EBF9FF` 和 `#DAF0FD`。第一版实机图中，助理气泡背景比原位置向右偏 10 像素。最终根据文本单元格的布局和内边距定位。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。用虚构的双客户端短消息、多段长文本、代码和审批请求，在 `build-exe/verification/lanxi-soft-bubbles/r11-release` 设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output ../windows-release`。验证两种气泡的圆角处透出毛玻璃，圆角内绘制背景，1020×640 长消息换行和滚动正常。最终图位于 `windows-release`，首轮图留在 `windows-first`。
- 在 `cf0f3d9` 的集成工作树设置 `QT_QPA_PLATFORM=offscreen`，执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，178 项中 177 项通过、1 项跳过，用时 193.026 秒，结果在 `regression.txt`。桌面、会话和项目分组专项共 33 项全部通过，5.413 秒。
- 最终交付包沿用 R11 的构建基线，仅增加本轮气泡源码。使用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R12`，约 59.83 秒。包为 61,434,009 字节，复制前后 SHA-256 一致。运行 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R12.exe`，在 `lanxi-folder-package-tx00spc_` 中于 1.84 秒验证独立原生窗口和 SQLite。未测真实模型会话。

## 2026-11-18 列表选中外框与展开箭头

- 以 R10 的任务树和吉光片羽界面为基线。去掉任务、项目组和片羽条目的焦点外框，以及片羽分组入口的悬停和按下反馈。任务树的展开箭头独立于行底色绘制。第一次复测发现选中时底色仍延伸到箭头区，第二次复测发现原生图元在独立绘制时没有显示。最终将行背景裁到条目区域，直接绘制等尺寸三角形。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。使用虚构项目、任务及临时笔记库，在 `build-exe/verification/lanxi-flat-lists/r10-release` 设置 `QT_QPA_PLATFORM=windows`，运行 `python tests/smoke_desktop.py --output ../windows-r11`，约 9.43 秒通过。项目组的箭头区域在静止、悬停和选中时逐像素一致。展开与折叠可点，方向随状态变化。验证任务行、待办条目的焦点变化不改变选中外观。截图位于 `windows-r11`。
- 在 `a096c6d` 的集成工作树以 `QT_QPA_PLATFORM=offscreen` 运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，173 项中 172 项通过、1 项跳过，用时 130.730 秒，日志位于 `regression.txt`。最终交付包在 R10 基线 `ff3f703` 只覆盖本次列表源码，以 PyInstaller 6.22.3 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R11`，约 37.35 秒。包为 61,433,557 字节，复制前后 SHA-256 一致，归档确认含新版桌面模块。R10 正在运行，验收使用原生源码窗口，R11 EXE 独立启动未测。

## 2026-11-18 拾得片羽与待理诸事

- 以 `1bd5157` 的 R9 界面和确认过的两张概念图为基线。左栏保留两个固定入口，切换时同步更新列表和右栏组名。沿用 SQLite 表结构。新记录只写正文，编辑旧记录时在同一编辑区读回原题名和正文。待办的完成操作只更新状态，排序和时间戳保持稳定。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。在 `build-exe/verification/lanxi-notebook-groups/worktree` 设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output ../windows-first`，约 9.16 秒通过。用临时 SQLite 记录覆盖旧随记、旧归档、旧待办的读取和恢复，核对双栏位置、各组新建、单句保存、切换工作区保存、点击勾选和空格取消。1180×760 实机图为 `windows-first/notebook-desktop.png` 和 `windows-first/todo-desktop.png`。未测真实用户笔记库。
- 设置 `QT_QPA_PLATFORM=offscreen` 后运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，164 项中 163 项通过、1 项跳过，用时 72.583 秒。使用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R10`，构建约 45.66 秒。交付文件 `release/Lanxi-Glass-R10.exe` 为 61,430,155 字节，复制前后 SHA-256 一致，包内核对了笔记、诗词、毛玻璃及 Windows Qt 插件。R9 正在本机运行，R10 的独立 EXE 窗口未测，实机界面验收使用源码窗口。

## 2026-11-18 吉光片羽双栏毛玻璃与整点换诗

- 以 `b3b8dbf` 的 Codex 页为界面基线，用虚构任务、随记和待办对照。吉光片羽复用两块毛玻璃、列表高亮、标题字重和按钮，自动保存延迟为 450 毫秒。初版截图的筛选选中态区分较弱，复验版降低未选按钮底色并加粗当前分类。两块面板与 Codex 页的位置和尺寸逐项相同。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。在 `build-exe/verification/lanxi-notebook-matching/worktree` 设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output ../windows-hourly-poetry`，约 9.07 秒通过。保留早期截图 `windows`、筛选复验 `windows-final` 和固定诗句版 `windows-release`。用模拟时间走完 25 联并验证整点换句、出处更新、同小时稳定，以及 1020×640 下不裁字。随记和待办以临时 SQLite 验证新增、自动保存、归档、恢复、完成与切回 Codex 时保留草稿。未等待自然整点或触发真实休眠。
- 选诗依据 [chinese-poetry《唐诗三百首》](https://github.com/chinese-poetry/chinese-poetry/blob/master/全唐诗/唐诗三百首.json)，简体篇名有通用别名，另核对 [常建原文](https://zh.wikisource.org/wiki/破山寺後禪院) 与 [钱起原文](https://ctext.org/text.pl?if=gb&node=153790)。源数据留在验收目录，运行时仅使用随包的 25 联。
- 运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，打包基线集成回归 147 项中 146 项通过、1 项跳过，用时 33.867 秒。执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R9`，PyInstaller 6.22.3 构建最终包约 36.69 秒。单独存放前序构建 `package-first` 和 `package-notebook`。交付文件 `release/Lanxi-Glass-R9.exe` 为 61,422,902 字节，包含诗词、随记和 Qt Windows 插件。使用 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R9.exe` 在 `lanxi-folder-package-kvwtc0zh` 中于 1.43 秒验证原生窗口和 SQLite。
- 源码合并并行的后台提交 `26c0d7e` 后重跑 150 项。首次用时 38.580 秒，Claude 暂停测试清理临时数据库时遇到 Windows 文件占用，输出在 `integrated-regression.txt`。同命令复验用时 38.147 秒，149 项通过、1 项跳过，输出在 `integrated-regression-rerun.txt`。

## 2026-11-18 自动封存、签名发布和预备目录恢复

- 第一阶段的手动封存、复制和恢复作为基线。新增后台定期扫描、按内容去重、签名发布、接收滞后重试和独立预备目录。输入沿用临时 Git 仓库、同一文件的暂存与未暂存两版、未推送提交、选定新增文件及隔离的 Codex 会话目录。Windows、Python 3.14.7、Git 2.56.0.windows.4、Codex CLI 0.169.1，回环模型回复为固定测试文本。
- `.\.venv\Scripts\python.exe -m unittest -v tests.test_handoff tests.test_web` 运行 18 项全部通过，25.456 秒。覆盖重复扫描、同一轮次后的已保存变更生成新版本、清单先到、包截断、清单篡改、合法签名但包身份不同、回退清单以及本机存在分叉会话。验证源仓库、目标 Git 基线、现用工作区和原会话均保留。
- 执行 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --sync --output build-exe/verification/handoff-sync-native-01`，5.24 秒通过。第二次输出保存于 `build-exe/verification/handoff-sync-native-02`，再次通过。测试用 C 盘和 D 盘的独立交换目录模拟文件复制，两次均用真实 Codex CLI 续写原线程，核对目标目录、暂存内容与新回复。结果、接收侧清单、交接包与继续封存包均保留在相应输出目录。
- 本轮验证覆盖自动封存和预备目录恢复。保留已用工作区合并与本机旧会话快进，真实 Syncthing、两台物理机和两种 IDE 交接待验。未测量网络速度和大包吞吐。配置及接口见 [代码与会话交接](../HANDOFF.md)。

## 2026-11-18 封面诗句与三栏工作区

- 以 R6 为基线，对照开发方案头图。诗句从 34 pt 调至 42 pt 粗宋体，保留单行，下方补出处与方案封面的两行副标题。顶部滑块新增「吉光片羽」作为第三个停靠点。随记和待办进入主窗口，切回客户端前提交待保存编辑。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。在独立交付源码 `build-exe/verification/lanxi-verse-notebook-release/source/mobile-monitor` 设置 `QT_QPA_PLATFORM=windows`，运行 `python tests/smoke_desktop.py --output ../../windows`。核对 1020×640 和 1180×760 截图、出处和完整副标题、滑块停靠、第三栏及返回后的草稿保存。独立源码回归 `python -m unittest discover -s tests -t . -p 'test_*.py' -q` 运行 121 项，120 项通过、1 项跳过，12.029 秒。
- 通过 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R7`，交付文件为 `release/Lanxi-Glass-R7.exe`，54,104,555 字节；R6 为 54,103,534 字节。核对包内滑块、随记、桌面和 Windows 平台模块。运行 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R7.exe`，在 `lanxi-folder-package-9jmzedhj` 中于 1.11 秒验证原生窗口和 SQLite 控制库。

## 2026-11-18 VS Code 与 PyCharm 活动上报

- 目标为将第二里程碑的活动接口接到编辑器，并区分正常退出、断网与意外退出。基线 `b00130d` 可用 CLI 报到和查询协调状态，未提供编辑器插件。环境为 Windows、Python 3.14.7、Node.js 26.13.1、VS Code 1.146.0 与 PyCharm 2026.3（PY-263.7307.206，JBR javac 25.0.4.1）。输入为临时项目、一台虚构设备及回环协调服务。
- 首轮 Python 专项共 17 项，1 项在临时目录清理时失败。测试自身的 SQLite 查询未关闭连接，Windows 返回文件占用。确认连接关闭后，扩展到 18 项全部通过，2.338 秒。专项覆盖正常关闭后只释放当前窗口、异常输入 EOF 保留占用、心跳不推进项目控制版本、开窗回执丢失且断网时将关闭事件落盘补发。回环实测用两个独立的 Python 上报进程分别模拟 VS Code 和 PyCharm。
- 用 `node --test tests/test_vscode_activity.cjs` 验证 VS Code 适配器在启动、多根工作区变更和停用时生成相应的 reporter 生命周期。通过 1 项，Node 报告总耗时约 57 ms，输入为模拟 VS Code API。
- 执行 `.\.venv\Scripts\python.exe scripts/build-ide-adapters.py --pycharm 'C:/Program Files/JetBrains/PyCharm 2026.3' --output build-exe/verification/ide-adapters-02`。两次构建分别用时约 1.27 和 1.45 秒，保留 `ide-adapters-01` 和 `ide-adapters-02`。最后一次 VS Code VSIX 为 2,246 字节，通过 VS Code CLI 安装到该目录下隔离的用户和扩展配置。PyCharm ZIP 为 2,629 字节，使用本机平台库编译并校验 JAR、类文件与清单。编译器提示 `projectOpened` 已弃用，适配器当前限定构建 263。安装输出还有既有 Crashpad 权限提示，CLI 退出码为 0。
- 全量回归使用 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v`，143 项中 142 项通过，1 项可选 Stream SDK 跳过，31.014 秒。输出位于 `build-exe/verification/ide-adapters-regression.txt`。
- 现场待验收包括真实 IDE 窗口生命周期、普通工作区首次打开间隙、真实网络分区和三台物理机。没有测量网络时延或编辑器性能影响。更新后的七阶段口径见 [跨设备方案交付进度](../DELIVERY_STATUS.md)。

## 2026-11-18 项目会话分组与吉光片羽

- 在 R4 界面基线上增设案头项目树，同一 Git 根目录下的会话入同组，其他项目按实际工作目录归组。组内以现有会话标题及本地 Codex 用户消息命名，手动标题优先。同名项目显示父路径。只计算本机历史命名覆盖，4 段记录为 `codexDesk` 的 Codex 会话均能取得已有标题或首条话题。
- 「吉光片羽」从案头底部或托盘打开，含随记与待办。本机 `lanxi-notebook.sqlite3` 保存两种记录。输入后 450 毫秒自动保存，关闭窗口时提交尚未保存的编辑。Qt 测试验证创建、归档、恢复、待办完成及再次打开数据库读回。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。独立交付源码目录为 `build-exe/verification/lanxi-project-notebook-release/delivery-source/mobile-monitor`，以 R5 的 `9f4ff0d` 为快照叠加本次桌面文件。设置 `QT_QPA_PLATFORM=windows` 后运行 `python tests/smoke_desktop.py --output ../../windows-delivery`。早期项目树截图在缩进处出现多余高亮，最终限制子行绘制区域后复查消失。R4 与 R6 的顶部滑块及诗句截图逐像素一致，右栏差异仅在悬停工具栏的渲染像素范围内。
- 执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，独立交付源运行 121 项，120 项通过、1 项跳过，11.857 秒。与并行后端工作区的联合回归运行 142 项，141 项通过、1 项跳过，29.622 秒。
- 在独立源码目录用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R6`。交付 `release/Lanxi-Glass-R6.exe` 为 54,103,534 字节，R5 为 54,088,903 字节。归档核对包含项目树、笔记与原生桌面模块。使用 `python build-exe/verify-lanxi-launch.py Lanxi-Glass-R6.exe` 验证隔离副本，在 `build-exe/verification/lanxi-folder-package-l0oc1bfc` 于 1.32 秒检测到窗口和控制库。启动验证使用独立目录，实际模型调用与跨设备联调未执行。

## 2026-11-18 跨设备项目控制与桌面共用后台

- 目的为验证人工活动能撤销下一次后台写入许可，且原生写入锁在交还后可重新取得。上一阶段 `b707345` 完成跨盘会话续接，回归 128 项、通过 127 项、跳过 1 项。此次实现中心 SQLite 事务、设备身份验证、活动序号和项目版本，接入 Codex 原生发送边界。试验在 Windows、Python 3.14.7、Codex CLI 0.169.1 下进行。
- 初次用两份 Codex app-server 和临时会话探测 `thread/unsubscribe`，因缺少测试模型提供方配置而接入失败，C 盘临时目录清理还遇到 Windows 文件占用。把原测试配置复制到独立目录后，`ownership-native-03` 证实旧连接收到 `unsubscribed`，第二份原生连接仍被写入锁拒绝。复验 `ownership-native-04` 在 0.2 秒、再等 1 秒和再等 3 秒后重试，仍均拒绝。实现随后改为确认空闲并退出原生进程，再释放中心记录。
- 首轮协调与额度专项共 32 项，其中旧回归用 `__new__` 构造 RPC 导致缺少默认协调字段，1 项报错；补默认字段后 37 项均通过，2.805 秒。PowerShell 裸命令续接和显式参数透传用例独立通过，2.369 秒。专项还覆盖 3 设备并发登记、重复事件、多窗口、中心重开后保留失联占用、旧审批拒绝、发送超时保留写入记录以及明确拒发后的调度重试。
- 使用 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --output build-exe/verification/handoff-v2-regression` 创建独立的跨盘验收数据，约 4.08 秒通过。再运行 `.\.venv\Scripts\python.exe tests/smoke_coordination.py --fixture build-exe/verification/handoff-v2-regression --output build-exe/verification/coordination-native-04`。单机回环使用 3 个虚构设备身份、3 次真实 app-server 连接和 1 条完成的原生会话；验证后台按回执加载目标目录、人工报到后拒发旧后台的新轮次、旧连接退出后人工接手、最后一个人工会话关闭后后台重接。四次通过的协调验证分别记录 1.438、0.873、0.927 和 0.715 秒，未测线上时延。最后一次结果位于 `build-exe/verification/coordination-native-04/result.json`，后台重接后的项目版本为 7。
- 用 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v` 跑本机工作树两次，均运行 139 项，138 项通过，1 项可选 Stream SDK 跳过，分别用时 26.493 和 25.841 秒。最终输出位于 `build-exe/verification/coordination-regression-02.txt`。其中用真实回环 HTTP 验证桌面内嵌 Web 登录、读取同进程 Worker 状态及收尾退出。
- 已完成协议、终端适配器和工作进程接入。VS Code、PyCharm 自动上报，独立网络故障，Claude Code，三台物理设备以及本次源码的 EXE 打包仍待验收。配置、恢复口径与复现命令见 [跨设备项目控制](../PROJECT_CONTROL.md)。

## 2026-11-18 以 R4 为基线重建 R5

- 在 `build-exe/verification/lanxi-r4-baseline/r5-source/mobile-monitor` 解包 R4 源码 `287a3f6`。只覆盖 `9f4ff0d` 的 `desktop_qt.py` 和配套桌面验收脚本。第一轮发现 Git 归档受 CRLF 设置影响，第二轮关闭归档的 CRLF 转换，逐字节核对桌面入口、毛玻璃、配色、后台模块、图标和构建脚本均来自 R4。重建使用已提交的快照。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。设置 `QT_QPA_PLATFORM=windows` 后，从该独立源码目录运行 `python tests/smoke_desktop.py --output ../../r5-windows`。同尺寸 1180×760 的 R4 与 R5 实机截图相差 3018 个像素，全部位于左侧任务标题与状态或右侧当前标题区域。其余区域相差 0 像素。运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，118 项通过，1 项跳过，10.976 秒。
- 用 PyInstaller 6.22.3 从独立源码目录运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R5`。交付文件 `release/Lanxi-Glass-R5.exe` 为 54,088,903 字节，同构建文件哈希一致，包内已核对 Qt Windows 插件、原生桌面模块及三张壁纸。隔离启动返回 0 时，发现 R4 仍占用单实例锁，未进行第二实例独立启动验收。

## 2026-11-18 跨设备后端第一阶段：Git 与原生 CLI 跨盘续接

- 目的为验证方案的第一个技术依赖：目标工作区能同时保留未推送提交、暂存版、未暂存版、选定新增文件和指定的已完成轮次，并在新路径续写原线程。基线 `9f4ff0d` 的桌面回归为 119 项、通过 118 项、跳过 1 项，尚无交接入口。实施期间 17:00 例行备份产生 `ef577f8`，该提交保存的是开发中的版本。
- Windows 单机，Python 3.14.7、Git 2.56.0.windows.4、Codex CLI 0.169.1。源仓库与源端 Codex 数据目录位于 C 盘临时目录，目标仓库与目标端 Codex 数据目录位于 D 盘。两个 CLI 进程共用一个回环 Responses 测试服务；测试服务在第二次请求带有第一次回复时才返回 `reply-on-B`。输入是一份新建的 Git 仓库、两次固定提示和两份固定回复。
- 复现命令为 `.\.venv\Scripts\python.exe tests/smoke_handoff.py --output build-exe/verification/handoff-native-04`，复测时更换目录名。首次运行输出到 `handoff-native-01`，4.38 秒，退出码 1。核对原生记录发现第二轮已有正确回复，断言读取了旧版 `agent_message` 字段。改为按实际完成事件的 `turn_id` 和 `last_agent_message` 核对后，`handoff-native-02` 通过，5.64 秒；加入回执接口后 `handoff-native-03` 再次通过，3.47 秒。
- 最后一次 `handoff-native-04` 用时 4.11 秒，退出码 0。核对目标 HEAD、Git 状态、二进制文件的暂存内容与工作内容、选定新增文件、新一轮实际 cwd，以及本机恢复后再次封存。线程 `01a1edea-af64-7061-9f60-9ecc7078c31b` 始终一致；原轮次、新轮次、Git 版本和包 SHA-256 均在 `build-exe/verification/handoff-native-04/result.json`。初次封存包 13,731 字节，续接后封存包 14,493 字节。耗时为各次本机命令墙钟时间，部分与回归并行。
- 回归命令为 `.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -p 'test_*.py' -v`。首批 5 个专项用例通过，7.594 秒；扩展实现期间专项复测 14.197 秒，初期全套 124 项、跳过 1 项、19.444 秒。增加 PowerShell 裸命令/显式参数透传、子目录映射、历史敏感文件与 Web 鉴权等覆盖后，全套两次均为 128 项、127 项通过、1 项跳过，分别为 25.189 秒和 25.152 秒。跳过的是可选 Stream SDK 回调用例。末次日志在 `build-exe/verification/handoff-regression-04.txt`，仍有既有的 SQLite 与 TestClient 警告。
- 此次验证了实际 CLI 的历史续接、跨盘目录映射、数据校验和冲突保全。封存、传包与首次恢复为显式操作。两台物理机、VS Code/PyCharm 交互式终端、线上模型、后台自动传输与跨机写入权尚未验收，日常额外操作数和网络耗时未测量。使用与接口见 [代码与会话交接](../HANDOFF.md)。

## 2026-11-18 项目文件夹名称与连接状态

- 以标题对齐版为基线。左侧列表、右侧标题和审批窗口共用项目名，优先保留用户手动设置的显示名，默认取工作目录的末级文件夹。用 Windows 目录末尾带反斜杠的记录验证提取，用无目录的历史会话验证标题回退。用完整路径可区分同名任务。
- 列表显示「待执舟」「执舟中」「已执舟」与「已暂泊 · 待续航」，对应现有任务操作。Windows 11 x64、Python 3.14.7、PySide6 6.12.0。设置 `QT_QPA_PLATFORM=windows` 后执行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-folder-names`，通过并核对实机截图。执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，119 项中 118 项通过、1 项跳过，10.926 秒。
- 用 PyInstaller 6.22.3 执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1`，更新 `release/Lanxi.exe`，54,088,554 字节，前一版 R4 为 54,086,051 字节。第一次隔离验证因 Windows 环境变量转为普通字典后键名大小写不匹配，在启动前退出。改用 `SYSTEMROOT` 后，`python build-exe/verify-lanxi-launch.py` 在 `build-exe/verification/lanxi-folder-package-2hfndfvz` 内于 1.01 秒发现同路径的原生窗口和 SQLite 控制库。隔离验证未连接真实 CLI。
- GitHub 仓库改为 `gjh1999/mobile-monitor`。REST API 核对改名前后仓库 ID 均为 1461100100，新地址读取到同一初始分支，已同步本地 `origin` 与全局 Git 备份登记。[GitHub 仓库](https://github.com/gjh1999/mobile-monitor)

## 2026-11-18 案头与对谈标题对齐

- 以扁平玻璃版为基线。左侧「案头诸事」由 12 pt 粗体改为与右侧当前任务标题共用 13 pt 粗体。两侧使用相同的上边距、标题行高和垂直对齐。「一处开始」由 12 pt 常规改为 12 pt 半粗。
- 第一次 Windows 验收发现，左侧在右边输入框继承样式前读取了行高。改为两栏完成装配后取值。离屏复测 1020×640、1180×760、1460×1076，标题行同为 y=256、行高均为 34 个逻辑像素。设置 `QT_QPA_PLATFORM=windows` 后运行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-title-alignment-final`，验证相同字体、相同位置并检查实机截图。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，119 项中 118 项通过、1 项跳过，12.055 秒。用 PyInstaller 6.22.3 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R4`，并列包为 `release/Lanxi-Glass-R4.exe`，54,086,051 字节，前一版为 54,086,517 字节。已核对归档中的桌面模块、Windows 插件和三张壁纸，验收期间上一版仍在运行。

## 2026-11-18 扁平玻璃滑块与字号调整

- 以顶部玻璃复核版为基线。「一处开始」小字从 10 pt 提至 12 pt，计数标签从 9 pt 粗体改为 8.5 pt 常规，数字从 17 pt 粗体改为 15 pt 常规。逐帧绘制圆角按压与键盘焦点，移除原生按钮样式画出的方框。胶囊材质取消阴影和高光渐变，滑块以 146/255 的浅色覆盖同一张柔化壁纸。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。设置 `QT_QPA_PLATFORM=windows`，执行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-flat-glass`。检查计数字号与字重、滑块底座和移动块的壁纸透射像素，并复测拖动、反转和减弱动态效果。人工核对静态工作台与拖动截图，动画实际帧率未测。
- 同环境执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，119 项中 118 项通过、1 项跳过，10.895 秒。用 PyInstaller 6.22.3 运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R3`，生成 `release/Lanxi-Glass-R3.exe`，54,086,517 字节，前一版为 54,087,019 字节。已检查包内 Qt 平台插件、桌面模块与壁纸。上一版运行期间完成源码窗口和归档验证。

## 2026-11-18 澜汐顶部玻璃与动效复核

- 对照通过的 `docs/design-preview/workspace-hover.png` 与首版实机截图，校正顶部胶囊位置及分段宽度、右侧计数卡的宽度和对齐。计数卡设为三个 60 像素等宽栏，分隔线为 1×35 像素。顶部胶囊与滑块直接采样柔化壁纸。诗句换为 34 pt 粗宋体并加 0.85 像素描边，客户端标签用 11 pt 粗体。
- 按 [Apple Design skill](https://github.com/emilkowalski/skills/blob/main/skills/apple-design/SKILL.md) 的原则实现按下反馈、跟随指针、速度交接和中途改向。滑块使用临界阻尼，角频率 25 s⁻¹，16 毫秒的计时器步进，减弱动态效果分支立即停靠。未测量实际刷新率或输入到屏幕的延迟。
- Windows 11 x64、Python 3.14.7、PySide6 6.12.0。设置 `QT_QPA_PLATFORM=windows`，运行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-glass-header-final`。最终实机截图复核三栏、描边和玻璃层，合成 Qt 输入验证按下、64 像素拖动、反向时保留位置与速度、松开停靠和关闭动画。第一轮截图的宋体细笔画偏轻，加粗轮廓后复测。
- 在同一虚拟环境运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，119 项中 118 项通过、1 项跳过，耗时 11.586 秒。
- 使用 PyInstaller 6.22.3，通过 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass-R2` 生成 `release/Lanxi-Glass-R2.exe`，54,087,019 字节，首版玻璃包为 54,082,682 字节。验收包含源码原生窗口及 EXE 归档。旧版实例仍占用单实例锁，未做新 EXE 的独立启动。

## 2026-11-18 澜汐毛玻璃工作台

- 以确认的桌面预览和旧版原生窗口为基线。保留 Qt Widgets，任务列表与对谈面板从同一张壁纸采样并使用相同半透明底色。新增居中客户端滑块、定时壁纸轮换、悬停操作、独立审批窗、就地改名与 VS Code「启卷」。点击任务后由本地控制器先接入目标，再断开同端其他任务。已有运行轮次不因切换而中断，草稿按客户端和任务保留。
- 环境为 Windows 11 x64、Python 3.14.7、PySide6 6.12.0 和 PyInstaller 6.22.3。壁纸输入为现有 Mavericks 及 512 Pixels 档案的 Big Sur 白昼和 Catalina 夜景，后两张缩放至 2560 像素宽。默认 30 分钟轮换，启用系统动画时 600 毫秒渐变。壁纸实际长期轮换与跨显示器性能未测。
- 将 `QT_QPA_PLATFORM` 设为 `windows` 后运行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-glass-acceptance`。首次悬停验证的鼠标模拟未可靠触发进入事件，改用显式 Enter/Leave 事件验证淡入淡出，保留真实 Qt 鼠标点击和键盘测试。中间版本的半透明消息底色因 Qt HTML 导入重复填充文字背景而出现条纹，清理后复测截图已确认。最终用虚构双端会话验证了两块玻璃采样、列表点击与草稿隔离、双击改名、VS Code 参数、轮换计时器、审批来源、托盘弹窗及 1020×640 布局。
- 在同一虚拟环境运行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`。相比 118 项基线新增 1 项任务切换回归，覆盖接入失败、快速选择、旧消息取消与接入状态保存。119 项中通过 118 项、跳过 1 项，耗时 11.109 秒。
- 用 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1 -AppName Lanxi-Glass` 生成并列的 `release/Lanxi-Glass.exe`，体积 54,082,682 字节，旧包为 51,793,094 字节。打包日志位于同一验收目录。EXE 归档检查确认 Windows Qt 插件、新桌面模块、图标及三张壁纸均已内置。旧版仍在运行，因此以源码原生窗口和打包资源做验收。真实账号审批和从新版点击启动真实 VS Code 窗口未测。

## 2026-11-18 澜汐 Windows 原生交付

- 目的为让 Windows 用户双击进入独立桌面窗口。沿用旧版 Qt Widgets 的本机任务控制、托盘和 SQLite 状态，在原生窗口加入澜汐品牌、诗句与 Mavericks 海浪壁纸。
- 环境为 Windows 11 x64、Python 3.14.7、PySide6 6.12.0、PyInstaller 6.22.3。运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build-exe.ps1` 生成 `release/Lanxi.exe`，使用单文件 GUI 模式，体积 51,793,094 字节。归档内确认含 Qt Widgets、Windows 平台插件和壁纸。旧版桌面 EXE 体积未测。
- 首次探索构建了浏览器工作台启动包。需求明确为原生桌面后，转为构建 Qt 入口。首次隔离启动已产生 SQLite 状态，隐藏窗口用 PowerShell 的主窗口标题检查未能识别。改用按进程 ID 枚举 Windows 顶层窗口，隔离包在 1.22 秒内检测到“澜汐”窗口及任务控制数据库。现场目录为 `build-exe/verification/packaged-native-143342493/`。
- Qt offscreen smoke 通过，但离屏截图缺少本机中文字形。首次改用 Windows 平台插件执行时，测试仍查找旧品牌。更新断言并加上壁纸加载检查后，用 `QT_QPA_PLATFORM=windows` 运行 `python tests/smoke_desktop.py --output build-exe/verification/lanxi-native-win-final` 通过，已查看原生截图。输入为虚构 Codex 与 Claude 会话，覆盖任务控制、草稿、中文、按钮动画、紧凑窗口和托盘恢复。
- 在项目虚拟环境执行 `python -m unittest discover -s tests -t . -p 'test_*.py' -q`，118 项运行，117 项通过，1 项跳过，耗时 11.243 秒。仍有既有 SQLite 与 TestClient 警告。
- 打包和隔离启动已验证。真实模型收发、实际工具审批、Windows 其他机器兼容性和复杂转场性能需另测。当前交付使用现有 Qt Widgets 的按钮动画，尚未引入 Qt Quick / QML。

## 2026-11-12：钉钉路线冻结与 P0 核验

已完成的具体动作：

- 写入 `钉钉监控与长程任务守护_实施计划书.md`，以钉钉为默认路线，取消继续扩张 Bark/飞书范围。
- 核对现有 `monitor.py`、`automation.py`、页面、CLI 0.167.0 及已生成 app-server schema。
- 核对钉钉官方 Stream Python SDK，确认存在机器人消息、事件和卡片回调接入；当前用户租户配置尚未验证。
- 执行本地既有测试，3 项通过：审批过期及幂等、未知/notLoaded 不唤醒、默认只演练。

这 3 项测试不是完整计划验收。尚未证明钉钉签名、手机收到通知、Stream 发送者鉴权、真实审批回传、持久调度和重启恢复。

## 已交给实现阶段的问题

1. 额度接口拒绝应独立退避，不得为重试额度而销毁承载任务与待审批的连接。
2. RPC 输入写入需串行化，通知、请求、响应分别处理，不能吞掉所有异常后丢失状态。
3. `serverRequest/resolved`、断连和过期需要撤销本地审批，并验证迟到点击不能响应到错误连接。
4. `notLoaded` 不代表桌面客户端空闲；托管会话注册、独占所有权和持久去重完成前不启用真实续跑。
5. 先做钉钉通知的本地 mock 与凭据配置说明；没有用户机器人配置时禁止实际外发。

## 阶段状态

| 阶段 | 状态 |
|---|---|
| P0 现状审计 | 已完成第一轮，问题已登记 |
| P1 钉钉通知 | 实现中，未真实接通 |
| P2 稳定连接 | 修复中，未完成全生命周期验证 |
| P3 任务守护 | 有保守原型，持久调度和真实托管续跑待验收 |
| P4 审批闭环 | 有原型，手机实际审批待验收 |
| P5 双向 Stream | 待应用凭据和身份配置；可先做离线测试 |
| P6 常驻交付 | 未完成 |

用户明确要求不控制桌面或浏览器。所有后续实施只通过文件、命令行和官方协议进行。

## P1 第一批实际交付

新增 `channels_dingtalk.py`：签名文本 Webhook、HTTPS 官方目标限制、业务返回码检查、进程内去重、明确限流时退避、网络结果不确定时不自动重发。所有异常结果为脱敏状态，不输出 Webhook 或签名密钥。

新增 `test_dingtalk_channel.py`，离线 transport 验证签名、消息格式、业务拒绝、去重、错误目标和网络不确定性。与原有测试合计 7 项通过。没有真实钉钉外发，也尚未完成跨重启事件队列，因此 P1 只算部分完成。

新增 `notify_cli.py` 与 `钉钉本机配置步骤.md`。默认只检查环境配置，不发网络请求；真实测试需显式 send，且由用户本机设置收件群的凭据。主循环集成、Stream、持久守护与真实审批仍未完成。

独立实现任务最近处于 waitingOnApproval，不能把其活跃状态当作代码进展。根任务已继续实现不需要外部安装的离线适配与测试，并通知实现任务文件所有权，避免冲突。

## 2026-11-12：根任务接入主服务（替代前面的旧原型说明）

已完成：

- 主循环移除 Bark，接入签名钉钉 Webhook；通知发送前预留 SQLite 记录，跨重启阻止重复外发。
- RPC 输入串行化、响应分发、断连唤醒等待请求、serverRequest/resolved 处理；额度查询失败不销毁连接。
- 调度持久保存间隔、失败退避和发送前预留；增加活动 goal 和待审批检查。未加载、未知和非独占线程继续阻止唤醒。
- 手机网页显示连接、额度、任务、调度和具体审批信息；接口令牌认证、默认只监听回环地址。
- 可选 Stream 指令路由：企业/员工/私聊会话三重白名单，重复消息抑制，查看操作后一次性确认码，120 秒到期。SDK 适配器依据官方代码编写，尚未安装和联网验收。
- 实际执行 21 项离线测试全部通过；包含本地 HTTP 请求测试，不调用钉钉、不启动模型任务。

尚未完成：

1. 用户钉钉应用/机器人的账号配置及 iPhone 收发实测；凭据必须在本机私密设置，不放聊天。
2. 托管线程注册、所有权恢复及真实长程任务续跑。当前 owned_threads 为空，真实唤醒禁用；这是未完成项，不能称为守护已投产。
3. 文件变更完整预览，及完整 app-server 连接端到端审批。
4. Stream SDK 真实连接、断线恢复和进程退出验收。
5. Windows 开机常驻/重启恢复安装；当前仅手动进程方式。

本轮没有控制桌面、浏览器或替用户点击任何权限提示。测试不代表真实外部服务已接通。

## 本轮最终现场状态（以后面的记录为准）

- 26 项测试通过，新增托管任务创建/恢复、不确定创建禁止重试、目标修改阻止恢复及真实钉钉 SDK 离线回调测试。
- 官方 dingtalk-stream 0.24.3 已安装在本项目独立 .venv，依赖版本保存到 requirements-lock.txt，没有修改医学训练虚拟环境。
- ManagedTasks 支持仅创建本服务新线程、SQLite 注册记录、重启恢复和固定目标校验；没有启用任何托管任务，尚未真实创建模型任务做端到端验收。不能保证另一个客户端不会手动操作同一线程，因此必须避免跨客户端并发控制。
- Windows 计划任务 CodexMobileMonitor 已实际注册，返回 Ready；普通用户权限，登录触发、失败重启及忽略重复实例。尚未注销/重登或模拟故障验证。
- 主服务实际启动并连接 app-server；认证状态接口返回 connection=connected、managedTasks=[]、schedule=[]。额度端点不可用，quotaStatus=unknown: endpoint unavailable；不是额度为零。
- Webhook、签名密钥、Stream client ID/secret 均未配置，没有真实钉钉外发或手机审批验收。
- 文件审批缺少完整 diff 时仍拒绝批准；桌面原客户端权限弹窗不在本服务可控范围。
- 本轮未接管已有研究线程，未控制桌面/浏览器。

可独立完成的代码、离线验证和登录常驻注册已经推进。余下联网闭环需要用户在本机配置钉钉账号凭据及身份白名单；配置方法见 钉钉本机配置步骤.md，秘密不发送到聊天。

## 2026-11-13：小时守护与Windows通知的现场复核

- 新增完整回复SQLite日志、Windows Toast/托盘回退、跨重启通知去重、限定任务/目录/完整命令匹配的自动审批规则；29项离线测试通过。
- 回复通知正文已经使用实际回复摘要；待审批通知显示操作摘要。Windows通知脚本已实际调用，返回`balloon_api_called`，代表托盘通知接口调用成功，不代表已目视确认显示。
- 使用本机令牌在内存中查询`/api/status`，现有服务返回`connection=connected`，但`managedTasks=[]`、`schedule=[]`、`windowsNotification`未提供。不能将旧服务在线等同于新增功能已部署。
- 独立开发任务`01a1d43b-7a50-7612-8bd4-b6dd122b9c70`仍为`waitingOnApproval`。其待审批工具调用是沙箱外启动并检查计划任务：`Start-ScheduledTask -TaskName CodexMobileMonitor`及对应Get-ScheduledTask/Get-ScheduledTaskInfo核验；没有证据表明这条调用已获批准或执行。
- 每小时唤醒现有医学研究线程、该线程所有权/状态绑定、回复日志到通知的真实端到端链路尚未验收。尚不能宣称守护已全天生效。当前并未为绕过额度或线程所有权创建替代研究任务。

### 同日审批通过后的部署核查

- 用户在根任务批准启动和进程身份核查后，确认四个重复监听8765的进程均属于本项目，已清理并重启已注册计划任务。
- 增加Windows独占端口绑定及重复实例回归测试；30项测试全部通过。
- 实际认证状态接口返回`serviceVersion=0.3.1-windows`、`connection=connected`，端口只有一个监听PID。新版后台服务已实际部署，不能再把旧服务在线当成新版本运行。
- `probe_research_binding.py`仅执行只读检查，未启动模型。当前连接不拥有目标研究线程，`thread/read`和`thread/goal/get`均返回RPCError。因此既有研究任务的自动唤醒尚未接通；不是审批一通过就已经完成端到端链路。
