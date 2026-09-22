# 本机部署适配

`compose.yaml` 启动独立 `komodo` 组，Core 仅监听 `127.0.0.1:28793`。Mongo、密钥、备份和工作目录使用独立命名卷，不挂原业务数据目录。Core/Periphery 之间沿用上游自动生成的通信密钥，不需要操作者手工管理令牌。

本机 Core 的初始账号是 `admin`，密码及 JWT 密钥由安装过程写在仓库外发范围之外的 `.local/komodo.env`，不提交。Komodo 本身保留登录机制；不修改上游认证代码。

启动：

```powershell
docker compose --env-file .local/komodo.env -f adaptations/local-platform/compose.yaml up -d --no-build --pull never
```

先按上游方式构建并链接 `client/core/ts`，再在 `ui` 执行翻译检查和 `yarn build`；`Dockerfile.ui` 在固定上游 Core 镜像上覆盖静态前端。必须记录源码输入摘要，不能把包含本地改动的镜像说成原上游提交的精确构建。

## 接管已有项目

```powershell
python adaptations/local-platform/export_stacks.py --root E:\jvjv\local-platform --output E:\jvjv\local-platform\.local\komodo-import --group mqtt-sandbox
```

导出器仅运行 `docker compose config`，按组叠加 current 的镜像 pin，保留项目名，将已有卷与网络显式标记为 external，并输出 Komodo Stack TOML。不会调用部署、删除或导入 API。生成配置可能包含环境值，只放本机工作目录，不提交到 fork。

宿主 bind mount 会拒绝导出，需要先明确 Windows→Periphery 路径映射。不要为了导入而移除挂载。当前 MCP 组存在此类挂载，不能直接导入原生 Stack；已通过下述 Windows 发布引擎保留原路径执行。

正式接管前还需要：跨组依赖顺序、统一发布锁、Windows 构建替代或桥接、独立业务验收、版本来源映射、不同版本恢复、旧历史保留。新旧管理台不得同时管理同一项目的发布。此阶段没有启用自动更新或同步删除。

## 多项目 Windows 执行桥接

Komodo 管理发布入口；`bridge.py` 调用本机 `E:/jvjv/local-platform/services/project-console/pipeline.py` 和同目录依赖、`scripts/release_ops.py`。旧发布台的 HTTP 服务不是桥接依赖，但这一套构建/发布引擎、配方、工作树和历史目录仍需保留。并非将 Windows Java/Maven 强行搬进 Core 容器。

Core 只额外挂专用 `.local/mqtt-queue`，不是整个 E 盘。将 `KOMODO_LOCAL_QUEUE=E:/jvjv/komodo/.local/mqtt-queue` 写入未跟踪的 `.local/komodo.env`，预先建立 requests/responses/claims 子目录；任务入口按 `projects.json` **只限定项目与操作**（批准清单），可构建分支、构建命令与验收配方只在平台权威文件 `E:/jvjv/local-platform/config/project-console.json` 里定义一次，由 pipeline 拒绝未登记分支——本仓库不再保存第二份分支白名单，`projects.json` 出现 `refs`/`default_ref`/`build_commands` 等键会直接报错。仅允许登记过的构建、已验证版本发布和回退，没有通用 shell/命令/URL 参数，也不监听额外网络端口。具有 Core 管理权限的用户属于本机可信操作者。

启动入口：

```powershell
pwsh -NoProfile -File E:\jvjv\komodo\adaptations\local-platform\Komodo.ps1 -Action Open
```

`Start` 启动容器及 Windows 后台工作进程，`Status` 查询工作进程，`StopWorker` 请求空闲后停止；不强杀正在运行的发布。Docker 重启会恢复容器，但 Windows 工作进程需要上述启动入口，不能把容器 online 误认成 Windows 构建工具已启动。

`python adaptations/local-platform/komodo_api.py` 按 `projects.json` 幂等安装各项目允许的 Action/Procedure，升级时保留操作者已选的参数，账号由本机 env 读取，默认禁用定时和 webhook。安装时用 `--platform-root`（默认 `E:/jvjv/local-platform`）读取平台配方，把 `build-deploy` 的默认分支从权威文件派生出来，不再从本仓库的清单里取：

| 入口 | 用途 |
| --- | --- |
| 流水线 `mqtt-release` | 独立 worktree → Maven 测试 → 构建 API/Web → 独立网络/卷验收 → 发布及健康检查 |
| 流水线 `mqtt-restore` | 使用同一发布引擎回退上一已验证版本 |
| 操作 `mqtt-deploy-version` | 运行参数中填写已存在清单的 `version`，重新部署该版本 |
| 操作 `mqtt-list-refs` | 只读：留空 `ref` 刷新来源分支/标签与已登记清单；填写 `ref` 预览该分支是否已登记及将构建的完整提交。不写 claim、不建任务、不构建不部署 |
| 操作 `mqtt-reconcile` / `mcp-reconcile` | 填写已有 Windows 任务 ID `job`，重新读取执行结果，不提交新任务 |
| 操作 `mcp-deploy-version` | 发布已验证 MCP 版本清单；当前为源码快照叠加镜像，不是 Git 构建 |
| 流水线 `mcp-restore` | 回退上一已验证 MCP 版本 |

