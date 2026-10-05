# Turn 可观测性与断线恢复设计

## 目标

让用户在页面内持续知道当前 Turn 是否仍在运行、正在等待哪个边界、最近一次活动发生在何时，以及后端是否已经断开。页面展示可审计的执行进度摘要，不展示 Claude 的原始隐藏思维链。

本设计采用后端显式进度事件方案：运行时、Turn 服务和前端通过已有的持久化事件与 SSE 链路传递状态；SSE 心跳和一次性状态查询用于区分“模型暂时没有输出”“事件流正在重连”和“后端已经退出”。

## 已确认的产品决策

- 进度主要显示在页面内，不以终端日志作为用户入口。
- 工具名称、状态和耗时始终可见。
- 工具输入与输出默认折叠，用户点击后展开。
- “思考过程”使用事件驱动的简短进度摘要表达，不展示或持久化 `ThinkingBlock`、`thinking_delta`、签名或其他隐藏推理内容。
- 30 秒没有文本或工具等业务事件时，页面明确显示“仍在运行，等待模型响应”。
- 后端断开时页面必须停止显示虚假的“就绪”或无期限 `running`。
- 本次不安装 `launchd`、systemd 或其他系统级常驻服务；服务进程必须从长期存在的终端或外部进程管理器启动。

## 当前问题与根因

当前 UI 只处理业务事件，忽略 SSE `heartbeat`，也没有注册 `EventSource.onerror`。顶部“就绪”状态是静态文本，后台每五秒刷新 Session 失败时会静默吞掉错误。因此后端退出后，页面仍保留旧的 `running` 状态。

发生问题的 Turn 只持久化了 `message.user` 和 `turn.started`。后端在进入 Claude 运行时后退出，既没有机会写入 `turn.failed`，浏览器也没有把 SSE 断线转成可见状态。服务下一次启动虽然会把数据库中的活动 Turn 标记为 `interrupted`，但当前实现不会为历史时间线追加 `turn.interrupted` 事件。

## 事件模型

### 持久化进度事件

新增 `turn.progress`，通过现有 `MessageRecord`、`EventBroker` 和 SSE 链路持久化及广播。载荷为：

```json
{
  "phase": "connecting_mcp",
  "message": "正在连接 5 个 MCP 服务",
  "occurred_at": "2026-07-13T01:43:43.000000+00:00"
}
```

允许的 `phase` 使用固定集合：

| phase | 触发边界 | 页面文案示例 |
| --- | --- | --- |
| `preparing` | Turn 开始构建运行请求 | 正在准备 Session 环境 |
| `connecting_runtime` | 调用 Claude SDK `connect()` 前 | 正在连接模型运行时 |
| `connecting_mcp` | 等待 MCP readiness 前 | 正在连接 5 个 MCP 服务 |
| `mcp_ready` | 所有 MCP 已连接 | MCP 服务已就绪 |
| `waiting_model` | 用户消息提交给 SDK 后 | 已提交请求，等待模型响应 |
| `generating` | 收到首个文本增量 | 模型正在生成回复 |
| `finalizing` | 收到运行结果、写入终态前 | 正在保存执行结果 |

进度摘要只陈述代码能够证明的状态，不根据用户问题猜测“正在生成 SQL”或“正在整理 HTML”。更具体的可审计动作由 `tool.started` 和 `tool.completed` 表达。

`turn.progress` 只在阶段变化时写库，不为每个轮询或心跳写库，避免事件膨胀。

### 心跳与静默等待

现有 SSE `heartbeat` 保持非持久化，每 15 秒发送一次。前端收到后更新“最后心跳”，但不在时间线新增一行。

前端维护两个时间：

- `lastTransportActivityAt`：任意 SSE 事件或 heartbeat 的到达时间，用于判断后端连接是否活跃。
- `lastBusinessActivityAt`：文本、工具或进度事件的到达时间，用于判断模型是否处于静默等待。

Turn 运行期间若 30 秒没有业务事件但心跳正常，执行面板显示“仍在运行，等待模型响应”，累计耗时继续更新。

### 工具事件

保留现有 `tool.started` 和 `tool.completed`，补充可靠时间字段：

- `tool.started`: `started_at`
- `tool.completed`: `completed_at`、`duration_ms`

运行时按 `tool_use_id` 记录开始时间。找不到开始事件时仍显示结果，耗时显示为未知，不伪造 `0 ms`。

工具卡片默认关闭。摘要行显示状态、工具名和耗时；展开区显示已经过现有 `preview()` 截断的输入或输出。错误结果使用失败样式，但不会自动展开。

### 服务重启中断事件

启动恢复在把遗留 Turn 更新为 `interrupted` 时，同时为每个 Turn 追加一条 `turn.interrupted` 消息事件：

```json
{
  "code": "service_restarted",
  "message": "服务在该 Turn 执行期间退出，任务已中断。",
  "interrupted_at": "2026-07-13T01:50:00.000000+00:00"
}
```

`turn.interrupted` 是终态事件。刷新历史后页面展示明确错误并允许用户重新发送，不把旧 Turn 当作仍在运行。

