# Davinci Mock × AG-UI × Claude Agent SDK MVP 验证设计

## 文档状态

- 日期：2026-08-06
- 分支：`codex/davinci-iframe-mvp-validation`
- 目标运行模式：`local_inline`
- 设计状态：用户已批准架构、Mock 页面、协议、错误处理和验收 Gate
- 事实基线：当前项目使用 `claude-agent-sdk==0.2.128`、FastAPI、Session/Turn 持久化和 SSE；现有 Workspace Agent 页面与 API 必须保持兼容

## 目标

构建一个可在本机运行的最小集成验证，证明以下架构链路可行：

1. Mock Davinci 页面通过悬浮按钮打开独立 Origin 的 Agent iframe。
2. 用户在 iframe 中创建 Session，并通过当前 Claude Agent SDK Runtime 进行真实模型对话。
3. Claude 自主调用 AG-UI frontend-defined tool `dashboard.capture_current_view`。
4. iframe 通过受控 `postMessage` 请求 Mock Davinci 采集当前筛选器和 Widget 数据。
5. Tool Result 返回后，Claude 在同一个用户可见 Turn 中继续推理并生成仪表盘解读。
6. Claude 调用 `navigateTo` 后，Mock Davinci 切换到数据集页面，并通过 `UI_ACK` 确认结果。
7. 页面切换不卸载 iframe；Session、历史消息和 `threadId` 保持不变。

本验证回答的是“当前仪表盘前端运行态能否通过标准 Agent-UI 边界交给 Claude 解读”，不验证真实 Davinci 生产接口或部署拓扑。

## 非目标

- 不接入真实 Davinci 前端或后端。
- 不实现 Artifact Store；Mock Snapshot 以受限的小型 JSON 直接作为 Tool Result。
- 不生成、修改、保存或发布仪表盘配置。
- 不实现 `dashboard.apply_patch`、草稿、版本冲突或多人协作。
- 不覆盖 AG-UI 的全部事件、状态同步、Generative UI、Handoff 或 Interrupt 能力。
- 不验证 Docker Web、OpenSandbox 或 Kubernetes Runner 的跨进程 Bridge。
- 不替换现有 Workspace Agent 根页面和 Session/Turn API。

## 已确认的设计决策

- 使用双服务、双 Origin：Mock Davinci 为 `http://127.0.0.1:4173`，Agent Host/iframe 为 `http://127.0.0.1:8000`。
- iframe 使用官方 `@ag-ui/client` 的 `HttpAgent`；Python 服务使用官方 `ag-ui-protocol` 类型和事件编码器。
- Agent iframe 与 Agent Host 之间使用 AG-UI `RunAgentInput -> BaseEvent` HTTP SSE 边界。
- iframe 与 Mock Davinci 父页面之间使用项目自定义、强校验的 `postMessage` 宿主协议。
- 仪表盘读取属于 Capability，页面跳转属于 UI Command，二者不混用。
- iframe 挂在 Mock Davinci 应用根 Shell，不挂在 Dashboard 或 Dataset 路由组件下。
- Tool Result 通过包含标准 `ToolMessage` 的 AG-UI 输入回传，并用真实 `toolCallId` 关联。
- `RUN_FINISHED` 只在 Claude 生成最终回答后发送；前端工具等待期间保持原 Run 活跃。
- MVP 不依赖 Python SDK 尚未正式文档化的 deferred-tool 恢复行为，使用进程内 Pending Bridge 完成同 Run 等待。

## 总体架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Mock Davinci Host · 127.0.0.1:4173                           │
│ App Shell                                                    │
│ ├─ /dashboard/1024：筛选器、KPI、Widgets                     │
│ ├─ /datasets：Mock 数据集列表                                │
│ ├─ DavinciBridge：Capability / UI Command 执行器              │
│ └─ 常驻悬浮按钮与 Agent iframe                               │
└──────────────────────────────┬───────────────────────────────┘
                               │ 自定义 postMessage
                               │ HOST_CONTEXT / REQUEST / RESULT / ACK
┌──────────────────────────────▼───────────────────────────────┐
│ Agent iframe · 127.0.0.1:8000/embed                          │
│ ├─ Session 创建与聊天 UI                                     │
│ ├─ @ag-ui/client HttpAgent                                   │
│ ├─ frontend-defined tools                                    │
│ └─ Davinci Host Bridge                                       │
└──────────────────────────────┬───────────────────────────────┘
                               │ AG-UI HTTP POST + SSE
                               │ RunAgentInput / BaseEvent
