# 订阅 Agent 优化实施与验证

日期：2026-09-15

状态：本地实现和程序链路验证完成；真实模型耗时目标尚未验收。改动涉及配对 AgentHost / Davinci，尚未提交或部署。

## 1. 本次改变

### 直接减少模型工作

- 首轮提示改为尽早交出已明确的部分意图，保留完整资源名称；资源尚未搜索属于执行依赖，不能自动变成业务澄清。
- 在现有 `get_context.configurationIntent` 中增加有限的 `dataBindings`。模型交出可信查询或目录引用、字段用途、昨日口径、阈值及内容位置；程序生成原生查询、比较、条件和内容绑定。继续复用已有 Controller、DraftStore、权限校验和原生操作。
- 确定性接续、程序展示的唯一 A/B 回答和完成交接不创建模型 SDK client。字段齐备后不再要求模型组装临时引用及复杂原生参数。
- 接续上下文移除执行账本和已消费操作；技术缺口明确关联一个查询时，仅投影该查询完整证据。保留其全部字段、输出、筛选、参数、权限和能力，明确说明省略了哪些配置。稀疏或无法唯一定位的回执保持原文。
- 旧 checkpoint 没有结构化意图时保留候选和上一段回复，保证历史 A/B 接续仍有解释依据。

### 可靠性与用户选择

- Widget 来源按可信父仪表盘核验，不再要求候选提供不存在的 `sourceKind`。
- `resolvedKeys` 精确关闭对应澄清；同版技术增量省略 `requirementsComplete` 时保留原值。
- 已确认来源跨“改时间”等用户增量保留。只有新的明确换源要求通过 `changedSelections`，或回答新的有效候选问题，才允许替换；技术恢复不能自行换源。
- 写入成功先消费原请求，再处理下一步，避免拿已失效引用重新编译成功日期修改。重复及迟到回执不能覆盖新需求。
- 以原生字段身份复用引用，日期筛选值或展示名称变化不造成无意义换引用；原值和比较输出仍有不同身份。
- 同目标业务绑定与原生操作明确拒绝冲突；已完成正文不会因自动补标题再次整体写入。用户改时间不会重放旧日期要求。
- 保留完整空间名；空间首页读取失败时独立接收合法意图，按真实导航能力继续，原工具错误仍保留。
- 同一原生草稿内归属写法改为 `current` 时保留消费和目录绑定记录；真实归属或草稿变化时清理旧执行证据并停止自动接续，避免重复创建或跨空间复用。
- 相同技术缺口连续三次没有新增相关证据时停止自动循环，保留现场。无关标题、其它查询和单纯版本变化不重置计数。

原生 DraftStore 仍是唯一配置事实来源。最终停在“命名并保存”，由用户发送预览、检查后保存。

## 2. 首批支持范围

| 路径 | 实施情况 |
| --- | --- |
| 固定通知 | 已支持时间、接收对象、富文本及交接 |
| 仪表盘截图 + AI + 跳转 | 已支持来源定位、明确选择及连续配置 |
| 昨日四列表格 | 新增目录绑定编译，保留区域经理、门店、成交量、成交金额的指定列序 |
| 图表昨日原值 + 日环比预警 | 新增日期、比较、双阈值和两个正文变量绑定，保留原格式及其它筛选和查询 |

业务绑定首批限定单查询、昨日日期和日环比增长率。复杂关联、其它日期口径、动态员工和其它复杂内容继续使用已有原生操作；七类原生组件的能力没有删除。

原生表格结构不能用该简化入口表达“比较列之后再放维度列”时，返回明确缺口，改用已有真实输出引用的原生表示，不悄悄调整用户列序。已绑定的目录查询需要修改时，要求使用当前 `queryRef`，避免再次创建同源查询。

## 3. 时序和模型参数

新增首段正文、首次工具、首次/最后一次可观察 thinking 的阶段计时；记录 thinking 来自全局覆盖还是订阅默认值。失败时另发不含正文或凭证的部分时序诊断，不把失败当作零耗时成功。

