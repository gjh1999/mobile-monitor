# 跨设备项目控制

## 个人版项目占用

R24 使用 GitHub 仓库 `owner/repo` 识别同一个项目。通过澜汐接入该项目的 Codex 任务时，服务端确认设备控制权。同一项目已有另一设备占用时，澜汐保留只读查看，拦截接入、消息、暂停续航与审批等写操作。不同项目可以由不同电脑同时操作。

R36 要求共享 Codex 连接在同意审批前复核项目控制权。来源设备无法确认仍有控制权时，桌面显示「控制权已变更」。从同工作区另一台电脑操作时，同一结果通过跨机回执返回。显式登记项目的协调服务需提供写入版本，个人工作区继续使用现有任务控制接口。 [工单与复测状态](DELIVERY_STATUS.md#r35-review)

要切换电脑，先在原设备结束进行中的对话及审批，然后点「交还任务」。澜汐等本机 Codex 连接退出、Git 同步完成且服务器已收到最新完整会话，再交还控制权。另一端要等同一份会话到达后才能接入。一个项目有多个正在持有控制权的任务时，逐一交还。断网和异常退出时保留原占用，在原设备恢复网络后重新打开并交还。

R42 在关闭主窗口缩到托盘时自动发起本机澜汐任务交还，同时暂停自动接入和推进。结束进行中的轮次和审批后，后台核对同步结果并逐项交还。断网或会话缺少可校验的同步记录时，托盘显示待处理状态，等条件恢复后重试。重新打开主窗口后，可选择要接管的会话重新「执舟」。VS Code 与 PyCharm 的项目占用随各自窗口关闭后释放。

VS Code 与 PyCharm 安装便携包里的澜汐活动插件后，在打开 GitHub 项目时上报占用，在窗口关闭时归还 IDE 占用。两个插件使用同一个本机配对身份和相同仓库标识，遇到别的设备占用时会提示。R40 由独立后台刷新占用并重发已保存的关闭事件，案头按当前会话、同项目其他会话或项目占用显示原因。属于任务写权的占用，在提示的设备中选择会话并点击「交还任务」。第三方 Codex 聊天扩展使用自己的发送入口。

在各机解压便携包内的 `IDE上报工具.zip` 到固定位置，运行其中的 `启用IDE上报.cmd`。从 VS Code 扩展菜单安装 `lanxi-activity.vsix`，或从 PyCharm 插件设置选择从磁盘安装 `lanxi-activity-pycharm.zip`。重启对应 IDE。PyCharm 包匹配 263 系列。插件需要澜汐已完成 R23 或之后的云配对。R24 至 R28 的这些文件位于便携包根目录。 [便携版](PORTABLE_CHECK.md)

## 已登记项目的控制与调度

选择一台常在线的 Windows 电脑运行协调服务。各开发机用同一项目标识登记本机路径，分别持有设备凭据。人打开已登记的项目或在登记目录运行终端适配器时，后台停止发出该项目的新推进、消息和批准。原 Codex 后台在确认其连接上的任务空闲、审批已结清后退出原生连接。最后一个人工会话关闭后，后台按原有任务设置重新接入。

## 运行中心与登记设备

从澜汐源码目录执行。服务使用已有的 `requirements-web.txt`，将独立的 SQLite 数据库和设备登记文件放在本机私有目录。每台设备配置不同的随机令牌。下面的令牌字段仅为占位内容。

```json
{
  "office": {"token": "<替换为公司机的随机令牌>", "projects": ["vision"]},
  "laptop": {"token": "<替换为笔记本的随机令牌>", "projects": ["vision"]},
  "home": {"token": "<替换为家庭机的随机令牌>", "projects": ["vision"]}
}
```

```powershell
.\.venv\Scripts\python.exe coordination_server.py --devices '.local\devices.json' --database '.local\coordination.sqlite3'
```

中心默认监听 `127.0.0.1:8770`。跨设备时通过可信 HTTPS 入口转发到这个服务。各开发机在自己的 `local-config.json` 中加上以下配置，并把本机令牌设为进程环境变量 `LANXI_DEVICE_TOKEN`。重启桌面或 Web 后台以重新读取项目映射。启用前先退出该项目原有的澜汐后台和 Codex 写入连接。

```json
{
  "coordination": {
    "url": "https://lanxi.example",
    "device": "office",
    "projects": {
      "vision": {
        "path": "D:\\Work\\Vision",
        "threads": ["替换为已跨机交接的 Codex 线程 UUID"]
      }
    }
  }
}
```

两端的目录可以不同，项目标识保持一致。原生记录已使用本机路径的线程按目录识别。对照 [代码与会话交接](HANDOFF.md) 恢复的线程应登记 ID，使后台能够用恢复回执中的目标路径重新接入。后台接入前需要本机已有对应原生记录。

## IDE 自动上报

从澜汐源码根目录构建本机安装包，使用新的输出目录。PyCharm 的构建参数指向实际安装路径，本次支持构建 263。

```powershell
.\.venv\Scripts\python.exe scripts/build-ide-adapters.py `
  --pycharm 'C:\Program Files\JetBrains\PyCharm 2026.3' `
  --output build-exe/verification/lanxi-ide-adapters
```

VS Code 从 `lanxi-activity.vsix` 安装扩展，PyCharm 在插件设置中从磁盘安装 `lanxi-activity-pycharm.zip`。在启动两个 IDE 的环境中设置 `LANXI_INSTALL_DIR` 为包含源码、`.venv` 和 `local-config.json` 的目录，并继承同一台设备的 `LANXI_DEVICE_TOKEN`。全量重启 IDE 使新环境变量生效。

VS Code 在启动完成及工作区根目录变化时更新当前窗口各本地工作区的活动。PyCharm 从项目打开和关闭事件上报。上报器只处理有登记映射的项目。正常关闭会保存关闭事件，断网后由共用同一配置目录的本机后台或下次打开的上报器重发。进程意外终止会保留协调端的人工占用，等操作者核对。 [VS Code 扩展生命周期](https://code.visualstudio.com/api/references/activation-events) · [JetBrains 项目生命周期](https://plugins.jetbrains.com/docs/intellij/project.html)

## 人工活动与失败恢复

- 加载 `scripts/lanxi-terminal.ps1` 后，在登记目录执行裸命令 `codex`。适配器先上报人工占用，等后台退出原生连接后再启动 CLI。同一项目同时允许一个登记的 CLI 会话和多个 IDE 窗口。CLI 正常退出时将关闭事件保存到配置目录下的 `.local/ide-close.sqlite3` 并立即上报。断网时保留事件，由后台或下一次登记的终端启动时重发。
- IDE 上报端用 `POST /api/v1/projects/{project}/activity` 发送 `session`、`source`、`sequence`、`event`。每个窗口与工作区组合使用唯一会话 ID，`source` 为 `vscode` 或 `pycharm`，序号递增，依次使用 `open`、`heartbeat` 和 `close`。多根工作区对各登记项目分别上报。请求头为 `X-Lanxi-Device` 与 `Authorization: Bearer <设备令牌>`。通过命令行上报可用 `coordination_client.py activity vision vscode <会话ID> <序号> <事件>`。
- 失联 30 秒的窗口显示为 `stale`，仍保留人工占用。确认窗口及其终端实际退出后，从同一设备以更高序号上报 `close`。后台发送结果未知时保留写入记录。协调机可用 `--database <路径> --inspect <项目>` 查询。确认旧后台及相关执行已经退出后，再使用 `--release-confirmed <项目> <版本> <设备> <线程>` 解除对应记录。
- 设备上报在中心事务中撤销后续自动写入许可。已取得发送许可的请求由旧连接继续核对，终端会等待后台退出。若同一后台连接还持有其他活动任务，也会一并等待。挂起的审批可拒绝，或由操作者暂停旧轮次。
- `handoff_apply.py` 用同一接口登记 `switch` 占用。项目已有 IDE、终端或后台占用时，中心拒绝切换。切换占用期间，中心拒绝新占用和后台写入，失联后仍保留记录。执行端通过本机切换日志恢复后再结束占用。[原路径切换](HANDOFF.md#应用到本机原路径)

本版协调范围覆盖澜汐的 Codex 后台、使用适配器的裸 `codex` 和两个 IDE 上报器。真实 IDE 宿主、未经过适配器的原生调用、Claude Code 与三台物理机联调仍待接入或验证。

## 桌面与 Web 共用后台

在安装 Web 依赖的源码环境中设置 `MONITOR_EMBED_WEB=1` 及既有的 `MONITOR_PASSWORD`，再启动 `desktop_app.py`。托盘、桌面和 Web 共用一个工作进程及其状态。Web 默认监听 `127.0.0.1:8765`，端口可用 `MONITOR_PORT` 修改。桌面退出时一并停止 Web 服务。前端登录后可调用 `GET /api/v1/project-control` 查询各登记项目的版本、后台持有者和人工占用。

## 硬件计算队列

协调中心可用 `--compute` 读取本机管理的项目计算配置。运行配置以名称登记，包含 CPU 核数、内存和单卡显存需求。内存和显存单位为 MiB。设备还需在原有设备文件登记该项目。下面的示例为 `vision` 登记一个 CPU 配置和一个 GPU 配置。

```json
{
  "vision": {
    "workers": {"office": "local", "home": "home"},
    "profiles": {
      "render": {"cpu": 2, "memoryMiB": 2048, "vramMiB": 0},
      "train": {"cpu": 4, "memoryMiB": 8192, "vramMiB": 6144}
    }
  }
}
```

```powershell
.\.venv\Scripts\python.exe coordination_server.py --devices '.local\devices.json' `
  --database '.local\coordination.sqlite3' --compute '.local\compute.json'
```

提交端沿用设备认证。作业请求提供唯一 UUID `id`、本设备仍在线的前台活动 `session`、登记的 `profile` 和完整 Git 对象 ID `commit`。同一个 UUID 和请求内容重试时返回同一作业。查询可见范围为提交设备和作业当前执行设备。请求只接受这些结构化字段。

```powershell
.\.venv\Scripts\python.exe compute_client.py --project vision submit --body '.local\submit.json'
.\.venv\Scripts\python.exe compute_client.py --project vision status '<作业UUID>'
.\.venv\Scripts\python.exe compute_client.py --project vision cancel '<作业UUID>'
```

计算端以持久化实例 UUID `instance` 上报 `profiles`、已准备好的 `commits`、可用 `cpu`、`memoryMiB` 和每张卡的 `gpuVramMiB` 数组。中心只选最近 30 秒有心跳、运行配置与提交匹配且资源充足的设备。CPU 作业优先发起设备，GPU 作业优先家庭设备。每台设备跨项目只占用一个计算执行槽。资料齐备的较新作业可以先于尚无合适设备的旧作业领取。

以下接口位于 `/api/v1/projects/{project}/compute/`，可用 `compute_client.py` 的对应子命令和 JSON 文件调用。

| 方法与路径 | 用途 |
| --- | --- |
| `POST heartbeat` | 上报执行实例、运行配置、已就绪提交及可用资源。 |
| `POST claim` | 按 `instance` 领取作业，或取得该实例已有的领取结果。`{"job": null}` 表示暂无可领取作业。 |
| `POST jobs/{id}/cancel` | 仅允许提交设备请求取消。可用相同作业 ID 安全重试。 |
| `POST jobs/{id}/events` | 发送 `instance`、领取凭据 `ticket` 和 `event`。`start` 确认开始，`renew` 延长正在执行的租期并返回取消请求，`finish` 和 `canceled` 提交 `exitCode` 与 `resultSha256`。 |

排队或已领取但尚未确认开始的作业立即进入 `canceled`，旧凭据同时失效。已经确认开始的作业先记录 `cancelRequestedAt`，状态保留为 `running` 或 `uncertain`，执行槽继续保留。执行端收到请求后在本机落盘，确认进程树退出才提交 `canceled` 回执，退出码为 130。如果命令在观察到取消前已退出，中心保留实际完成回执。运行中作业支持重连后继续处理取消。使用此功能前升级协调中心和 Windows 执行端。

领取后一直未确认开始的作业，到期可重新分配，旧凭据失效。已开始的作业到期进入 `uncertain`，保留执行槽，原执行实例仍可提交最终结果。重复上传相同完成凭据和结果时返回原完成记录。若原实例无法恢复，先停止该设备的领取进程，再确认对应的执行进程及其子进程已经退出。中心管理员用以下命令释放执行槽，作业进入 `abandoned`。

```powershell
.\.venv\Scripts\python.exe coordination_server.py --database '.local\coordination.sqlite3' `
  --confirm-compute-stopped vision '<作业UUID>' 1 home
```

### 启动 Windows 执行端

在执行设备上准备项目仓库及可信提交，配置设备令牌 `LANXI_DEVICE_TOKEN`。在该机 `local-config.json` 中登记 `coordination` 和 `computeWorker`。将示例 `commits` 的值替换为本机已审核且存在的完整 Git 提交。资源值由本机管理员按可用容量填写，GPU 数组每项对应一张卡。

```json
{
  "coordination": {
    "url": "https://coordination.example.com",
    "device": "home",
    "projects": {"vision": {"path": "D:\\Projects\\vision"}}
  },
  "computeWorker": {
    "projects": {
      "vision": {
        "commits": ["0123456789abcdef0123456789abcdef01234567"],
        "cpu": 8,
        "memoryMiB": 16384,
        "gpuVramMiB": [8192],
        "profiles": {
          "train": {
            "command": ["D:\\Envs\\vision\\Scripts\\python.exe", "train.py"],
            "timeoutSeconds": 7200,
            "outputs": ["reports/metrics.json"],
            "environment": {"PYTHONUTF8": "1"}
          }
        }
      }
    }
  }
}
```

```powershell
.\.venv\Scripts\python.exe compute_worker.py --config local-config.json --state-dir 'D:\ComputeState\lanxi'
```

状态目录需位于项目仓库外，由运行执行端的账户私有保存。重启时沿用同一个目录。执行端空闲时每 5 秒检查可用作业，运行时每 5 秒续租，当前进程可在监督进程退出时立即开始补报。`--once` 执行一次检查并返回状态，已启动的监督进程继续运行。

执行端在独立目录内检出固定提交，只运行本地配置的绝对可执行文件和参数。提交端只能选择登记名称及提交标识。命令默认只继承 Windows 系统路径和专用临时目录，项目所需的额外环境由本机配置声明。请使用权限受限的 Windows 账户，且只审批可信项目代码及配置。Windows Job Object 管理进程树、超时及远程取消，命令仍具有运行账户的文件和网络权限。当前检出仅支持普通 Git 文件，含子模块、链接或 LFS 过滤器的输入会在准备阶段报错。 [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)

执行端用现有续租发现取消，通常每 5 秒检查一次。请求到达已运行的监督进程后，监督进程按 250 毫秒间隔检查本机信号。正在准备 Git 工作目录时，检出返回后再检查取消，已收到取消时跳过启动命令。取消后保留本机日志和清单。

每次尝试在 `attempts/<项目>/<作业UUID>/<尝试编号>/` 下保留合并日志 `process.log`、结果清单 `result.json` 及中心回执 `ack.json`。日志最多 2 MiB，超出部分计数。成功产物按 `outputs` 配置读取，合计最多 128 MiB，记录文件大小和 SHA-256。执行端在报告前复验日志和产物。断线期间结果留在本机，重连后可补报。已记录进入执行且缺少有效结果的尝试会保留待核对状态。确认中心已收到且本机产物已另行保存后，按需归档已完成的尝试目录。

### 回传成功作业的文件

回传默认关闭。协调中心用 `--artifact-dir` 指定专用存储目录，执行设备在 `local-config.json` 的 `computeWorker` 中加上 `"uploadArtifacts": true`。两端均需运行支持回传的版本。中心存储目录由服务账户管理，按需要归档或清理过期数据。

```powershell
.\.venv\Scripts\python.exe coordination_server.py --devices '.local\devices.json' `
  --database '.local\coordination.sqlite3' --compute '.local\compute.json' --artifact-dir 'D:\LanxiArtifacts'
.\.venv\Scripts\python.exe compute_client.py --project vision download '<作业UUID>' --output 'D:\Received\vision-job'
```

开启后，执行端先上传成功作业中登记的产物，再发送完成回执。每块最多 4 MiB，每个作业最多 128 个文件。上传和下载均按已落盘字节位置继续。文件完整并通过 SHA-256 校验后，且清单散列与成功回执一致时，原提交设备才能下载。失败和取消的作业沿用本机日志和退出码回执。日志正文保留在执行设备。

下载目录需使用绝对路径并独立于登记项目。CLI 保留产物的相对路径，逐个校验后发布文件，已有同散列文件直接复用。已有不同内容的文件会报错。中断后使用同一个 `--output` 重试即可续传，自动写回项目工作树仍待开发。回传期间执行槽继续保留，执行端重连后补齐文件再发送回执。

检查点、实际 GPU 探测及三台物理设备联调待完成。

## 复验

```powershell
.\.venv\Scripts\python.exe -m unittest -v tests.test_ide_activity tests.test_coordination tests.test_web
.\.venv\Scripts\python.exe -m unittest -v tests.test_compute_queue tests.test_compute_worker
.\.venv\Scripts\python.exe -m tests.bench_compute_queue --output build-exe/verification/compute-queue-repeat
node --test tests/test_vscode_activity.cjs
.\.venv\Scripts\python.exe tests/smoke_handoff.py --output build-exe/verification/coordination-input
.\.venv\Scripts\python.exe tests/smoke_coordination.py --fixture build-exe/verification/coordination-input --output build-exe/verification/coordination-local
```

使用新的输出目录。真实连接测试启动独立的回环服务和三份虚构设备身份，从第一阶段验收样本复制一条完成的线程。测试确认旧连接释放前原生写入锁仍有效，人工报到后旧后台无法发送新轮次，交还后人工连接可接手，随后后台重新接入。
