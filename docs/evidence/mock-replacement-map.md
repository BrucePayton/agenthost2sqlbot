# Mock 逐步替换台账

当前状态：未启用任何 Mock。Data MCP 的 `table.search/describe/query` 明确禁止 Mock。

2026-09-21 更新：Asset Search MCP 的 `access.check_resources`、Schema 和资产工具已真实可用，`MOCK-DM-001` 禁止启用；SQLBot 动态 certificate/回调头透传已有源码证据，`MOCK-SB-004` 仅可用于故障注入。

## 使用规则

- Agent Host：`MOCK-AH-*`，只用于冻结尚未实现的 API/UI/事件契约。
- Data MCP：`MOCK-DM-*`，只覆盖经真实枚举确认缺失的报告扩展能力。
- SQLBot：`MOCK-SB-*`，只用于不可达、故障注入或缺口影响演示；对应 `SB-GAP-*` 不得消失。
- 每个启用项必须补充失败证据、调用方、端口、契约测试、替换负责人、退出条件和删除日期。

## 架构位置

| 系统 | Mock 根目录 | 契约测试 | 正式替换边界 |
|---|---|---|---|
| Agent Host | `mock_services/agent_host/` | `tests/contract/agent_host/` | 现有 sessions/runtime/agui/web 的 agentId/bootstrap/data.ask 适配；不新建智能体主数据域 |
| Data MCP 扩展 | `mock_services/data_mcp/` | `tests/contract/data_mcp/` | Java Office 网关模块或其后的内部 Python Ask Service；调用方只切换配置/transport |
| SQLBot | `mock_services/sqlbot/` | `tests/contract/sqlbot/` | `SQLBOT_BASE_URL` 切回真实 18000；不改 SQLBot 源码 |

## 候选状态

完整 ID、文件位置和替换目标以 `docs/analysis-report-flow-drawio-plan.md` 第 8 节为准。实际启用时在此新增一行：

| Mock ID | 状态 | 启用原因/证据 | 调用方 | 代码位置 | 契约测试 | 真实目标 | 退出条件 |
|---|---|---|---|---|---|---|---|
| - | 未启用 | 当前处于真实能力核验和样板阶段 | - | - | - | - | - |