┌──────────────────────────────▼───────────────────────────────┐
│ Current FastAPI Agent Host · local_inline                    │
│ ├─ POST /api/ag-ui                                           │
│ ├─ AG-UI Protocol Adapter                                    │
│ ├─ Session / Turn / Event persistence                        │
│ ├─ FrontendToolBridge：toolCallId -> Pending Future           │
│ └─ ClaudeSDKClient + in-process davinci-ui MCP tools          │
└──────────────────────────────────────────────────────────────┘
```

## 组件职责

### Mock Davinci App Shell

负责路由、Mock 业务状态、悬浮入口和 iframe 生命周期。路由切换只替换业务内容区，不能销毁 Agent iframe。Shell 保存当前 `HostContext`，任何路由、筛选器或资源变化都递增 `contextVersion` 并主动发送 `HOST_CONTEXT`。

### Mock Dashboard

提供可操作的筛选器和稳定的 Mock 数据：

- 区域：南区
- 时间：最近 7 天
- 物品量：`4,734`
- 周同比：`-10.88%`
- 竞得金额：`40,388,380`
- Widget：物品量趋势、品类构成、核心指标明细

数据由页面内 Store 持有。`captureCurrentView()` 必须读取当前 Store，而不是返回脱离页面状态的固定提示词。

### Mock Dataset Page

展示三个 Mock 数据集及字段数。该页面不支持 `dashboard.capture_current_view`，仅支持通用导航；用来验证页面能力协商和 iframe 在路由切换后的持续存在。

### Agent iframe

提供紧凑聊天界面、新建 Session、历史消息、流式回复和 Tool 状态。iframe 是独立的小型前端 Bundle，不重写现有 Workspace Agent 页面。首个用户 Run 使用 `HttpAgent` 调用 `/api/ag-ui`，并把当前父页面声明的前端工具放入 `RunAgentInput.tools`。

### AG-UI Protocol Adapter

复用当前 Session、Turn、Runtime 和事件持久化能力，负责：

- 校验标准 `RunAgentInput`。
- 映射 `threadId -> Session ID`、`runId -> Turn ID`。
- 将 AG-UI UserMessage 转换成当前 `RuntimeRequest`。
- 将当前 RuntimeEvent 转成标准生命周期和文本事件。
- 将 Claude `ToolUseBlock` 与 `PreToolUse` 中的真实 `tool_use_id` 转成标准 `TOOL_CALL_*`。
- 将包含 ToolMessage 的 continuation 输入交给 `FrontendToolBridge`。
- 使用官方 `EventEncoder` 输出 SSE。

### FrontendToolBridge

每个运行中的 AG-UI Run 拥有独立 Bridge，保存：

```text
(threadId, runId, toolCallId)
  -> tool name
  -> validated args
  -> pending Future
  -> expiry
  -> completion state
