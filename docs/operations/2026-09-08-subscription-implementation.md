# 订阅推送链路本地实施记录

日期：2026-09-08。以下为本地实现与自动化回归阶段记录（含 Java 定向验证），未部署、发布数据库 Skill 或操作真实规则。后续按用户要求改为基于本地未提交代码测试真实 Agent，不以 UAT 页面作为本次验收对象；最新测试结果与连接阻塞见 [本地 Agent 测试记录](2026-09-08-subscription-local-agent-evaluation.md)。

2026-09-09 更正：下文历史记录中的 bundle 合成摘要及跳过整库检查，不能证明前后端可启动；该方案导致了跨工程启动契约不匹配。已在本地修复，见文末“启动契约修复与回归约束”。此前独立测试通过不等于 UAT 启动通过。

## 基线与边界

| 工程 | 分支 / 起点 | 本次职责 |
| --- | --- | --- |
| davinci | codex/collaborative-space-agui-implementation / 65581c25 | 原生七步编辑器、六工具实现、上下文与候选、确认保存、后端回执 |
| claude_workspace_mvp | main / 5d9cfa7 | V2 正式契约、托管 Skill 源文件、模拟链路与验收用例 |
| davinc-data-mcp | main / 86a67bf | 无代码修改，沿用现有能力 |

沿用 `space.message_rule.get_context/search_options/start_draft/apply_draft/review_draft/save_draft` 六工具，复用现有 Store、Port、normalizer、validator 和保存入口。不增加另一套 SubscriptionSpec 状态机或 MCP 工具。

“帮我配置”停在可编辑、未保存草稿；只有用户明确保存并通过原生确认才创建，创建固定停用。现有持久化规则的修改、启用、删除、实际试发不在本轮 Agent 实施范围。

## 按实施顺序落地

### 1. 正式契约与七步操作

- 时间：日/周/月/月末/小时、多个时间点、生效区间，与原生参数对应。
- 数据：多查询、同源多次查询、维度/指标/聚合、过滤、查询变量、比较指标和格式；新增查询或切源前必须完成字段元数据加载。
- 条件：OR 组内 AND、源字段与查询输出分离、比较输出引用、字段类型与操作符校验；命中上限默认 200，范围 1–1000。
- 推送方式：整体/逐条/分组，以及显式补充查询和字段映射。
- 接收方：本人、成员、群组、查询字段动态员工；个人快捷规则按原生约束固定本人。
- 内容：原生七类组件、标题/富文本和数据绑定、表格/图片/按钮等；按钮仅允许安全 HTTP(S) 链接。
- 命名保存：名称最多 30 字、标签、默认停用。

关键文件：

- Host `contracts/davinci-agent-v2.json` 与 `web/shared/generated/davinci-contracts-v2.js`。
- Davinci `webapp/share/containers/WorkBenchNew/agent/contracts/{contract-v2.fixture.json,generated-v2.ts}`。
- Davinci `.../SubscriptionConfigDetail/draft/SubscriptionDraftOperations.ts` 及测试。
- Davinci `.../SubscriptionConfigNodes/DatasetsConfigNode/utils/outputFieldIdentity.ts`：仅兼容实际比较输出类型。

### 2. 上下文、候选、快捷草稿接续

- 三个配置中心保持个人/所属空间归属；个人、协同、同步仪表盘的推送和预警快捷入口始终个人归属。来源位置不替代规则归属。
- `get_context` 默认不查规则列表，返回可信 scope、活动草稿 revision、锁定状态、查询/输出引用和七步配置回显。只有显式查询历史才加载列表。
- 候选按可信 scope 和父对象检索；重复搜索不无故废弃有效引用，拒绝猜测的引用。源字段 ref 不与查询输出 ref 混用。
- 新增 `subscriptionDraftPublicContext.ts` 仅做有界、脱敏投影，不新增状态。未完整回显的配置标记 unresolved，不能整包盲目回写。
- 同一原生 Store 接续模板、手工和仪表盘快捷草稿，局部 operation 保留未指定和手工修改部分。已有草稿拒绝重复 start。
- 协同仪表盘同页浮层有独立页面身份，使打开/关闭 ACK 的 revision 确实变化；Agent 原地关闭不触发浏览器后退。
- 视角、归属切换与外部规则清空撤销旧草稿；原生手动保存后保留去向弹窗但立即撤销 Agent 写能力。
- 快照/链接资源等显式权限在工具执行层拒绝，不能靠注入个人 scope 绕过。

