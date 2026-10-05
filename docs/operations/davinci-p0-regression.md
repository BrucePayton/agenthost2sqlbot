# Davinci Agent P0 本地回归记录

日期：2026-08-17

## 结论

P0 三仓实现和静态回归已经完成，本地三个服务均可启动并通过健康检查。真实页面三条业务用例尚未完成最终验收，阻塞原因是本机访问 UAT Davinci 超时（`ETIMEDOUT`），不是本地 Host、Data MCP 或前端服务启动失败。

在 UAT 恢复可达之前，不将真实会话、Davinci API 写回或最终业务 Gate 标记为通过。

## 代码基线

| 仓库 | 分支 | P0 实现提交（不含本记录） |
| --- | --- | --- |
| Davinci 前端 | `codex/davinci-agent-mvp-embed` | `4e0ab275a` |
| Agent Host | `codex/local-data-mcp-stack` | `dcf87f8` |
| Data MCP | `codex/local-stack-listener` | `acc3a00` |

## 已完成能力

- 新增原子工具 `dashboard.apply_widget_spec`：支持创建或更新组件、`dryRun`、数据集/指标/维度/筛选/排序/TopN 配置，并在一次前端事务中编译、校验、持久化和读回。
- 前端数据集选项改为按需加载，避免把“当前未加载”错误解释成“数据集不可用”。
- Host 增加 operation ledger、UI 写工具串行化、重复调用拦截、工具变化提示和 Stop 阶段读回校验。
- 新增 `davinci-dashboard` Workspace 与 `configure-dashboard-widget` Skill，约束 Agent 使用“解析需求 → 原子写入 → 读回验证”的最短路径。
- Data MCP 增加结果裁剪、多词回退、用户指定数据集优先和局部 schema 失败降级。
- 本地 Host 使用模型 `qwen3.8-max`；Data MCP 意图解析沿用同一模型配置。

## 自动化验证

| 范围 | 结果 |
| --- | --- |
| Agent Host 全量测试 | `744 passed, 25 skipped` |
| DashboardV2 Agent 前端测试 | `25 suites / 146 tests passed` |
| 前端工具注册与生成产物 Jest | `2 suites / 11 tests passed` |
| 前端工具合约同步 Node tests | `4 passed` |
| 前端工具合约同步检查 | V1：`public=56`、`frontend=51`、`internal=4`；V2：`public=67`、`frontend=63`、`internal=4` |
| Data MCP 全量测试 | `234 passed, 3 skipped` |
| Host SQLite `quick_check` | `ok` |

说明：Davinci 全量 TypeScript 编译仍受仓库既有旧版 TypeScript/依赖问题影响，本次以变更相关的 Jest 套件和生成物一致性检查作为前端 Gate。

## 本地运行状态

| 服务 | 地址 | launchctl label |
| --- | --- | --- |
| Davinci Data MCP | `http://127.0.0.1:18000` | `com.aihuishou.davinci.local.data-mcp.18000` |
| Agent Host | `http://127.0.0.1:18001` | `com.aihuishou.davinci.local.host.18001` |
| Davinci 前端 | `http://local.aihuishou.com:15002` | `com.aihuishou.davinci.local.web.15002` |

运行日志：

- `/tmp/davinci-data-mcp-18000-p0.log`
- `/tmp/davinci-agent-host-18001-p0.log`
- `/tmp/davinci-web-15002-p0.log`

## Workspace 迁移

本地 18001 的 personal Workspace 已由 `example` 切换为 `davinci-dashboard`。旧的 6 个会话快照不再作为本次回归依据，真实回归必须新建 Session。

迁移前数据库备份：

`/Users/a110356/work/code/claude_workspace_mvp/.worktrees/local-data-mcp-stack/.local-data-18001/app.db.pre-davinci-dashboard-p0`

## 旧会话基线复现

对问题会话 `49f72f9d-db22-4b9b-a01f-4a897dbc20fc` 运行 audit，成功复现改造前的异常：4 个孤儿 `tool_use`、`page.get_context` 和 `dashboard.get_structure` 各重复 4 次，并调用了 `Bash`、`ListMcpResourcesTool`、`Agent` 三类禁用工具。该结果用于证明审计器能识别历史问题，不代表 P0 新实现的真实页面 Gate 已通过。

## 真实页面 Gate

### 待执行用例

1. `帮我查找近30天各城市成交金额需要使用的数据集、指标和维度。`
   - 期望：Data MCP 解析/目录检索/权限校验；不调用 UI 写工具。
2. `添加一个指标卡，近30天 O2O 奢侈品成交额。`
   - 期望：解析数据需求后只调用一次 `dashboard.apply_widget_spec` 完成创建和配置，再读回验证；不得先创建空卡。
3. `奢侈品门店成交订单金额，按照城市分组，增加一个柱状图，最近30天。`
   - 期望：解析数据需求后只调用一次 `dashboard.apply_widget_spec`，同时配置数据集、横轴、指标和日期筛选，再读回验证。

每条用例都必须检查：

- Session audit 无孤儿 `tool_use`、无重复写调用、无被禁用的低层工具绕行。
- 页面不存在新增空组件或半配置组件。
- `dashboard.get_widget_config` / `dashboard.get_widget_data` 读回与用户需求一致。
- Davinci 后端 API 二次读取确认组件已真实持久化，而非只有前端草稿态。

### 当前阻塞证据

- 前端代理访问 `https://abdavinci-uat-up.aihuishou.com` 报 `ETIMEDOUT`。
- 本机直接请求 UAT 根路径和 `/api/v3/users/currentUser` 均在约 5 秒后超时，HTTP 状态为 `000`。
- 因 UAT 不可达，浏览器无法获得登录上下文，也无法新建三个真实 Session 或核对后端写回。

### UAT 恢复后的重试步骤

1. 确认 `https://abdavinci-uat-up.aihuishou.com/api/v3/users/currentUser` 可达。
2. 打开 `http://local.aihuishou.com:15002/share.html#/share/workbench-new`，确认 Agent 显示“就绪”。
3. 为上述每条用例新建独立 Session，并保存 Session ID。
4. 执行 Host 的 `scripts/audit_davinci_session.py`，保存三份 audit 结果。
5. 从页面和 Davinci API 两侧核对新组件的数据集、字段、筛选和持久化状态。
6. 只有三条用例和 audit 全部通过后，才把 P0 真实页面 Gate 标记为 PASS。