实时验证报告分别统计 Host 轮次、SDK 返回的模型调用次数、程序调用及失败时序。SDK 未返回用量时模型调用数为未知；不能用 Host 轮次代替模型调用次数。

本次没有默认换模型或缩短超时。思考流时间是可观察时段，不能等同于模型内部计算时间。

## 4. 验证证据

测试数量有重叠，不相加作为独立用例总数。

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| Host 订阅、编译、上下文、运行时及恢复回归 | 368 passed，6.17 秒 | `/private/tmp/subscription-optimization-host-final.log` |
| 最终时序观测相关 Host 回归 | 163 passed | `/private/tmp/subscription-optimization-runtime-final.log` |
| Davinci 引用、回读、目录及交接专项 | 48 passed，11.58 秒 | `/private/tmp/subscription-optimization-native-unit.log` |
| Host JS 回归 | 165 passed | `/private/tmp/subscription-optimization-js.log` |
| 真实原生浏览器链路 | 6 passed，10.99 秒 | `/private/tmp/subscription-optimization-native-live.log` |
| 配对 `check:agent-startup` | 通过；含 258 项前端、90 项 Host、1 项真实双 Origin 浏览器检查 | `/private/tmp/subscription-optimization-startup.log` |

真实原生浏览器用例使用实际 Host → Controller/Store → 回执，元数据和后端校验使用隔离夹具。确定性路径的 SDK client 创建即抛错，验证了零模型接续；这不等同于从自然语言开始的真实模型验收。

新业务绑定两个用例还断言：每场景只写一次查询和一次内容；日期引用稳定；原值与日环比绑定正确；区域筛选和历史查询保留；不请求预览、保存或真实业务查询。

两端契约已从唯一源生成，Host `embed.js` 已构建。契约 SHA256：`c32f5fc5cd476b3e12e5c970c922918f44e47c3cc68d7c872d6dd3b24271c1a3`。注册 schema 为 32647 bytes，保持既有 32768-byte 上限；共享相同 `$defs` 后，展开的原生操作 schema 与改动前一致。

## 5. 尚未完成的真实性能验收

在当前配置的 `deepseek-v4-pro-0813` 上尝试了真实模型 + 原生页面路径。工具注册通过，但首轮约 62.654 秒未产生正文或工具调用，随后以 `SUBSCRIPTION_NO_PROGRESS` 结束；整体 65.994 秒，原生配置仍为空，不能作为提速成功样本。

另行对同一配置网关进行的最小参数探测中，省略 thinking 和显式 disabled 均返回 HTTP 502。因此尚不能证明网关是否实际执行了 thinking 设置，也不能将首轮无输出精确归因于模型计算或网络排队。

失败报告：`/private/tmp/subscription-optimization-live-model/subscription-live-evaluation.json`。该次运行早于追加失败时序诊断；没有后来补造时序。报告中用量和 thinking 时序缺失表示未观测到，不能填零。

以下项目仍待同一模型链路恢复后完成：九条原始自然语言需求重跑、用户增量/中断场景的真实模型验收、每个代表场景至少三次配对测量，以及首次正文、首次写入、schema 到业务选择、选择到写入的完整耗时比较。暂不声称“中位数下降 50%”或“schema 后 30 秒内写入”达标。

## 6. 主要改动入口

- Host `app/runtime/subscription_executor.py`：意图、澄清、来源保护、回执消费及接续。
- Host `app/runtime/subscription_bindings.py`：有限业务绑定到现有原生操作的编译。
- Host `app/runtime/subscription_model_context.py`：只读模型上下文与当前查询证据投影。
- Host `app/runtime/claude.py`：调度、交接、程序选择及模型观测。
- Host `contracts/davinci-agent-v2.json`：唯一契约源及两端生成物。
- Davinci `spaceMessageRuleOptions.ts` / `subscriptionDraftPublicContext.ts`：稳定引用及完整能力回读。
- 项目 `configure-subscription-rule` 的 Skill 与 `references/business-bindings.md`：模型职责和调用示例。