关键文件：`SpaceMessageRuleAgentController`、`spaceMessageRuleOptions`、`messageRuleAgentScope`、`subscriptionAgentDraftHandoff`、`subscriptionDraftPublicContext`；Workbench 的 `workbenchMessageRuleScope`、`workbenchAgentRouteView`、`useWorkbenchMessageRuleBinding`；两侧页面 `index.tsx` 与 `SubscriptionConfigDetail/index.tsx`、`SubscriptionDraftPort.ts`。均有对应测试。

### 3. 校验与确认保存

- 默认 review 复用七步静态校验及批量字段/权限元数据校验，不执行真实查询、渲染或试发。
- 原生确认之后再次验证 actor/scope/revision/policy；保存时调用一次创建请求。
- 后端新建固定 disabled，并复用既有领域/数据权限校验；更新入口也执行配置校验，但不擅自改变已有状态。
- 新增真实创建回执：ruleId、ownerType、spaceId、status。前端保留旧 boolean 包装供既有页面使用；Agent 只能按真实回执报告。
- 请求发出后的未知结果保留 saving，禁止自动再次创建；用户取消和明确失败有不同处理。已创建但导航失败不能说成未保存。
- 未实现数据库级幂等键：刷新/新会话后的跨请求去重不在当前保证内，未知结果必须先核对真实规则。

关键文件：`SubscriptionDraftReview.ts`、`saveSubscriptionAgentDraft.ts`、`CollaborativeSpace/api.ts`；后端 `MessageRuleDto.java`、`MessageRuleServiceImpl.java`、`MessageRuleServiceImplTest.java`。

### 4. Skill

更新现有 `workspaces/davinci-dashboard/.claude/skills/configure-subscription-rule/`，不另建平行 Skill。正文按业务意图决定动作，不机械逐步追问；详细字段放 references。同步 workspace 路由及 `configure-dashboard-widget` 的订阅交接说明。

通过 skill-creator 的结构校验并做独立行为演练：只调整已有预警的时间和阈值时，接续原草稿、局部修改、静态复核，无重复起稿、保存或查询。正文为 5173 字节，既有提示词预算测试通过。

`evals/cases.json` 固化 9 类页面位置与归属映射，以及多查询动态员工、复合比较预警、七类内容、续改、显式保存、取消、未知结果、版本冲突、指标歧义等用例。

本次更新的是工程内托管 Skill 源文件；Downloads 中自定义副本未覆盖，已运行会话/数据库托管版本不会自动生效。

### 5. 回归与性能验收状态

| 验证 | 结果 |
| --- | --- |
| 前端领域、页面、快捷入口、Store 接续、权限、保存与真实 Flow 契约 | 30 suites / 561 tests 通过 |
| Host 契约、workspace/Skill 预算与订阅模拟链路 | 58 tests 通过 |
| 定向契约同步脚本 | 11 通过，2 个整库来源检查因基线不匹配跳过 |
| Host V2 生成 `--check` / Davinci bundle `--check` | 通过 |
| Skill `quick_validate.py` | 通过 |
| Java 公共模块 | 3 suites / 17 tests 通过，已安装当前分支的本地 SNAPSHOT |
| Java 订阅定向回归 | 7 suites / 196 tests 通过，失败/错误/跳过均为 0；主代码及测试源码编译通过 |
| 真实 Qwen live 用例 | 3 skipped，未触发真实模型调用 |
| 修改页面 TypeScript 相对 HEAD 诊断比较 | 当前与基线均 14 项，新增 0 项；不等于全工程类型检查通过 |
| 两工程 `git diff --check` | 通过 |

