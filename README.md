# Claude Workspace Agent MVP

本项目采用 [MIT License](LICENSE)。第三方依赖遵循各自的许可证。

问数智能体与 SQLBot 高级小助手的实现、配置和真实验收门禁见
[`docs/data-agent-implementation.md`](docs/data-agent-implementation.md)，完整链路图见
[`docs/agenthost-data-mcp-sqlbot-report-flows.drawio`](docs/agenthost-data-mcp-sqlbot-report-flows.drawio)。

面向本机或受信任部署的 Workspace Agent 工作台。页面可以选择服务端 Workspace、创建或恢复历史 Session、管理 Skills、上传图片和文件，并通过 Claude Agent SDK 持续对话。

## 能力范围

- FastAPI 单体页面服务，无 Node 构建链。
- Workspace 级 `CLAUDE.md`、MCP 和工具权限，以及全局/个人 Workspace Skills。
- 精确保存并恢复 Claude `session_id`。
- 图片多模态输入和普通文件 workspace 挂载。
- SQLite 页面历史与 Claude 本地 transcript 双轨持久化。
- SSE 文本、工具、用量、压缩、错误和终态事件。
- Session 列表、重命名、删除、停止和服务重启恢复。
- Session 在团队 Workspace 中仍只对创建者可见。
- 同一用户在同一 Workspace 的不同 Session 共享 Claude Code Auto Memory。
- Auto Memory 不跨 Workspace，也不会自动写入团队共享知识。

本版本只提供 Mock 身份，不包含真实登录、令牌校验或可信身份代理。Mock 模式仅适用于本机或其他受信任部署，必须保持在可信网络边界内，不得直接暴露公网。生产部署必须先接入能够从已验证请求上下文解析用户身份的 `IdentityProvider`，不能信任浏览器提交的用户或角色字段。

第一阶段团队成员与角色的唯一来源是 Mock 配置夹具 `MOCK_WORKSPACE_ROLES`：启动同步只为当前 Mock 用户写入这些角色。当前没有成员/角色管理 API 或页面，也没有实现团队 Workspace 的 exactly-one-owner 生命周期；真实成员管理、角色变更和 owner 治理留待后续身份系统阶段。

## 环境要求

