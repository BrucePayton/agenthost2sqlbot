# Davinci OBID UAT 运维手册

## 适用边界

本方案只用于当前 UAT 单机 Inline Execution。浏览器提交的
`X-Davinci-ObId` 尚不是加密凭证，因此服务必须仅允许受控 UAT Davinci
页面和运维网络访问，不得暴露给公网，也不得作为生产鉴权方案。

健康检查必须返回：

```json
{
  "identity_mode": "obid",
  "deployment_constraint": "single_instance",
  "security_marker": "UAT_OBID_UNVERIFIED"
}
```

一期明确只运行一个 Agent Host 进程。不要用不同的 `APP_DATA_DIR` 绕过本机
实例锁，也不要启动第二个 API/Worker 副本。

## 部署配置

Agent Host 必需配置：

```dotenv
APP_ENV=uat
APP_IDENTITY_MODE=obid
APP_RUNTIME_MODE=local_inline
DATABASE_URL=postgresql+asyncpg://<user>:<password>@<postgres-host>:5432/<database>
APP_PERSONAL_WORKSPACE_TEMPLATE_ID=example

DAVINCI_LOCAL_INTEGRATION=1
DAVINCI_LOCAL_PUBLIC_ORIGIN=https://uat-agent.aihuishou.com
DAVINCI_LOCAL_PARENT_ORIGINS=["https://abdavinci-uat-up.aihuishou.com"]
```

两个 Origin 必须是完整、精确的 HTTPS Origin，不带路径、query、通配符或尾部
斜杠：

- Davinci 父页面：`https://abdavinci-uat-up.aihuishou.com`
- Agent iframe/API：`https://uat-agent.aihuishou.com`

网络侧只允许 UAT Davinci 网关、受控办公网和运维探针访问 Agent Host；数据库
只允许 Agent Host 和备份作业访问。Nginx/API Gateway 不得用通配 CORS，也不得
重写或打印 `X-Davinci-ObId`。

## 发布步骤

1. 备份 PostgreSQL，并记录 Agent Host 与 Davinci 构建版本。
2. 停止旧 Agent Host，确认没有运行中的 Turn。
3. 配置以上环境变量，执行 `uv run python -m app.db.cli` 完成数据库迁移。
4. 启动一个 Agent Host 进程，检查 `/api/health` 的三个 UAT 标记。
5. 发布 Davinci 构建；确认 iframe 指向 Agent HTTPS Origin。
6. 按下方浏览器清单验收，再开放给少量 UAT 用户。

仓库内可重复执行的自动化 Gate：

```bash
bash scripts/verify-davinci-obid-phase-1.sh
```

脚本会启动一次性 PostgreSQL、迁移数据库、执行 OBID/Workspace/Session 隔离与
前端协议测试，并在退出时删除测试容器和数据卷。

## 浏览器验收

- 登录用户 A：bootstrap 请求体中的 `obId` 是 A，URL、form、postMessage、
  artifact body 和 AG-UI forwarded props 中都没有 OBID。
- 管理员从被查看用户 A 切到 B：只变化 `defaultUser`，Agent iframe 不重建，
  snapshot 身份 Header 仍是登录者 A。
- 真正切换登录者 actor：旧 iframe/Bridge 被销毁，新 iframe 重新 bootstrap。
- 用户 A 和 B 分别只有自己的 Personal Workspace 与 Session；互相访问返回 404。
- 同一用户创建两个 Session：目录与对话恢复 ID 独立，Workspace Auto Memory 共享。
- Native V2 至少跑通一次读取工具和一次修改工具；父页返回 Tool Result 后，
  continuation 创建新的 Run，Session 历史可以恢复。
- 刷新页面后恢复当前 actor + workspace namespace 下的 Session，不读取其他 actor
  的 localStorage key。

## 备份、回滚和故障处理

PostgreSQL 是用户、Workspace、Session、Turn 和消息的权威元数据；
`APP_DATA_DIR` 是一期附件、输出、Session 工作目录和 Auto Memory 的权威文件。
发布前两者必须在同一时间窗口备份。

回滚步骤：

1. 立即停止接受新 Turn，等待或中止运行中的 Turn。
2. 停止当前 Agent Host，恢复上一版 Agent Host 和 Davinci 构建。
3. 若新迁移尚未产生用户数据，可按备份恢复 PostgreSQL；一旦已有新数据，禁止
   直接 downgrade 或只回滚文件，先保留现场并人工核对。
4. 恢复对应 `APP_DATA_DIR` 备份，启动且只启动一个旧 Agent Host。
5. 重新检查健康、附件下载、Session 历史和一个只读工具调用。

若发现两个用户看到同一 Session、OBID 出现在日志/URL、或第二个 Agent Host
实例被启动，应立即关闭 UAT Agent 入口并按身份隔离事故处理，不能仅刷新页面。