自动化已验证默认数据查询为 0、默认规则列表查询为 0、已有草稿无需再次 start、apply 每批最多 8 个有序操作、失败批次不部分提交。Jest 耗时不能当作真实 Agent 响应时间。

尚待 UAT 测量：首个可编辑草稿耗时、完整静态复核耗时、模型及工具调用数、p50/p95、歧义澄清轮数；覆盖三中心与各类快捷入口、个人/空间权限差异及人工修改后续接。未宣称真实端到端性能验收通过。

## 已知基线问题与上线前条件

1. Java 编译阻断的确切原因是本地 Maven 的 `davinci-common:1.0-SNAPSHOT` 过旧，并非源码缺类：当前分支的 `davinci-common/.../shared/utils/IdGenerator.java` 已存在，但旧缓存 JAR 不含此类。按仓库既有 Jenkins 构建顺序重新安装当前 common 后，服务端主代码编译通过。README 已补齐构建顺序和离线复验说明，不新增工具类、不修改依赖版本或 ID 算法。
2. Host main 有 59 个公开工具，Davinci 指定分支已有 67 个。为不覆盖无关能力，既有同步脚本增加 `--bundle space-message-rule`，只合并六工具及专属 schema；拒绝覆盖其他工具共用 template。已逐项核对其他工具、pageStateSchema、errors 不变。fixture provenance 记录合成摘要及 bundle 来源，不声称整个 Host 与前端基线完全相同。
3. 页面基线已有 TypeScript 问题：`perspectiveEpoch` 类型、原生 onSave 返回类型、旧 TS 不支持 catch 类型注解；本次未新增诊断，也未扩展修改这些基线问题。
4. UAT 验证前需协调部署前后端回执契约和托管 Skill 新版本。新前端遇到旧后端缺失真实回执时会停止并报告结果未知，不伪造创建成功。

## Java 基线补修结果

用户确认一并修复后，完成以下最小修复；本轮补修没有额外修改生产 Java 代码、POM、依赖版本或 ID 算法。

- 重新构建并安装当前 checkout 的 common 到本地 Maven 缓存，确认 JAR 包含 `edp/davinci/shared/utils/IdGenerator.class`。
- `davinci/README.md` 补充先安装 common 再构建 server 的顺序、显式开启测试参数和离线复验说明，与既有 Jenkins 构建顺序一致。
- `MessageRuleScheduleServiceImplTest.java` 保留全部 8 项旧用例，改为使用当前 `MessageRuleApplicationService` + 真实 `MessageScheduleCalculator` 计算时间；文案仍验证既有 scheduleText，不恢复废弃的 validate/nextTriggerTime API。
- `MessageRuleServiceImplTest.java` 补齐两个资源关联用例的独立查询 ID；保留底层数据集去重断言。同步 5 项过时权限/认领/启停测试：历史创建者不能绕过有效成员关系，认领走当前系统停用状态及数据范围比较，启停更新状态和下次触发时间但不重建历史权限快照。
- 权限/启停断言调整依据是已有基线迁移（`9d0b819b7`、`9dfcd0a4e`），没有放宽生产权限或删除测试。额外修正“默认启用”的旧测试注释为默认停用。

最终 Java 合计 10 suites / 213 tests 通过（公共模块 17 + 服务端 196），无失败、错误或跳过。这是定向回归，不代表执行了全仓 Java 测试或真实 UAT 验收。

## 主要复验命令

在 Host 工程执行：

```sh
python -m pytest tests/test_agui_contracts.py tests/test_workspace_registry_davinci.py tests/test_subscription_agui_simulator.py -q --tb=short
python scripts/generate-davinci-contracts.py --profile v2 --check
```

在 Davinci 工程执行：

```sh
node webapp/scripts/sync-davinci-agent-contracts.js --profile v2 --bundle space-message-rule --source ../claude_workspace_mvp/contracts/davinci-agent-v2.json --check
node --test webapp/scripts/sync-davinci-agent-contracts.test.js
```