- Python 3.12.x（`.python-version` 与依赖声明统一限制到 3.12，已有 3.12.7 可继续使用）。
- [uv](https://docs.astral.sh/uv/)。
- Anthropic Messages API 兼容的 Base URL。
- 可用的 API Key。

Claude Agent SDK 会自动携带 Claude Code CLI，不需要单独安装 Claude Code。

## 安装

```bash
cd /Users/a110356/work/code/claude_workspace_mvp
uv sync --frozen
```

布局求解器随默认依赖安装，不需要单独的 solver 虚拟环境。
本次清华镜像下载返回 403，锁文件回退到官方 PyPI；部署使用 `--frozen` 安装锁定产物。
Linux x86_64 的依赖基线兼容 glibc 2.17：OR-Tools 9.11.4210、
NumPy < 2.3、pandas < 2.3、asyncpg 0.30.0、greenlet 3.2.4。
生产 Python 3.12.7 / glibc 2.39 可使用同一锁文件；下次同步会补装求解器，
并将此前锁定的 asyncpg 0.31.0 调整为 0.30.0。
此调整不涉及数据库结构或系统 glibc。首次从 3.13 迁移时，先停止服务并保留旧虚拟环境，
再用 `uv sync --frozen --no-dev --python 3.12` 创建新环境；不要同步仍在运行的服务环境。
部署后必须验证实际布局求解，不能只以 `/api/health` 返回成功作为验收。

复制环境变量示例：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
ANTHROPIC_BASE_URL=https://your-anthropic-compatible-proxy.example.com
ANTHROPIC_API_KEY=your-secret-key
# Bearer 兼容网关改用下一行，并移除 ANTHROPIC_API_KEY：
# ANTHROPIC_AUTH_TOKEN=your-bearer-token
WORKSPACES_ROOT=/Users/your-name/path/to/claude_workspace_mvp/workspaces
CLAUDE_SKILLS_ROOT=/Users/your-name/.claude/skills
CODEX_SECURITY_MCP_ENTRYPOINT=/absolute/path/to/codex-security/mcp/server.mjs
DATA_ANALYTICS_MCP_ENTRYPOINT=/absolute/path/to/data-analytics/mcp/server.cjs
AHS_HIVE_QUERY_MCP_ENTRYPOINT=/absolute/path/to/luxury_data/hive_query_mcp_server/server.py
CODEX_HOME=/Users/your-name/.codex

CLAUDE_MODEL=claude-sonnet-4-6
APP_HOST=127.0.0.1
APP_PORT=8000
APP_DATA_DIR=./data
APP_RUNTIME_MODE=local_inline
APP_RUNTIME_COHORT=local
APP_RUNTIME_IMAGE_DIGEST=local
APP_RUNTIME_PROTOCOL_VERSION=1
MOCK_USER_ID=mock-user
MOCK_USER_SUBJECT=mock-user
MOCK_USER_DISPLAY_NAME=Mock User
MOCK_PERSONAL_WORKSPACE_ID=example
APP_PERSONAL_WORKSPACE_TEMPLATE_ID=davinci-dashboard
MOCK_WORKSPACE_ROLES='{"example":"owner"}'
MAX_SKILL_FILE_SIZE_MB=10
MAX_SKILL_BUNDLE_SIZE_MB=50
MAX_SKILL_FILES=200
```

`ANTHROPIC_BASE_URL`、`WORKSPACES_ROOT` 和一种模型凭证缺一不可。标准 Anthropic/API-Key 兼容代理配置 `ANTHROPIC_API_KEY`；要求 `Authorization: Bearer` 的兼容网关配置 `ANTHROPIC_AUTH_TOKEN`。两者不要同时配置。`WORKSPACES_ROOT` 必须是存在的绝对目录且不能是软链接。

`CLAUDE_MODEL` 必须填写代理实际支持的模型标识。Workspace 未显式配置 `model` 时继承该值；只有确认代理支持时才在 `workspace.yaml` 中覆盖模型。

使用 `qwen3.8-max` 时，建议从 `CLAUDE_THINKING_BUDGET_TOKENS=4096` 开始，并按真实 turn 时长调整；`CLAUDE_STREAM_IDLE_TIMEOUT_MS` 可控制流式响应空闲超时，Claude CLI 的有效下限为 `300000` 毫秒。

模型凭证只注入服务端 Claude 子进程环境，不会写入 SQLite、页面、Prompt 或普通日志，也不会传入 Workspace 的 MCP 子进程。

`workspace.yaml` 可以引用任意服务进程环境变量。除应用自身声明的设置外，MCP
变量必须实际导出到服务进程；使用 `.env` 时可在启动前执行：

```bash
set -a
source .env
set +a
```

## 启动

开发模式：

```bash
./scripts/run-dev.sh
```

稳定运行模式：

```bash
uv run workspace-agent
```

也可以直接运行：

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。健康检查位于 [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)。

### SQLite 与 Skill Artifact 单机部署

本地或单机 UAT 不需要 PostgreSQL。推荐把数据库与 Skill Artifact 放在同一个持久化
数据目录：

```env
APP_DATA_DIR=/data/agent-host
SKILL_ARTIFACT_BACKEND=filesystem
SKILL_ARTIFACT_ROOT=/data/agent-host/skill-artifacts
# JSON array used only to bootstrap bindings for matching existing identities:
APP_SKILL_ADMIN_SUBJECTS='["mock-user"]'
```

省略 `DATABASE_URL` 时，服务使用 `APP_DATA_DIR/app.db`。`APP_DATA_DIR` 必须是宿主机
bind mount 或持久卷，不能使用容器 rootfs；filesystem Artifact 模式只支持一个 Agent
Host 实例。当前版本只提供 filesystem 后端，没有 OSS、S3 或 MinIO SDK/适配器。

备份必须同时覆盖 SQLite 和 `skill-artifacts`，并保留二者的一致时间点。SQLite 可在服务
运行时使用 `sqlite3 /data/agent-host/app.db ".backup '/backup/app.db'"` 做在线备份；或者先
停止写入再复制 `app.db`。随后复制 `/data/agent-host/skill-artifacts`，以及 Session、
transcript 和 Memory 所需的其余 `APP_DATA_DIR` 内容。恢复时应恢复同一批备份并保持原
Artifact Key。

平台 Skill 管理员绑定只接受已经出现过的 identity subject。引导变量仅在启动时把匹配
的现有身份写入绑定；日常授权使用 CLI：

```bash
uv run python -m app.skills.admin_cli grant --subject mock-user
uv run python -m app.skills.admin_cli list
uv run python -m app.skills.admin_cli revoke --subject mock-user
```

数据库迁移在启动时自动升级到当前 Alembic head。升级自旧版 BLOB Skill 时，启动迁移器
会校验旧 `SKILL.md` 与支持文件的 Hash，再把不可变 Bundle 写入 filesystem Artifact；
过程可重试且幂等，完整迁移前不会清空旧内容。校验失败会中止启动，须先修复旧数据或
恢复备份。升级前已有的 schema-v2 Session 不会被改写。

上传、替换、启停和归档都只影响之后创建的新 Session；已有 Session 始终保留创建时的
Skill 快照。未来多节点部署必须同时采用 PostgreSQL 和共享 Artifact 适配器，并先按原
Artifact Key 迁移不可变对象；在共享适配器交付前，不得把 filesystem 模式横向扩容。

### 只读 Session Inspector

需要跨用户排查持久化 Session 时，可在任意环境显式开启独立只读页面：

```env
APP_SESSION_INSPECTOR_ENABLED=true
APP_SESSION_INSPECTOR_USERNAME=inspector
APP_SESSION_INSPECTOR_PASSWORD=replace-with-a-long-random-secret
```

重启后访问 `/inspector`，使用上述 HTTP Basic 凭据登录。页面直接读取当前环境配置的
SQLite/PostgreSQL，支持按 Session ID、标题、用户和 Workspace 搜索，并展示脱敏后的
事件、工具调用、耗时与 Workspace 快照。Inspector 默认关闭且不注册路由；密码至少
24 个字符。它没有发送消息、修改、删除或发布接口。非本地环境必须通过 HTTPS 暴露，
不要与普通业务用户共享 Inspector 凭据。

### Davinci Mock × AG-UI iframe MVP

该验证会同时启动 Agent Host（`127.0.0.1:8000`）和 Mock Davinci
（`127.0.0.1:4173`），用独立 iframe Session 验证当前仪表盘读取和父页面导航：

```bash
cd /Users/a110356/work/code/claude_workspace_mvp
uv sync --frozen
npm ci
npm run build:agui
export APP_RUNTIME_MODE=local_inline
export CLAUDE_MODEL=qwen3.8-max
./scripts/run-davinci-agui-mvp.sh
```

打开 [http://127.0.0.1:4173/dashboard/1024](http://127.0.0.1:4173/dashboard/1024)，点击右下角
“AI”，新建 Session，依次输入“解读当前仪表盘”和“打开数据集页面”。第一问应读取父页面
Store 中的 `4,734`、`-10.88%`、`40,388,380`，第二问应把父页面切换到
`/datasets`，且 Session 和 iframe 不变。然后在数据集页输入“帮我解读仪表盘”，同一轮应先
导航回 `/dashboard/1024`，再读取并解读上述三个指标，Session 和 iframe 仍保持不变。

无需模型调用的确定性 Gate：

```bash
uv run pytest tests/browser/test_davinci_agui_mvp.py -q
```

真实 Qwen Gate 会产生模型费用，仅在显式设置开关后运行：

```bash
RUN_LIVE_DAVINCI_AGUI=1 \
CLAUDE_MODEL=qwen3.8-max \
uv run pytest tests/live/test_davinci_agui_qwen.py -q -s
```

这是 loopback-only 的架构 MVP，只验证 AG-UI、Claude Agent SDK、前端 Tool Bridge、
`postMessage` 和 Session 连续性；不验证 Docker/OpenSandbox、真实 Davinci 鉴权或真实数据 API。

## Workspace

每个 Workspace 是 `WORKSPACES_ROOT` 下的一级子目录：

```text
workspaces/
└── sales-analysis/
    ├── workspace.yaml
    ├── CLAUDE.md
    ├── .claude/
    │   └── skills/
    │       └── sql-analysis/
    │           └── SKILL.md
    └── seed/
        └── reference.md
```

`workspace.yaml` 示例：

```yaml
version: 1
id: sales-analysis
name: 销售分析
description: 查询销售数据并生成分析报告
model: claude-sonnet-4-6

skills:
  - sql-analysis

allowed_tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - WebSearch
  - WebFetch
  - Skill
  - mcp__davinci_data__*

mcp_servers:
  davinci_data:
    type: http
    url_env: DAVINCI_DATA_MCP_URL
    authorization_source: davinci_session
  local_renderer:
    type: stdio
    command: node
    entrypoint_env: LOCAL_RENDERER_MCP_ENTRYPOINT
    args:
      - --stdio
    env_vars:
      - RENDERER_CONFIG_DIR
```

然后在服务进程环境提供：

```env
DAVINCI_DATA_MCP_URL=https://davinci-data-mcp.example.com/mcp
LOCAL_RENDERER_MCP_ENTRYPOINT=/absolute/path/to/server.cjs
RENDERER_CONFIG_DIR=/absolute/path/to/config
```

`authorization_source: davinci_session` 会在每次运行时按当前用户注入已经由 Davinci
`currentUser` 验证过的 bearer；Token 不进入 YAML、Workspace 快照或模型上下文。
若 MCP 使用固定服务身份，仍可改用 `authorization_env`，两者不能同时配置。HTTP MCP
必须使用 HTTPS。stdio MCP 不经过 Shell，入口变量必须指向绝对普通文件，`env_vars`
只列出该服务需要显式注入的变量。

Workspace `id` 必须与目录名相同。`workspace.yaml.skills` 是一次性 bootstrap 清单：未配置 `skills_root_env` 时从 Workspace 的 `.claude/skills/<name>/` 尝试导入；配置后从对应的外部根目录尝试导入。Skill 来源缺失或内容无效只会记入 bootstrap 报告，不会因此把 Workspace 标记为不可用。目录名、YAML、MCP 或其他 Workspace 配置错误仍会在页面标记为不可用，但不会阻止其他 Workspace 工作。

也可以把服务端受信任的外部目录作为一次性 Skill bootstrap 来源，避免人工逐个导入：

```yaml
skills_root_env: CLAUDE_SKILLS_ROOT
skills:
  - autoplan
  - lark-doc
```

此时环境变量应指向存在的绝对目录。`workspace.yaml.skills`、Workspace 本地 Skill 目录与 `skills_root_env` 外部目录都只是一阶段的一次性 bootstrap 输入，不是运行时 Skill 挂载：系统把有效 Skill 导入托管存储，之后的上传替换、启停、归档和新 Session 快照均只以托管数据为准，不会继续读取或覆盖自 YAML、本地目录或外部根目录。启动日志会逐项记录 `created`、`skipped`、`conflict`、`failed` 并输出汇总；存在 `failed` 时不会写完成标记，下次启动会重试。已经成功导入后又被用户归档的 bootstrap Skill 会作为历史完成项跳过，不会被复活；所有来源不再失败后才写完成标记，后续启动不再扫描这些来源。

个人 Workspace 头部的 `N Skills` 计数按钮是全屏 Skill 管理入口，固定展示全局和个人
两个分组。个人 Workspace owner 可以为自己的 Workspace 开关全局 Skill，并上传、
替换、重命名导入、启停或归档个人 Skill；`skill_admin` 还可以上传、替换、重命名导入
或归档全局 Skill。本期没有团队 Skill 管理 UI、浏览器内创建/编辑、跨 Workspace 复制、
市场、分享或 Git 同步。

文件夹必须以 `SKILL.md` 为根文件；也可以上传 `.skill`/Zip。浏览器会发送真实文件内容
和相对路径，`scripts/`、`assets/` 等支持文件会原样进入不可变 Artifact，不会扫描服务端
本机目录，也不会与原文件夹持续同步。新个人 Skill 与重命名导入默认开启，新全局 Skill
对各个人 Workspace 也默认开启；同名且内容相同的上传幂等，同名但内容变化时必须选择
覆盖、重命名或取消。覆盖保留 Skill ID 和启停状态。文件夹和压缩包共用默认限制：单个
支持文件 10 MiB、总量 50 MiB、最多 200 个支持文件，可通过
`MAX_SKILL_FILE_SIZE_MB`、`MAX_SKILL_BUNDLE_SIZE_MB` 和 `MAX_SKILL_FILES` 调整。

项目内置通用模板 `workspaces/example`，以及模型固定为 `qwen3.8-max` 的
`workspaces/davinci-dashboard`。后者仅开放 Davinci 数据只读 MCP 与受控页面工具，
并包含 `configure-dashboard-widget` Skill；本地 Davinci 联调应设置
`APP_PERSONAL_WORKSPACE_TEMPLATE_ID=davinci-dashboard`。缺少对应环境变量时，
该 Workspace 会显示为不可用。
配置更新只影响之后新建的 Session；已有 Session 继续使用创建时的不可变快照。

## Session 与文件

Session、页面历史、Claude transcript 和个人 Workspace 记忆均持久化在
`APP_DATA_DIR`：

```text
APP_DATA_DIR/
├── app.db
├── skill-artifacts/           # 全局与个人 Skill 的不可变 Bundle
├── sessions/<session-id>/
│   ├── workspace/
│   │   ├── CLAUDE.md
│   │   ├── workspace.snapshot.yaml
│   │   ├── .claude/skills/   # 新 Session 固化已启用托管 Skill 的普通文件副本
│   │   ├── attachments/
│   │   └── outputs/
│   └── claude-config/        # Claude transcript 与本地状态
└── memories/users/<opaque-user>/workspaces/<opaque-workspace>/
    ├── MEMORY.md
    └── <topic>.md
```

每个 Turn 启动一个短生命周期 Claude 子进程。第一次成功后保存 Claude `session_id`；后续 Turn 使用同一绝对 `cwd`、`CLAUDE_CONFIG_DIR` 和精确 `resume`。

同一个用户在同一个 Workspace 下的不同 Session 拥有不同的 `workspace/` 和
`claude-config/`，但解析到同一个不透明 Memory 目录。服务会在每个 Turn 自动读取
`MEMORY.md`（如存在），并让 Agent 自动判断稳定偏好、长期事实或明确记忆请求是否值得
写入；不同用户或不同 Workspace 不共享该目录。Session 即使位于团队 Workspace，列表、
消息、附件和 Turn 也只允许创建者访问。

新 Session 只使用创建时已启用的托管 Skill，并把内容复制为普通文件；之后编辑或启停 Skill 不会改变该 Session 的自动补全或执行快照。升级前已经存在的 Session 不会被重写：其中原有的 Skill 软链接仍保持旧版兼容行为，直到该 Session 被删除。

页面聊天历史来自 SQLite，因此 Claude 自动上下文压缩不会删除页面历史。Claude resume 使用其本地 transcript。部署时必须把整个 `APP_DATA_DIR` 挂载为持久卷；备份和恢复必须覆盖完整目录，不能只备份 SQLite，否则 Session resume 或个人记忆会丢失。

### 第一期运行边界

- 当前只支持一个应用实例。
- 多实例前必须迁移数据库、Claude transcript、Memory 存储和作用域锁。
- Auto Memory 文件是明文；数巢多人部署前必须完成可信身份与运行时文件系统隔离。
- 当前没有团队动态记忆、跨 Workspace 记忆或独立记忆管理页面。

### Runtime V2 Phase 0 边界

Phase 0 只完成后续多机 Kubernetes Runtime 所需的应用内边界，不改变当前部署
形态。当前唯一支持的执行模式是 `local_inline`：FastAPI 进程内的 Dispatcher 为每个
Turn 启动现有 Claude Agent SDK 执行流程，尚没有远端 Runner、容器调度或租户级安全
隔离。

几个对象的含义如下：

- Product Workspace：页面中的个人空间或团队空间，是配置、成员权限、Skills、MCP 和
  记忆作用域的产品边界。
- Session Workdir：单个 Session 的固定工作目录与 Claude transcript 目录；当前位于
  `APP_DATA_DIR/sessions/<session-id>/`。
- Team Knowledge Bundle：Workspace 启用的团队 Skills；创建 Session 时固化到 Workdir，
  不是运行时共享可写目录。
- Runtime Cohort：一组经过相同验证的 SDK、CLI、MCP SDK、内部 Runner 协议和能力声明。
  Phase 0 的 cohort 只是本地观测元数据。

应用只通过 `app/runtime/contracts.py` 中的项目自有接口调用模型运行时；只有
`app/runtime/claude.py` 可以导入 `claude_agent_sdk`。这使后续 SDK 升级或 Kubernetes
Runner 接入只需要替换 Adapter/Dispatcher，而不把 SDK 类型扩散到业务层。

`/api/health` 会返回脱敏的 runtime mode、cohort、image digest、内部协议版本、能力
列表，以及 Claude Agent SDK、bundled CLI 和 MCP Python SDK 版本。这里的
`APP_RUNTIME_PROTOCOL_VERSION` 是项目自己的 Runner 协议，不是 MCP protocol revision；
`mcp_python_sdk_v2=false` 也只表示当前 Claude Agent SDK 依赖约束尚未验证 MCP Python
SDK v2，不能据此推断 MCP 服务端协议是否支持某个 revision。

本地默认配置为：

```env
APP_RUNTIME_MODE=local_inline
APP_RUNTIME_COHORT=local
APP_RUNTIME_IMAGE_DIGEST=local
APP_RUNTIME_PROTOCOL_VERSION=1
```

非本地 Runtime image 必须记录为 `sha256:<64-hex-digest>`，不能使用可变 tag。Phase 0
仍是单实例、Mock 身份、SQLite 和共享宿主机文件系统方案，不是生产多租户实现；真实
多租户隔离、PostgreSQL、对象存储、Kubernetes Runner 和安全 Gate 属于后续阶段。

### Runtime V2 Phase 1 控制平面

Phase 1 增加了可水平扩展的 PostgreSQL 控制平面、OIDC 身份、Space 成员投影和持久化
Turn/SSE 事件。生产配置必须同时满足：

```env
APP_ENV=production
APP_IDENTITY_MODE=oidc
APP_RUNTIME_MODE=execution_disabled
DATABASE_URL=postgresql+asyncpg://workspace:password@postgres:5432/workspace
APP_OIDC_ISSUER=https://identity.example.com
APP_OIDC_AUDIENCE=workspace-agent
APP_OIDC_JWKS_URI=https://identity.example.com/.well-known/jwks.json
APP_SPACE_AUTHORITY_URL=https://spaces.example.com
APP_SPACE_AUTHORITY_TOKEN=replace-with-secret
```

该模式允许启动多个无状态 API 副本，但只接收并持久化 `queued` Turn，不在 API 进程
中调用 Claude SDK。成员关系以 Space 服务为权威，过期投影会重新校验并在撤权时失败
关闭；Session 仍仅创建者可见。SSE 先回放 PostgreSQL 的持久事件，再用
`LISTEN/NOTIFY` 做可丢失的唤醒信号。

本地可用以下脚本启动独立 PostgreSQL、OIDC/Space 测试权威和两个 API 副本，验证跨
副本幂等、活动 Turn 约束、隐私、撤权和 SSE：

```bash
bash scripts/verify-phase-1.sh
```

Phase 1 尚未启用 Kubernetes Runner、S3、Redis 或生产凭据下发；完整执行仍只在开发
环境的 `local_inline` 单实例模式中可用。

### Runtime V2 Phase 2A：本地 OpenSandbox Docker

Phase 2A 增加了独立 Worker 和 OpenSandbox Server。API 只把 Turn 持久化到 PostgreSQL；
Worker 领取任务后，为每个 Session 创建或复用一个 Docker sandbox，并把执行请求写入固定
的 `/session/control/request.json`。Session 文件和个人记忆分别使用保留的 Docker named
volume；空闲 5 分钟后销毁容器，下次请求创建新 generation 并重新挂载原 volume。

本地完整验收命令：

```bash
bash scripts/verify-phase-2a.sh
```

脚本会启动隔离的 PostgreSQL 和 OpenSandbox Compose project、构建非 root Runner 镜像、
运行 Python/JavaScript/安全测试及真实 Docker 假模型纵向测试，并只清理本次 Gate 创建的
资源。手工启动时先启动 OpenSandbox，再分别启动 API 与 Worker：

```bash
export OPENSANDBOX_SERVER_API_KEY=replace-local-server-key
docker compose -f deploy/opensandbox/compose.yaml up -d

# API 环境不应包含 OPENSANDBOX_API_KEY
APP_RUNTIME_MODE=opensandbox_docker uv run uvicorn app.main:app

# 仅 Worker 持有管理 API key；fake 用于无模型凭据的本地验收
OPENSANDBOX_API_KEY="$OPENSANDBOX_SERVER_API_KEY" \
OPENSANDBOX_RUNNER_RUNTIME=fake \
uv run python -m app.sandbox.main
```

`OPENSANDBOX_RUNNER_IMAGE` 必须是 `name@sha256:<digest>` 或本机不可变
`sha256:<image-id>`，不能使用可变 tag。真实 Claude 模式改为
`OPENSANDBOX_RUNNER_RUNTIME=claude`，并将 `OPENSANDBOX_ALLOWED_HOSTS` 配置为
`ANTHROPIC_BASE_URL` 的精确主机名。Worker 会通过 OpenSandbox 官方 SDK 为每个
sandbox 创建或刷新 Credential Vault；Runner 只看到固定占位 key，真实模型密钥不会写入
Runner 请求、环境、命令行或 volume。可用已配置的部署凭据运行完整真实模型 Gate：

```bash
RUN_LIVE_OPENSANDBOX_CLAUDE=1 bash scripts/verify-phase-2a.sh
```

该命令不会打印 Base URL 或 API key；它会额外验证真实对话、Session resume、managed
Skill、跨 Session Auto Memory、usage 以及删除 Vault 后失败关闭。

Phase 2A 只是在单机 Docker 上验证 Runtime 接口、持久化屏障、取消、恢复、租约、热复用
和资源回收。生产仍拒绝 `opensandbox_docker`，只允许 `execution_disabled`；Kubernetes
调度、CSI/RWOP、CNI、RuntimeClass、多机硬隔离与故障转移属于 Phase 2B。

### Runtime V2 Phase 2A.2：Docker Web 本地部署

Phase 2A.2 把 API、Worker、PostgreSQL 和 OpenSandbox 打包为一个可直接操作的本机
Docker Web 栈。它仍使用 Mock 身份、单宿主机 Docker 和 loopback Web 端口，只适合
本机开发或受信任网络中的工程验收，不是生产多租户部署，也不能替代后续 Kubernetes
隔离方案。

无需模型凭据的确定性启动：

```bash
bash scripts/docker-web.sh up --fake
open http://127.0.0.1:8765
```

真实 Claude-compatible 模式：

```bash
cp .env.docker.example .env.docker.local
# 编辑 ANTHROPIC_BASE_URL、ANTHROPIC_API_KEY、DOCKER_WEB_MODEL
# 和 OPENSANDBOX_ALLOWED_HOSTS；不要提交 .env.docker.local
bash scripts/docker-web.sh up --claude
```

Docker 的 Data Agent 配置也统一写入 `.env.docker.local`，不要手工修改生成的
`.runtime/docker-web/*.env`。启动器保留已识别的 `DATA_AGENT_*`、`SQLBOT_*`、
`STARROCKS_*` 配置；`DATA_AGENT_STARROCKS_MANIFEST` 填宿主机文件路径，由启动器
为 API、Worker 和迁移容器生成只读挂载。启用后会为本地 Mock 用户添加
`data-question` Workspace 角色。完整连接配置及凭据须来自同一 SQLBot 实例。
`/api/health` 就绪只证明通用服务可用，Data Agent 还须检查
`/api/data-agents/health`、数据集、Agent 列表和本地高级小助手的绑定。

生命周期命令：

```bash
bash scripts/docker-web.sh status
bash scripts/docker-web.sh logs api
bash scripts/docker-web.sh logs worker
bash scripts/docker-web.sh logs postgres
bash scripts/docker-web.sh logs opensandbox-server
bash scripts/docker-web.sh restart
bash scripts/docker-web.sh down
bash scripts/docker-web.sh reset          # 交互输入 RESET
bash scripts/docker-web.sh reset --yes    # 仅用于已确认的自动化清理
```

`restart` 只重启执行服务并等待恢复；`down` 删除容器和 Compose network，但保留数据库、
应用数据、Session 和 Memory named volume。`reset` 会校验运行目录中的 deployment marker，
再从数据库读取本部署拥有的精确 sandbox/volume 名称；它不会使用通配符或
`docker volume prune`。运行目录默认是 `.runtime/docker-web/`，项目名默认是
`workspace-agent-docker-web`。

健康状态为 `degraded` 时，历史 Session、消息和 Skill 元数据仍可读取，但新的 Turn
会以稳定的 `execution_unavailable`/503 拒绝。已接收并持久化后才发生的 Worker 故障
会保留 durable Turn，等待兼容 Worker 按现有恢复规则协调；API 不会回退到
`local_inline`，也不会偷偷启动第二次执行。

#### 备份与恢复

备份应在维护窗口执行。先阻止 API/Worker 继续写入，再只使用数据库记录中的精确
volume 名称：

```bash
export DOCKER_WEB_PROJECT=workspace-agent-docker-web
export DOCKER_WEB_RUNTIME_DIR="$PWD/.runtime/docker-web"
mkdir -p backup/docker-web/volumes

bash scripts/docker-web.sh down
docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  up -d postgres

docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  exec -T postgres \
  pg_dump -U workspace -d workspace -Fc > backup/docker-web/postgres.dump

docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  exec -T postgres \
  psql -U workspace -d workspace -Atqc \
  "SELECT session_volume_name FROM session_sandboxes UNION SELECT memory_volume_name FROM session_sandboxes" \
  > backup/docker-web/sandbox-volumes.txt

APP_IMAGE=$(uv run python -c \
  'from dotenv import dotenv_values; import sys; print(dotenv_values(sys.argv[1])["DOCKER_WEB_APP_IMAGE"])' \
  "$DOCKER_WEB_RUNTIME_DIR/compose.env")

archive_volume() {
  local volume=$1
  docker run --rm --read-only --network none \
    --mount "type=volume,src=$volume,dst=/source,readonly" \
    --mount "type=bind,src=$PWD/backup/docker-web/volumes,dst=/backup" \
    --entrypoint tar "$APP_IMAGE" -C /source -czf "/backup/$volume.tgz" .
}

archive_volume "${DOCKER_WEB_PROJECT}_app-data"
while IFS= read -r volume; do
  [[ "$volume" =~ ^wa-(session|memory)-[0-9a-f]{40}$ ]] || exit 1
  archive_volume "$volume"
done < backup/docker-web/sandbox-volumes.txt

docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  down
```

恢复必须进入同项目名、已停止且目标 volume 为空的新部署。先用 `up --fake` 生成受控
配置并立刻 `down`，不要覆盖仍在服务的部署：

```bash
bash scripts/docker-web.sh up --fake
bash scripts/docker-web.sh down

APP_IMAGE=$(uv run python -c \
  'from dotenv import dotenv_values; import sys; print(dotenv_values(sys.argv[1])["DOCKER_WEB_APP_IMAGE"])' \
  "$DOCKER_WEB_RUNTIME_DIR/compose.env")

# 仅允许对刚生成且已停止的空部署执行；删除的都是这个精确项目名的空 volume。
docker volume rm \
  "${DOCKER_WEB_PROJECT}_app-data" \
  "${DOCKER_WEB_PROJECT}_postgres-data" \
  "${DOCKER_WEB_PROJECT}_opensandbox-state" >/dev/null

restore_volume() {
  local volume=$1
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    printf 'restore target volume already exists: %s\n' "$volume" >&2
    return 1
  fi
  docker volume create "$volume" >/dev/null
  docker run --rm --read-only --network none \
    --mount "type=volume,src=$volume,dst=/restore" \
    --mount "type=bind,src=$PWD/backup/docker-web/volumes,dst=/backup,readonly" \
    --entrypoint tar "$APP_IMAGE" -C /restore -xzf "/backup/$volume.tgz"
}

restore_volume "${DOCKER_WEB_PROJECT}_app-data"
while IFS= read -r volume; do
  [[ "$volume" =~ ^wa-(session|memory)-[0-9a-f]{40}$ ]] || exit 1
  restore_volume "$volume"
done < backup/docker-web/sandbox-volumes.txt

docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  up -d postgres
docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  exec -T postgres \
  pg_restore -U workspace -d workspace --clean --if-exists \
  < backup/docker-web/postgres.dump
docker compose \
  --project-name "$DOCKER_WEB_PROJECT" \
  --env-file "$DOCKER_WEB_RUNTIME_DIR/compose.env" \
  -f deploy/docker-web/compose.yaml \
  down

# 真实部署使用已审核的 .env.docker.local，然后再启动
bash scripts/docker-web.sh up --claude
```

不要备份或恢复真实密钥文件到代码仓库，也不要使用 `docker volume prune`。数据库 dump、
`app-data`、所有 `wa-session-<40 hex>` 与 `wa-memory-<40 hex>` 归档必须作为同一备份集
保存；缺少其中任意一类都可能造成历史、附件、Claude resume 或个人记忆不完整。

#### Docker Web 排障

- 端口占用：修改 `.env.docker.local` 中的 `DOCKER_WEB_PORT`；Gate 专用的 PostgreSQL/
  OpenSandbox 端口也必须空闲。启动器最多等待 10 秒再报告占用变量名和端口。
- Docker 不可用：先确认 `docker info` 与 `docker compose version` 成功；启动器不会绕过
  Docker 回落到宿主机执行。
- 镜像解析失败：API 与 Runner 必须解析成本机 `sha256:<64 hex>` immutable image ID；
  可变 tag 不进入运行配置。
- PostgreSQL 不健康：查看 `logs postgres` 和 `status`，不要删除 volume 作为排障手段。
- Worker heartbeat 过期或 cohort/protocol/image 不匹配：`status` 显示 `degraded`；查看
  `logs worker`，修复兼容配置后执行 `restart`。
- OpenSandbox `/health` 不可用：查看 `logs opensandbox-server`，并确认 Docker socket 与
  `opensandbox/server:v0.2.2` 可用。
- Claude 模式变量缺失：启动只报告缺少的变量名；检查 `ANTHROPIC_BASE_URL`、
  `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`、`DOCKER_WEB_MODEL` 和
  `OPENSANDBOX_ALLOWED_HOSTS`，不要把值贴进日志。
- Credential Vault 创建、刷新或注入失败：Turn 失败关闭，不允许把真实 key 改放到
  Runner 环境、请求、命令行或 volume 中作为绕过方案。
- OpenSandbox `v0.2.2` 会把动态 Runner/egress host port 发布到
  `0.0.0.0:40000-60000`。Compose 声明的端口仍为 loopback，但这个上游限制意味着本配置
  只能用于受信任单机环境；共享或不可信 LAN 必须配置独立宿主机防火墙，或等待后续
  隔离 runtime，不能只依赖 Compose bind address。

确定性部署 Gate 不使用模型额度；真实 Gate 显式启用：

```bash
bash scripts/verify-phase-2a2.sh
RUN_LIVE_DOCKER_WEB_CLAUDE=1 bash scripts/verify-phase-2a2.sh
```

## 附件

默认单文件上限 20 MiB，每个 Turn 最多 5 个文件。

支持：

- JPEG、PNG、GIF、WebP。
- PDF、TXT、Markdown。
- CSV、JSON、YAML、XML。
- 常见 UTF-8 源代码文件。

图片以 base64 image content block 发送给 Claude。普通文件保存到 Session workspace，并将受控绝对路径作为文本块提供给 Agent。可执行文件、未知二进制、软链接、ZIP、Office 文档和视频会被拒绝。

## 测试

运行完整自动化测试：

```bash
uv run pytest -q
```

首次运行浏览器测试前安装 Chromium：

```bash
uv run playwright install chromium
```

只运行浏览器工作流：

```bash
uv run pytest tests/browser/test_workbench.py -q
```

默认测试使用 Fake Runtime，不调用真实模型。

## 真实 Claude Smoke Test

确认 `.env` 指向真实代理，并执行：

```bash
RUN_LIVE_CLAUDE_TESTS=1 uv run pytest tests/live/test_claude_smoke.py -q
```

该测试会产生真实模型调用和费用，依次验证首次 Session、精确 resume 和图片 streaming input。

## 常见问题

### 服务启动时报环境变量缺失

检查 `.env` 是否位于项目根目录，以及三个必需变量是否存在。启动错误只显示变量名，不显示值。

### Workspace 在页面显示不可用

调用：

```bash
curl http://127.0.0.1:8000/api/workspaces
```

查看脱敏后的 `validation_errors`。重点检查目录名与 `id`、YAML 和 MCP 环境变量。Skill bootstrap 的单项失败不会让 Workspace 不可用，应查看服务日志中的 bootstrap 逐项结果与汇总。

### Claude 鉴权失败

确认代理兼容 Anthropic Messages API，并检查 `ANTHROPIC_BASE_URL` 与 `ANTHROPIC_API_KEY`。浏览器无法查看这两个值。

### 历史 Session 无法恢复

不要移动单个 Session 的 `workspace/` 或 `claude-config/`。恢复依赖稳定的 Session workspace 路径、Claude transcript 和已保存的 Claude Session ID。

### 页面刷新后运行仍在继续

这是预期行为。SSE 断开不会取消 Turn；重新选择运行中的 Session 时，页面从 SQLite 重放事件并继续订阅。

## 安全边界

- 无登录，只允许本机或受认证反向代理后的内部访问。
- Workspace 工具调用经过服务端 allowlist，不使用 `bypassPermissions`。
- `Write`、`Edit` 和 `Bash` 允许 Agent 修改文件及执行终端命令，仅应在受信任 Workspace 中开启。
- `WebSearch` 和 `WebFetch` 允许 Agent 向外部搜索及网页服务发送查询或 URL，不要在查询中包含敏感数据。
- HTTP MCP 会把请求发送到配置的外部服务；stdio MCP 会执行本机受信任代码，只能挂载经过审核的入口文件。
- 当前页面展示 MCP 工具事件和文本结果，不渲染 MCP App 的交互式面板。
- `local_inline` 仍不是容器级恶意代码沙箱；不要用它处理互不信任用户的任意代码。
- `opensandbox_docker` 仅是本地工程 Gate，并不等同于生产多租户安全认证。

完整产品与技术要求见 [需求规格](docs/superpowers/specs/2026-07-12-claude-workspace-mvp-design.md)。实施任务见 [实现计划](docs/superpowers/plans/2026-07-12-claude-workspace-mvp.md)。


## 主界面 Data Agent 问答

在主界面选择“数巢问数”工作空间，新建会话后从服务端列表选择已发布的助手。
管理页的“开启问数”也直接进入该主会话。助手绑定后不在原会话中切换；切换助手请新建会话。

Claude SDK Agent 调用 `mcp__data_mcp__ask` 获取 SQL、实际查询数据和证据，再生成最终回答。
工具的 `contextMode=new` 创建新的 SQLBot 会话，`continue` 复用当前绑定；没有绑定的 SQLBot
会话时自动创建。主界面显示当前 SQLBot 会话，并允许在空闲时“重置问数上下文”。重置不删除
Claude 消息或历史查询结果。SQLBot 生成完整图表配置，由主界面复用 SQLBot 原生 G2/S2 渲染器展示；不调用 SQLBot 二次分析。

`GET/PUT /api/sessions/{session_id}/data-agent` 校验主会话所有者和工作空间。
PostgreSQL 下上下文变更与新 Turn 入队锁定相同 Session 行，运行期间拒绝重置。
同一进程的问数请求按主会话串行执行。

Docker 的 OpenSandbox Runner 通过已有控制通道发送 `data.ask.request`；Worker 从 Turn 所属
Session/User 解析身份，调用 Host DataAgentService，并回传 SQL 和结果。数据库凭据、SQLBot
票据及内部提示词日志不传入 Runner。更新这条链路时必须同时重建并配置 App 和 Runner 镜像。

数据流出现 `sql-data=execute-success` 和 `finish` 后，Host 直接读取对应记录的数据；不再为确认结果读取整个 SQLBot 聊天，避免多轮问数额外消耗授权回调次数。旧版缺少完成信号时仍保留聊天详情校验。Docker 部署保留被会话引用的 Runner 镜像标签，避免滚动构建破坏旧沙箱的状态查询。

### SQLBot 原生图表

问数请求启用 `generate_chart=true`，完整保存 SQLBot 返回的标题、轴字段、系列和多指标配置。
主界面和 Data Agent 页面共用 `web/sqlbot-chart/main.js`，支持柱状图、条形图、折线图、饼图和 S2 表格。
图表使用已缓存的查询结果；截断时显示实际绘制行数。历史记录如果仅保存图表类型，则回退数据表并提示重新问数，不猜测字段映射。
历史展示读取已持久化的查询快照，仍校验助手和结果归属，不受短期结果 TTL 影响；TTL 仅限制后续分析、预测和推荐操作，避免历史图表在刷新后消失。查询快照不代表实时数据。

`web/sqlbot-chart/vendor/` 保留 SQLBot 原始图表实现和许可证，`SOURCE.json` 记录源文件摘要、提交、部署 bundle 摘要和依赖版本。
构建会验证原始文件及依赖版本，禁止直接修改 vendor 算法来消除差异。升级 SQLBot 时应同步该来源清单及源码，并重新做配置对照和浏览器验收。

```sh
npm ci
npm run build:sqlbot-charts
node --test tests/js/test_sqlbot_charts.cjs tests/js/data-agent-chat.cjs
# 参数须为当前 SQLBot 部署的实际前端 bundle；摘要必须与 SOURCE.json 匹配。
node scripts/verify-sqlbot-charts.cjs /path/to/sqlbot-deployed-index.js
```

对照脚本从部署 bundle 中提取图表实现，与本项目生成的 G2/S2 配置比较，覆盖 5 种类型共 30 个场景。
这证明配置转换一致，不替代真实浏览器的视觉验收，也不保证未来 SQLBot 升级后自动一致。
构建产物位于 `app/web/static/sqlbot-charts*`，部署时需一起更新；本次恢复同时修改了 Runner 提示词，因此需更新 App 和 Runner 镜像。

### 数巢问数的 MCP 取数开关

会话顶部“取数方式”可在 SQLBot（原流程）和 MCP 工具之间切换。MCP 模式直接使用公司 `table.search / table.describe / table.query`，不要求 SQLBot 助手，也不经 Service-2 取数。服务端配置、独立登录缓存、上下文切换行为和容器运行条件见 [MCP 取数说明](docs/data-question-mcp.md)。