Action 日志包含 Windows 任务 ID、阶段、提交、不可变镜像 ID 和结果。成功后同步 `mqtt-release` 或 `mcp-deploy-version` 的说明，标明最近一次验证的版本/镜像；这不是实时容器监控，实时状态仍看 Local 的容器页。

任务先持久化领取记录，再交给 Pipeline；同 ID 只对账、不重跑。若 Pipeline 在入队前拒绝（例如分支未在权威配方登记、来源模式不可构建），结果里保留该失败原因并保持 `failed`，不会被后续对账改写成 `interrupted`；`interrupted` 只用于"已领取但没有执行记录"的真崩溃现场。启动时发现活跃执行记录会拒绝启动，需核对容器/发布历史后处理；不能删除记录或换 ID 重试不确定的发布。**取消 Komodo Action 只停止等待，已领取的 Windows 发布事务会继续完成**，请使用日志中的任务 ID 查询 `.local/mqtt-queue/responses/<ID>.json`。

交接时旧控制面以 `CONSOLE_READ_ONLY_PROJECTS=mqtt-sandbox,mcp-gateway` 禁用三个写入 API，并将按钮变为只读，原历史卷保留。这个开关依赖已更新的本机 controller.py；不能只给旧镜像添加环境变量便认为已禁用。维护脚本 Stack/Update-Service 仍保留人工修复能力，不能在 Komodo 发布期间并行使用。

旧控制面改动保存在 `legacy-controller-readonly.patch`。新安装时先在 local-platform 目录运行 `git apply --ignore-space-change --check <此补丁绝对路径>`，通过后应用并重建控制面；已应用的机器可用 `git apply --ignore-space-change --reverse --check` 验证，勿重复应用。切换前后都要检查旧队列没有 queued/running 任务，实测旧三个写入入口409后才认定交接完成。

后续更新上游只需复核 `adaptations/`、本机引擎契约及 Action API；此适配未修改 Komodo 后端。自动检查：`python -m unittest discover -s adaptations/local-platform -p 'test_*.py'`。单元测试不替代真实构建、隔离 MQTT 验收、回退和浏览器入口验证；当次实际证据记录在原实施状态清单。

## 接入新项目与版本来源

`project_registry.py` 校验注册表，`release-action.ts` 是共用模板。物理队列目录仍叫 `.local/mqtt-queue`，以保留已有任务账本，不按项目改名。新增项目先在 local-platform 登记服务、隔离依赖、构建配方与验收，再将允许的操作加入此注册表；不能把 MQTT 的协议验收套用于其他项目。修改注册表后在任务空闲时重启 Windows 工作进程，再运行安装器。已领取任务按原领取记录对账，策略撤销不重跑任务。

Git 来源展示完整提交；非 Git 项目展示源码快照或镜像来源，不能伪造 Git 版本。MCP 管理台当前不是 Git 仓库，仅开放 deploy/rollback；要启用 build-deploy，需先补齐 Git 来源、专用构建与独立验收配方。

发布指定 MCP 版本：打开自动化脚本 `mcp-deploy-version` 的配置，在 JSON 参数中设置 `{"operation":"deploy","version":"2026-09-21.komodo-link.1"}`，保存后运行并确认。版本必须已有通过验收的不可变清单。当前采用 Windows 引擎执行 Compose，因此保留原 Windows bind mount；无需为了接入而搬动业务数据。通用接入步骤见本机 `docker-project-onboard` skill。

## 应用栈：现有容器组的查看与启停

运行 `python adaptations/local-platform/adopt_stacks.py` 预览，添加 `--apply` 将已登记且实际存在的 Compose 项目登记到 Komodo 的“应用栈（容器组）”。登记不调用 deploy/up、不启动停止项目、不复制凭据或挂载配置。同名 Stack 必须由本导入器管理，否则拒绝覆盖。仅存在的服务会被列入，例如 infra 当前只含 MySQL，不会创建尚未使用的 Redis/PG/Mongo。重复导入可刷新服务清单。

进入应用栈默认查看“服务”，可看所属容器、状态、端口及日志，原生启动/停止作用于现有 Compose 项目。2026-09-22 已登记 docforge、docforge-legacy、fmea、infra、mcp、mqtt-sandbox、project-console 共8组；未启动原先停止的 spec 或旧版项目。Komodo 上游将 Created 容器汇总为 unhealthy，因此尚未首次启动的 docforge-legacy 以服务明细的 created 状态为准。

这里的 UI 文件仅为服务镜像清单，**不是完整部署配置**。pre_deploy 明确拒绝重新部署；版本更新继续走 Windows 发布流水线，不能移除保护后用此文件部署。镜像清单是登记时快照，实时镜像看容器详情，发布版本看对应发布 Action/Procedure。

原生 Stack 启停与 Windows 发布引擎没有共同事务锁，发布时不得并行启停，未知发布结果先按任务ID对账。跨组依赖不会自动启动：业务组前先启动其共享数据库/依赖；停止 infra 会影响依赖它的应用。分组启动只恢复已有容器，不创建缺失服务。维护入口不等于项目已具备源码CI或正式业务迁移完成。

本机实际验收：MQTT 通过 Komodo 原生 StopStack/StartStack 完成整组停止再启动，原容器ID、镜像、挂载不变且恢复healthy。保护性 DeployStack 演练必须在 Pre Deploy 阶段失败并保持容器不变；证据存 `.local/stack-lifecycle-verification.json` 和 `.local/stack-deploy-guard.json`。
