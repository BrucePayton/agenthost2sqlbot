# 订阅推送：本地未提交代码测试记录

## 范围与当前结论

用户要求基于本地工作区代码测试，不提交、不部署、不到 UAT 验收。

- Davinci：`codex/collaborative-space-agui-implementation`，基线 `65581c25f`，包含当前未提交改动。
- Host：`main`，基线 `5d9cfa7`，包含当前未提交的契约、Skill 与测试改动。
- 模型：读取当前 `workspaces/davinci-dashboard/workspace.yaml`，为 `deepseek-v4-pro-0813`。
- 本次没有访问 UAT 业务页面、实际取数、创建规则或发送消息，也没有提交、推送、部署和修改生产逻辑。

**本地定向代码回归通过；真实 Agent 效果测试尚未完成。** 真实 Host 首轮在等候模型时超时，独立连接探针发生 TLS 连接中断。没有模型工具调用，不能据此判定 Skill 效果，也不能给出配置成功率或配置耗时结论。

## 已执行的测试

| 层次 | 结果 | 能证明什么 |
| --- | --- | --- |
| 当前前端原生代码定向回归 | 5 suites / 167 tests 通过 | Controller、Store 操作、静态复核、归属和保存边界在固定输入下的行为 |
| 当前 Host 契约、工作区与评估夹具 | 62 tests 通过 | 契约、Skill 配置和评估脚本的确定性检查 |
| 真实模型 + 当前 Host 首轮路由测试 | 失败，Host `RUN_ERROR` | 当前测试环境无法取得模型输出；不是订阅业务断言失败 |
| 完整草稿与确认保存的真实模型多轮场景 | 未运行 | 首轮连接未通，不继续发起长时间测试 |
| 模型生成操作在原生前端中的回放 | 未运行 | 尚未取得可回放的模型操作 |

前端定向套件：

- `SubscriptionMessageRuleAgentFlow.test.tsx`
- `SpaceMessageRuleAgentController.test.ts`
- `SubscriptionDraftOperations.test.ts`
- `SubscriptionDraftReview.test.ts`
- `workbenchMessageRuleScope.test.ts`

Host 定向套件：`test_agui_contracts.py`、`test_workspace_registry_davinci.py`、`test_subscription_agui_simulator.py`。评估脚本自身的 10 项测试包含在 62 项中，不重复计数。

## 真实模型尝试的证据

原始业务问题：

> 帮我配置：每天 09:00，把奢侈品回收数据按区域经理分组推送给对应员工；指标用回收金额，维度用城市，只看已完成订单，消息包含富文本和数据表。

本地通过 ASGI 调用当前 `create_app` 与 `/api/ag-ui`，使用临时 SQLite、临时工作区和当前 Skill 文件。业务页面工具使用现有契约校验夹具，不连接 Davinci 业务服务。

- 单轮 `/api/ag-ui` 耗时：183.872 秒；测试总耗时：186.35 秒。
- HTTP 200 流内返回 `RUN_ERROR`，不得按 HTTP 状态判断成功。
- 最终助手正文为空、工具调用 0、Skill 调用 0。
- 独立最小网关请求失败：`httpx.ConnectError`，TLS 原因码 `UNEXPECTED_EOF_WHILE_READING`，最后一次探针耗时 3.375 秒。
- 未禁用证书校验，未修改网关、模型、代理或凭证配置。TLS 中断发生在取得模型响应之前，具体网络侧原因尚未确定。

这 184 秒是失败请求的等待时间，**不是成功配置的性能数据**。没有足够样本计算成功率、P50 或 P95。

临时原始证据：

- `/private/tmp/subscription-agent-eval.tJ7YFV/routing-run-2/test_real_qwen_loads_subscript0/subscription-live-evaluation.json`
- `/private/tmp/subscription-agent-eval.tJ7YFV/gateway-probe.json`

## 为可信测试补充的评估能力

改动仅在现有测试与本记录中，不修改业务生产代码：

- 模型默认跟随当前工作区配置；需要对比时可显式指定 `DAVINCI_SUBSCRIPTION_EVAL_MODEL`，不再固定旧 Qwen 模型。
- 去掉模型卡住后自动提示 `dashboard-push`、`scheduled_dataset` 等内部参数答案的行为。
- 保留原始问题、每轮助手文案、工具参数与模拟回执、每轮及总耗时、澄清次数、Skill 次数、预览与规则列表请求数。
- 首轮需要补问如实记录为 `clarification-needed`，不冒充一次配对成功。
- 失败也保留报告；不保存原始 stderr、环境变量、设置对象或凭证。
- 识别流内 `RUN_ERROR`，避免把运行时错误误报为业务澄清。

当前多轮夹具只有一个空间分组推送场景，工具回执仍是模拟的。这不能替代个人中心、空间中心、同步空间中心、仪表盘推送/预警快捷草稿接续、多查询与复杂条件的真实模型覆盖；也不能替代包含所有 Skill 的统一智能体路由评估。

## 测试环境

Python 临时环境按现有 `uv.lock` 同步运行依赖，包含 `claude-agent-sdk==0.2.128`、`mcp==1.29.0`、`ag-ui-protocol==0.1.19`。Apple Silicon 环境另补装项目已有约束版本 `greenlet==3.2.4`，解决模型调用前的异步数据库启动依赖问题。未改项目依赖声明或锁文件。

前端临时复用已有 `node_modules`。原生代码由当前工作区 Jest 转译执行，没有使用已部署页面。

## 继续条件

先确认当前本机可用的模型网关连接配置。连接恢复后，从首轮路由测试开始，再运行严格的完整草稿场景，检查最终回复是否与草稿/回执一致，并将真实模型操作交给当前原生 Controller/Store 执行验证。所有真实业务服务继续使用测试边界，不保存或发送实际规则。

## 拉取远端后的集成复验

按用户后续要求拉取并整合 Host `origin/main` 至 `6b67ecc`，保留远端全部非订阅契约及路由变化、本地六工具契约与订阅草稿流程。生成摘要冲突由现有生成器重建，工作区路由合并两侧意图，保持 5000 字节预算（合并后 4994 字节）。同步重建实际提供给浏览器的 `app/web/static/embed.js`，避免源码契约已更新而浏览器仍使用旧摘要。

- Python 定向集成检查：95 项通过，包括契约、工作区、订阅夹具、数据集编辑契约、deferred tools 与工具计划。
- JS 桥接、前端工具执行及协议检查：33 项通过。
- `build:agui`、契约生成 `--check` 与 `git diff --check` 通过。
- 未重新运行真实模型测试，前述网关连接阻塞与效果测试未完成的结论不变。
