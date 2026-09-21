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

宿主 bind mount 会拒绝导出，需要先明确 Windows→Periphery 路径映射。不要为了导入而移除挂载。当前 MCP 组存在此类挂载，不能直接套用 MQTT 方案。

正式接管前还需要：跨组依赖顺序、统一发布锁、Windows 构建替代或桥接、独立业务验收、版本来源映射、不同版本恢复、旧历史保留。新旧管理台不得同时管理同一项目的发布。此阶段没有启用自动更新或同步删除。