## 页面设计

### 执行过程面板

消息时间线底部新增一张紧凑的执行面板，只在当前 Session 存在活动 Turn 或历史记录中包含进度事件时显示。面板包括：

- 当前阶段摘要。
- 累计执行时间。
- 最后心跳相对时间。
- 后端连接状态：`已连接`、`重连中`、`已断开`。
- 按时间排列的阶段记录与工具卡片。

面板不复制助手正文。历史加载时只渲染已持久化的阶段和工具事件；heartbeat 只影响当前活动 Turn。

### SSE 断线处理

`EventSource.onerror` 发生时：

1. 面板立即切换为“事件连接中断，正在确认 Turn 状态”。
2. 前端对 `/api/turns/{turn_id}` 发起一次状态查询。
3. 若查询成功且 Turn 仍活动，保留 `EventSource` 自动重连并显示“重连中”。
4. 若查询返回终态，按返回状态结束当前 UI 状态并刷新历史。
5. 若查询也失败，顶部服务状态改为“已断开”，面板显示最后心跳时间，停止无期限运行动画。

后端不可达时输入区保持禁用，因为新的发送请求必然失败。现有五秒刷新改为更新全局服务状态，不再静默吞掉错误。健康检查恢复后重新拉取 Session；启动恢复产生的 `interrupted` 状态将使输入区重新可用。

### 顶部服务状态

现有静态“就绪”改为由 `/api/health` 和 SSE 活动驱动：

- API 健康且没有断线：`就绪`
- SSE 暂时错误但 API 可达：`重连中`
- API 不可达：`已断开`

状态同时使用文字和颜色，满足不能只依赖颜色表达的现有可访问性约束。

## 后端数据流

```text
TurnService._mark_running
  -> persist turn.started
  -> persist turn.progress(preparing)
  -> ClaudeAgentRuntime.run
       -> turn.progress(connecting_runtime)
       -> SDK connect
       -> turn.progress(connecting_mcp)
       -> MCP readiness
       -> turn.progress(mcp_ready)
       -> query
       -> turn.progress(waiting_model)
       -> text/tool events
       -> turn.progress(generating/finalizing)
  -> terminal event
  -> EventBroker
  -> SSE
  -> page execution panel
```

所有持久化事件沿用 Turn 内单调递增的 `sequence`，页面继续使用 `Last-Event-ID` 去重和重放。heartbeat 没有持久化序号，不参与历史恢复。

## 错误处理

- MCP readiness 失败：最后一条进度保留在“正在连接 MCP”，随后现有 `turn.failed` 显示具体服务器名称。
- 模型长时间无输出：heartbeat 正常时显示静默等待，而不是报错；仍由现有 Turn 总超时终止。
- 单个工具失败：工具卡片标记失败，Agent 可继续运行。
- 服务进程退出：浏览器显示断线；服务恢复后遗留 Turn 变为 `interrupted`。
- 页面刷新：历史阶段、工具和终态从数据库重放；活动 Turn 重新订阅 SSE。
- 敏感内容：不记录原始 thinking；工具预览继续受 8,000 字符限制，既有凭据隔离不变。

## 测试设计

### 后端测试

- Claude runtime 按边界产生有序 `turn.progress`，并且不把 `ThinkingBlock` 或 `thinking_delta` 转成页面事件。
- MCP 成功、失败和超时路径均先产生 `connecting_mcp`，成功时产生 `mcp_ready`。
- 工具开始与完成事件产生有效时间和非负耗时；缺失开始事件时不伪造耗时。
- TurnService 持久化进度事件并保持 sequence 单调。
- 数据库启动恢复同时更新 Turn、Session 并写入 `turn.interrupted`。
- SSE 可重放 `turn.progress` 和 `turn.interrupted`，并在中断终态后关闭。

### 前端测试

- heartbeat 更新连接时间但不向时间线追加重复记录。
- 30 秒静默时出现“仍在运行”，收到业务事件后恢复对应阶段。
- `EventSource.onerror` 后执行一次状态查询，并正确区分重连、终态和后端不可达。
- 工具详情默认折叠，展开后显示输入或输出预览。
- 顶部服务状态不再永久显示静态“就绪”。

### 验收路径

1. 正常 Turn：依次看到准备、连接 MCP、等待模型、工具与完成状态。
2. 慢响应 Turn：30 秒后看到静默等待，heartbeat 和耗时继续变化。
3. 工具 Turn：工具输入输出默认折叠，完成后显示真实耗时。
4. 中途停止后端：页面在下一次 SSE 错误时显示断线，不再保持虚假 `running`。
5. 重启后端：原 Turn 显示“服务退出导致中断”，输入区恢复可用。

## 非目标

- 展示或保存模型原始隐藏思维链。
- 安装或管理系统级守护进程。
- 支持多实例之间的实时事件接管。
- 让已退出的进程内 Turn 在重启后从中间步骤继续执行。
- 为进度事件新增数据库表或迁移；继续复用 `messages` 事件表。
