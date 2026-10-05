# Data MCP 能力证据

## 已确认事实

- 配置文档：https://atrenew.feishu.cn/wiki/GRMjweq1piRPJHkjnh4c4mBwnfd
- 已通过 `lark-cli docs +fetch` 读取文档。
- npm 包：`@aihuishoubi/mcp-proxy`，本机已安装为 `/opt/homebrew/bin/mcp-proxy`，版本 `1.0.3`。
- 网关：`https://bimcp-gateway.aihuishou.com`，环境 `prod`。
- 缓存目录：`/private/tmp/aihuishoubi-codex-149502`。
- 认证：公司 CAS；配置中不手工放 Token。
- 文档公开工具：`table.search`、`table.describe`、`table.query`。
- 限制：单表；不接收原始 SQL；库范围 `dw/dm/dim/rpt`；预览默认 10、最多 100 行；CSV 100,000 行/24 小时；并发 10；查询最长 30 分钟。

## 本机验证

| 检查项 | 结果 | 证据/解释 |
|---|---|---|
| 安装包 | 通过 | `command -v mcp-proxy` 返回 `/opt/homebrew/bin/mcp-proxy` |
| MCP initialize | 通过 | 返回 server `aihuishoubi-mcp-proxy`、协议 `2025-11-25` |
| CAS 会话创建 | 通过 | 浏览器打开 `sso.aihuishou.com/cas/login` |
| CAS 登录完成 | 阻塞 | 需要用户本人输入公司账号或使用 Passkey；未读取、记录或代填凭据 |
| tools/list | 未完成 | 等待 CAS 完成后返回；不启用 `table.*` Mock |
| search/describe/query | 未完成 | 依赖同一 CAS 登录 |

当前结论：服务存在、代理和网关可达、认证流程可启动；尚不能把三个工具标记为“本机运行验证通过”。

## 报告扩展能力差异

飞书文档只证明单表 MCP。以下目标能力没有出现在文档公开工具集中，必须在 CAS 完成后继续枚举其他工具/API；当前状态为 `EXTERNAL/GAP`：

- 数据集显式权限检查现已由 Asset Search MCP 的 `access.check_resources` 覆盖；授权成功路径仍待有权样本验证
- `sync_assistant`
- `data.ask`
- ticket 签发、校验和撤销
- SQLBot 回调供数接口
- `ask_session` 映射
- `result_cache`
- 身份和资源 ID 映射

不得因 Data MCP 服务存在就推定这些扩展已经存在；也不得为 `table.search/describe/query` 创建 Mock。

## 新增 Asset Search MCP（2026-09-21）

新端点 `https://asset-search-mcp-office.aihuishou.com/mcp` 已完成 initialize、`tools/list` 和最小只读调用。它是 `davinci-data-mcp 1.1.0`，提供 12 个资产搜索、数据集目录、Schema、字段和显式权限工具。使用 `159358` 已验证单/双数据集授权成功、字段子集和 Schema；使用 `149502` 的无权样本验证了拒绝路径与 Schema 隐藏。三个授权样本均返回空 `requiredFilters`，非空必填筛选正样本仍待补充。

该端点仍没有 `data.ask`、ticket、assistant 同步、SQLBot callback、SSE 归一化和结果缓存，因此不能取代报告目标中的问数编排 Data MCP。完整证据见 `asset-search-mcp-capability.md`。