本次 Java 实际复验命令（依赖已缓存，离线使用当前分支公共模块）：

```sh
mvn -o -f davinci-common/pom.xml -DskipTests=false -Dmaven.test.skip=false install
mvn -o -f server/pom.xml -DskipTests=false -Dmaven.test.skip=false -Dtest=MessageRuleServiceImplTest,MessageRuleScheduleServiceImplTest,MessageRuleValidatorTest,MessageRuleDatasetQueryServiceTest,MessageRuleApplicationServiceTest,MessageRuleCoreFlowTest,MessageRuleControllerValidateTest test
```

前端使用项目既有 Jest，覆盖 `CollaborativeSpace/agent`、订阅 `draft`、`SpaceDetail/index`、`SubscriptionConfigDetail/index`、Workbench scope/binding/route、快捷推送与预警、原生 Store 集成、rule adapter 及输出字段身份测试。本次仅临时复用既有 node_modules 和临时 Python 环境，未升级项目依赖或改动锁文件。

## 启动契约修复与回归约束（2026-09-09）

根因：此前 Davinci `--bundle` 模式对合并后的 JSON 计算运行时摘要，而 Host bootstrap、embed 及生成脚本均对原始契约文件字节计算 SHA256。两端业务契约虽然已在 pull 后对齐，但摘要不同，导致前端在加载 iframe 前抛出 `Agent bootstrap contract mismatch`。

最小修复：

- 完整和 bundle 同步统一使用 Host 原始文件摘要；移除合成摘要及其过时 provenance。
- bundle 合并后必须与完整 Host 契约深度相等；不同则在写入文件前失败，不能覆盖其他领域或伪造兼容。相等时按正式源文件顺序生成。
- 保留 bootstrap、embed、Bridge 的严格校验，不改七步业务逻辑或六工具。
- 原来因来源摘要不匹配而跳过的检查改为失败。新增 bundle 漂移拒绝/不覆盖、键顺序兼容、配套工程启动握手测试。

本次正式契约摘要为 `27ebdc8869f62e74dd9223251e817c25a93df17e17c519a4cb22bb20f41013f9`，来源 Host `980ce946a42b8a86b492d3d8c93d1aec998ccaff`。已用深度比较确认生成 fixture 的完整 contract 与修复前语义相同；较大文本差异仅为 schema 键顺序恢复为正式源顺序。生成 TypeScript 仅摘要变化，V1 未变。

回归先在旧代码失败，再在修复后通过：

| 检查 | 修复后结果 |
| --- | --- |
| 同步脚本及完整来源检查 | 15 passed，0 skipped |
| 前端启动、Bridge、契约、协同 Agent、订阅草稿 | 26 suites / 365 passed |
| Host bootstrap/embed、契约、订阅模拟、workspace 等定向测试 | 108 passed |
| Host JS 协议、Bridge、工具运行器 | 33 passed |
| Host 生成器全 profile `--check` | 通过 |
| Davinci `npm run check:agent-startup` | 通过（含 15 项同步测试及 7 项启动测试，与上表重复，不累计） |

发布前必须使用本次配套的两个 checkout 执行：

```sh
# Davinci webapp 目录
npm run check:agent-startup
# Host 根目录
python scripts/generate-davinci-contracts.py --check
python -m pytest tests/test_local_davinci_bootstrap.py -q
```

默认两工程同级；其他布局见 Davinci README 的 `DAVINCI_AGENT_CONTRACT_DIR`、`DAVINCI_HOST_ROOT`、`DAVINCI_WEBAPP_ROOT` 说明。新测试读取对端实际生成物；Host 侧走真实 ASGI bootstrap/embed 端点，不使用真实账户或业务规则。前端测试使用对端原始文件摘要构造接口响应，并验证 Host 已构建 bundle，未连接 UAT。

上述命令已作为代码内自动化回归入口，但未改造现有单仓 CI 的 checkout 拓扑；不能声称流水线已自动强制执行。未部署 UAT、未验证真实模型响应；模型网关连通性属于另一项验收，不能以本次本地启动测试代替。