```

只允许一个结果完成 Future。完全相同的重复结果幂等成功；不同内容的第二次提交返回冲突。Turn 取消、Session 删除、iframe 关闭或超时都会完成 Future 为结构化错误并释放资源。

### Claude Agent SDK Adapter

为本次 Run 动态增加进程内 `davinci-ui` MCP Server，包含两个工具。`PreToolUse` Hook 已能取得真实 `tool_use_id`，用于建立 AG-UI Tool Call 关联；Tool Handler 等待 Bridge Future，收到结果后返回 MCP Tool Result，Claude Agent Loop 自动继续。

## AG-UI 协议范围

### 输入

`POST /api/ag-ui` 接收：

```json
{
  "threadId": "session-id",
  "runId": "turn-id",
  "state": {
    "hostContext": {
      "pageType": "dashboard",
      "resourceId": "1024",
      "contextVersion": 3
    }
  },
  "messages": [],
  "tools": [],
  "context": [],
  "forwardedProps": {
    "workspaceId": "example",
    "profile": "davinci-mvp-v1"
  }
}
```

`threadId` 必须属于当前用户；`runId` 必须属于该 Session 或为新建 Run。`forwardedProps` 不能携带身份令牌或模型凭据。

### 输出事件

MVP 必须输出：

- `RUN_STARTED`
- `TEXT_MESSAGE_START`
- `TEXT_MESSAGE_CONTENT`
- `TEXT_MESSAGE_END`
- `TOOL_CALL_START`
- `TOOL_CALL_ARGS`
- `TOOL_CALL_END`
- `RUN_FINISHED`
- `RUN_ERROR`
- `CUSTOM`，仅用于已经证明无法用上述标准事件表达的非模型进度摘要

原有 `turn.progress` 不直接暴露为新的自定义协议；如需呈现，编码成命名明确的 `CUSTOM` 事件。原有历史与服务状态 API 保留。

### Tool Result continuation

父页面完成能力后，iframe 向同一 `/api/ag-ui` 提交一个 `RunAgentInput`，其 `messages` 最后一项为标准 ToolMessage：

```json
{
  "id": "tool-result-1",
  "role": "tool",
  "content": "{\"schemaVersion\":\"mock-dashboard-snapshot-v1\",\"widgetCount\":3}",
  "toolCallId": "tool-use-id"
}
```

Adapter 识别该输入为活跃 Run 的 Tool Result continuation，根据 `threadId/runId/toolCallId` 解除 Pending Future。提交请求本身返回一个短 `CUSTOM` acknowledgement stream；原 Run 的 SSE 继续输出 Claude 后续文本，最终只发送一次 `RUN_FINISHED`。

首个 Run 由官方 `HttpAgent` 管理其持续中的 Observable；Tool Result 由 iframe 内部的轻量 `ToolResultSubmitter` 以合法 `RunAgentInput` 单独提交并消费 acknowledgement，不能替换或关闭首个 Run 的 SSE 连接。两类请求共用同一端点、`threadId` 和 `runId`，但各自拥有独立 HTTP 响应流。

该 MVP 使用标准 AG-UI 请求、消息和事件类型，但不声明实现了未列入本设计的完整协议能力或第三方 conformance certification。

## Frontend Tools

### `dashboard.capture_current_view`

```json
{
  "name": "dashboard.capture_current_view",
  "description": "Capture the dashboard exactly as the user currently sees it.",
  "parameters": {
    "type": "object",
    "properties": {},
    "additionalProperties": false
  }
}
```

只有 `pageType=dashboard` 时注册。父页面返回的 Snapshot 最大 64 KiB，结构为：

```json
{
  "schemaVersion": "mock-dashboard-snapshot-v1",
  "page": {
    "pageType": "dashboard",
    "dashboardId": "1024",
    "title": "南区经营仪表盘",
    "contextVersion": 3,
    "capturedAt": "2026-08-06T16:00:00+08:00"
  },
  "filters": [
    {"field": "区域", "operator": "eq", "value": "南区"},
    {"field": "时间", "operator": "relative", "value": "最近7天"}
  ],
  "metrics": {
    "itemCount": 4734,
    "weeklyChangePct": -10.88,
    "bidAmount": 40388380
  },
  "widgets": []
}
```

### `navigateTo`

```json
{
  "name": "navigateTo",
  "description": "Navigate the Davinci host to an allowed application view.",
  "parameters": {
    "type": "object",
    "properties": {
      "destination": {"type": "string", "enum": ["dashboard", "datasets"]},
      "resourceId": {"type": "string"}
    },
    "required": ["destination"],
    "additionalProperties": false
  }
}
```

`destination=dashboard` 时 `resourceId` 必须为 `1024`；`datasets` 不需要资源 ID。页面完成导航并更新 HostContext 后才能返回成功 ACK。

## Davinci 宿主协议

消息类型限定为：

- `HOST_CONTEXT`
- `DAVINCI_CAPABILITY_REQUEST`
- `DAVINCI_CAPABILITY_RESULT`
- `UI_COMMAND`
- `UI_ACK`

除 `HOST_CONTEXT` 外，每条消息都包含：

```text
protocolVersion
messageId
requestId
toolCallId
nonce
issuedAt
expiresAt
contextVersion
payload
```

`HOST_CONTEXT` 包含当前路由、资源、`contextVersion`、`supportedCapabilities` 和 `supportedCommands`。iframe 只能调用父页面当前声明的工具。

`postMessage` 必须使用精确 `targetOrigin`。接收方同时校验 `event.origin`、`event.source`、`nonce`、协议版本、过期时间和字段 Schema，不允许使用 `"*"`。

## 核心时序

### 解读当前仪表盘

```text
User -> iframe: 解读当前仪表盘
iframe -> /api/ag-ui: RunAgentInput(UserMessage + frontend tools)
/api/ag-ui -> iframe: RUN_STARTED
Claude -> davinci-ui MCP: dashboard.capture_current_view
Adapter -> iframe: TOOL_CALL_START / ARGS / END
iframe -> parent: DAVINCI_CAPABILITY_REQUEST
parent -> parent store: captureCurrentView()
parent -> iframe: DAVINCI_CAPABILITY_RESULT(snapshot)
iframe -> /api/ag-ui: RunAgentInput(... ToolMessage ...)
Adapter -> pending MCP handler: resolve(snapshot)
MCP handler -> Claude: Tool Result
Claude -> iframe: TEXT_MESSAGE_* with dashboard interpretation
/api/ag-ui -> iframe: RUN_FINISHED
```

最终回答必须来自 Tool Result，不能在没有成功 Snapshot 的情况下猜测 Mock 指标。

### 打开数据集页面

```text
User -> iframe: 打开数据集页面
Claude -> davinci-ui MCP: navigateTo({destination: "datasets"})
Adapter -> iframe: TOOL_CALL_*
iframe -> parent: UI_COMMAND
parent: route /dashboard/1024 -> /datasets
parent -> iframe: HOST_CONTEXT(contextVersion + 1)
parent -> iframe: UI_ACK(executed)
iframe -> Adapter: ToolMessage
Claude -> iframe: 已打开数据集页面
```

回复“已打开”必须发生在成功 `UI_ACK` 之后。

## Session 与页面生命周期

- iframe 首次打开时创建或选择 Session。
- 关闭悬浮抽屉只隐藏 iframe，不销毁 DOM；重新打开继续原 Session。
- 仅收起抽屉不会触发 `IFRAME_CLOSED`；只有 iframe DOM 被卸载、页面离开或 Bridge 显式断开时，才取消等待中的 Tool Call。
- Dashboard 与 Dataset 路由切换不能重新创建 iframe。
- 浏览器整页刷新后允许 iframe 重载，但必须通过现有 Session API恢复历史。
- AG-UI `threadId` 等于 Session ID；一个用户可见 Turn 使用一个 `runId`。
- 同一 Session 同时只允许一个活跃 Run，沿用当前串行约束。

## 错误处理

| 错误码 | 触发条件 | 行为 |
| --- | --- | --- |
| `ORIGIN_REJECTED` | Origin、source 或 nonce 不匹配 | 丢弃消息并记录无载荷安全日志 |
| `CONTEXT_STALE` | 请求版本不是当前 `contextVersion` | 返回 ToolMessage error，不采集或导航 |
| `CAPABILITY_UNAVAILABLE` | 当前页面没有声明该能力 | 返回 ToolMessage error |
| `TARGET_NOT_FOUND` | 目标路由或资源不存在 | 保持当前页面并返回失败 ACK |
| `TOOL_TIMEOUT` | 15 秒未收到结果 | 结束 Future，Claude 得到结构化错误 |
| `SESSION_MISMATCH` | Tool Result 不属于当前用户/Session/Run | 拒绝并记录审计事件 |
| `TOOL_RESULT_CONFLICT` | 同一 Tool Call 第二次提交不同内容 | 返回 409；不重复恢复 Claude |
| `IFRAME_CLOSED` | iframe 在工具等待期间被销毁 | 取消该 Tool Call；Turn 以错误结束 |
| `RUN_ERROR` | Runtime、协议或模型失败 | 输出标准 `RUN_ERROR`，不泄露堆栈或密钥 |

完全相同的 Tool Result 重放返回幂等成功。Run 取消时立即清理所有 Pending Future；清理后到达的结果返回过期错误。

## 安全边界

- 模型 Base URL、API Key 和 Runtime 凭据仅存在于 Agent Host 进程。
- Mock Davinci、iframe JavaScript、AG-UI 输入、SSE、postMessage、数据库事件和日志不得出现模型凭据。
- Snapshot 为当前用户页面数据，只绑定当前 Session/Run；MVP 不跨用户共享。
- Tool Schema 由当前 HostContext 动态裁剪，不能让模型调用页面未声明能力。
- Snapshot 最大 64 KiB，字段使用固定 Schema，不接收任意 HTML、脚本或可执行表达式。
- 所有外部字符串用文本方式渲染，不写入 `innerHTML`。
- Mock 环境只绑定 loopback 地址。

## 实现边界建议

后续实施计划应围绕以下独立单元展开：

```text
app/agui/
  models.py          # 官方类型的应用边界与校验
  adapter.py         # RuntimeEvent -> BaseEvent
  routes.py          # POST /api/ag-ui
  frontend_tools.py  # davinci-ui MCP tools + Pending Bridge

app/web/templates/embed.html
app/web/static/embed-*.js/css  # 独立 Bundle，包含 HttpAgent 与 ToolResultSubmitter

demo/davinci-mock/
  index.html
  app.js
  styles.css

tests/
  test_agui_adapter.py
  test_frontend_tool_bridge.py
  browser/test_davinci_agui_mvp.py
  live/test_davinci_agui_claude.py
```

现有根页面继续使用当前 API，避免把验证性 iframe UI 强行耦合到工作台页面。
实施时必须把 `@ag-ui/client` 和 `ag-ui-protocol` 的实际版本写入现有依赖清单与锁文件，并在实施计划中记录选定版本；不能依赖未锁定的全局包。

## 测试设计

### 单元与 API 测试

- `RunAgentInput` 所有必填字段和 Session 所有权校验。
- RuntimeEvent 到标准生命周期、文本、工具和错误事件的映射。
- `PreToolUse` 的真实 `tool_use_id` 与 ToolMessage 关联。
- Pending Future 成功、超时、取消、完全相同重放和冲突重放。
- HostContext 版本递增和动态 Tool 列表。
- Snapshot Schema、64 KiB 上限及非法载荷拒绝。
- `RUN_FINISHED` 只发送一次且晚于最终文本。

### 浏览器测试

- 双 Origin iframe 能加载，错误 Origin 消息被忽略。
- 悬浮按钮打开、关闭时 iframe DOM 和 Session ID 不变化。
- Dashboard -> Dataset -> Dashboard 切换不重载 iframe。
- 调整筛选器后 `contextVersion` 增加。
- Dashboard 有两个前端工具，Dataset 只有 `navigateTo`。
- Tool 状态、最终文本、错误和超时均可见。

### 真实模型 Gate

使用当前配置模型执行：

1. 用户输入“解读当前仪表盘”。
2. 事件序列必须先出现 `TOOL_CALL_*`，再出现最终 `TEXT_MESSAGE_*`。
3. 回答必须引用 `4,734`、`-10.88%` 和 `40,388,380` 中的事实，不得改写为其他值。
4. 用户输入“打开数据集页面”。
5. 必须调用 `navigateTo`，页面路径变为 `/datasets`。
6. 成功 `UI_ACK` 后 Claude 才回复完成。
7. 两次交互使用同一 Session/threadId，历史连续。

### 回归 Gate

- 当前全部单元、API 和浏览器测试通过。
- 原 Workspace Agent 根页面仍能创建 Session、流式回复、恢复历史和取消 Turn。
- AG-UI 新依赖不改变 Docker Web 默认启动路径；本设计只要求 `local_inline` 验证。
- 浏览器网络记录、Mock Host 日志、API 日志、数据库事件中检索不到模型 Key。

## 验收标准

只有同时满足以下条件才能宣称 MVP 架构验证成功：

1. 真实 Claude Agent SDK Run 自主调用前端工具。
2. 仪表盘数据来自当前 Mock 页面 Store，而不是提示词硬编码。
3. Tool Result 通过 AG-UI ToolMessage 返回，并恢复原 Agent Loop。
4. Claude 基于 Snapshot 生成可见解读。
5. `navigateTo` 经 UI Command 和 ACK 改变父页面路由。
6. iframe 和 Session 在路由切换期间保持。
7. 协议错误、超时、重放和 Origin 检查有自动化证据。
8. 原 Workspace Agent 回归 Gate 通过。
9. 没有模型凭据越过 Agent Host 边界。

本 Gate 通过只证明单机 `local_inline` 架构链路可行。迁移到 Docker/OpenSandbox 时，需要把进程内 Pending Bridge 替换成 Runner 可访问、具备租约和恢复语义的跨进程 Bridge，并单独完成部署 Gate。

## 参考

- AG-UI Core Architecture: <https://docs.ag-ui.com/concepts/architecture>
- AG-UI Tools: <https://docs.ag-ui.com/concepts/tools>
- AG-UI JavaScript Core Types: <https://docs.ag-ui.com/sdk/js/core/types>
- AG-UI Python SDK Overview: <https://docs.ag-ui.com/sdk/python/core/overview>
- AG-UI Python Event Encoder: <https://docs.ag-ui.com/sdk/python/encoder/overview>
- Claude Agent SDK Sessions: <https://code.claude.com/docs/en/agent-sdk/sessions>
- Claude Agent SDK Hooks: <https://code.claude.com/docs/en/agent-sdk/hooks>
