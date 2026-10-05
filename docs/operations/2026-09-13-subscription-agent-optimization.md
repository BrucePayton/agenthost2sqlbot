# 订阅 Agent 优化修复记录（2026-09-13）

本轮修改 Davinci 前端、Host 工具契约、运行时及订阅 Skill，围绕组件预警卡住、同环比配置猜测、原值引用丢失和配置往返过多四类问题。未修改旧 davinc-data-mcp，也未部署、调用线上模型或创建真实推送规则。

## 已实现

| 问题 | 调整后的行为 |
| --- | --- |
| 原生组件预警已打开，Agent 仍等待页面切换 | 订阅覆盖层发布正确工作流；导航 ACK 检查订阅工具已注册，接受保留原仪表盘身份的空间页面，同时拒绝其他来源；补上模板写入与 React 订阅之间的竞态。 |
| 内容组件候选漏掉指标卡，Agent 转向全局找数 | 增加 `search_options(kind=alert_widget)`，复用原生铃铛的 21 种图表类型判定。正文 `widget` 候选维持原用途。 |
| 从订阅中心选择组件后重新组装底层查询 | `start_draft(mode=widget, widgetRef)` 导入原生 type=4 动态模板，保留指标、同环比和原筛选。规则归属由当前订阅中心决定。 |
| 同环比英文枚举、周期和参数靠试错 | `get_context.comparisonCapabilities` 从原生弹窗生成规则；start/apply 的 schema 校验完整周期、基准、差值/差异率、参数个数和截至日期组合。 |
| 补同环比时覆盖原指标，导致条件、正文引用失效 | `metricsMode=merge` 按输出身份合并；原值和同环比各有独立 outputRef。同批 outputKey 按实际输出身份定位，保留已有 cmpId。 |
| 组件回读不能表达某类同环比，却被当成没有配置 | 回读给出明确表达缺口；订阅通过原生模板继承。已有不完整比较配置保留并在 configuration.unresolved 标明，不静默丢弃。 |
| 已知需求仍逐步建空草稿、填时间、绑查询、补正文 | start/apply 共用完整原子操作器，批次上限从 8 提升至 32；支持同批查询和输出的 local 引用。精确来源资格与字段元数据并行核验。 |
| 回执后持续长推理、反复搜已绑定数据、相同参数反复失败 | Host 按可信回执标记阶段；普通订阅回执默认思考预算 1024，确认保存后关闭思考。Skill 和阶段提示规定只补缺失字段或新增来源；同一上下文相同失败写参数达到两次后拦截重复提交。 |

`metricsMode=replace` 或省略时，已传入的 metrics/metricRefs 仍表示完整替换；完全省略指标列表时保留现有指标。新增比较应明确使用 merge。

数据完成状态目前用于指导检索收敛，不强行禁止一切后续检索：新增查询、缺失字段和用户修改仍需合法检索。成功回执不等于业务要求全部完成；review 的结构校验也不替代需求核对。

## 两条组件预警路径

1. 仪表盘页面：定位组件 → 原生 `open_data_alert_config` → 读取已带入草稿 → 一批补齐条件、内容等配置 → review → 按用户授权终点保存。
2. 订阅中心：定位 dashboard → 查 alert_widget → start(mode=widget) → 用返回的原值/比较 outputRef 配置条件和正文 → review → 保存。

只有仪表盘页面的原生铃铛路径继承当前生效的全局筛选。订阅中心导入使用已保存组件模板，不能宣称继承另一个页面的临时筛选。模板无法唯一导入时返回明确错误，不改用全局同名数据。

个人订阅、协同空间及同步空间的归属和接收人约束继续由原有 scope/policy 决定；来源引用不改变规则归属。用户手改引发版本冲突时，重新读当前表单后组装变更，不只更换 revision 重发。

## 工具与 Skill 一致性

- 原生同环比能力覆盖 8 类评估期，合法比较基准随评估期变化。前端回归逐一比较原生选项与公开 schema；不使用另写的近似枚举。
- 用户截图对应：`valueExp=last_day`、`calcMethod=dayChain`、`calcType=diffRate`，时间口径引用真实日期维度。差异率原始阈值 -0.1 对应 -10%。
- start/apply 的输入 schema 通过局部 `$defs` 复用相同类型，保持 Host 32 KiB 注册限制，未放松字段校验。start 预算与 apply 对齐为 60 秒，原生导航 ACK 仍有单独超时限制。
- 主 Skill、tools.md、state-and-operations.md 和用例说明同步更新，移除旧的“三类初始操作/每批 8 项”描述。
- Host canonical contract 经官方生成、构建，再同步到 Davinci fixture/generated。最终原始字节 SHA-256：`221d579d867726956c6dfeb657b10a690f328f2b77384cf8310dfa01d021c5df`。

## 自动化验证

| 检查 | 结果和范围 |
| --- | --- |
| 前端定向回归 | 14 suites、307 tests 通过，覆盖原子配置、选项检索、引用、同环比契约、原生入口和挂载竞态。 |
| 产品配置流补充回归 | 完整补上指标卡双条件和正文变量后，配置流 30 tests 再次全部通过；这 30 项属于上述前端范围，不重复计数。 |
| Host 定向回归 | 272 tests 通过，覆盖 bootstrap、contracts、models、工具计划、runtime、ledger 和订阅模拟器。 |
| 真实跨 Origin iframe | 最终生成 bundle 下 1 test 通过，使用已有 Chromium 和仓库真实 iframe fixture。它验证嵌入链路，不代表线上产品场景验收。 |
| 构建和契约同步 | Host embed 构建、双端 canonical hash、生成物及严格 schema 检查通过。 |
| 差异格式检查 | 两个仓库 `git diff --check` 通过。 |

产品五类需求在隔离元数据的原生 Store/controller 回归中的覆盖：

| 用例 | 已验证 |
| --- | --- |
| 每天 09:00/18:00 截图、AI、跳转 | 无业务查询，复用同一仪表盘，保留 AI 组件引用。 |
| 周一 14:00 昨日成交表 | 一次 start 完成目录绑定、滚动昨天筛选及有序四列；精确来源资格、字段各读取一次。 |
| 每天 10:00 原值 <10000 且日环比 <-0.1 | 原生模板继承；两个独立输出用于同组 AND 条件和正文变量；个人/空间中心均通过复核，零数据集和字段检索。 |
| 空间月度群通知 | 保留真实 @所有人、斜体、字面“7月”和明确提供的链接。 |
| 单次大额失败订单通知两类负责人 | 明确单次日期、昨日筛选、订单粒度、两种合格员工账号、加粗红字、日期变量及异常订单表。 |

这些测试使用隔离服务回执，不证明新 MCP 的线上召回效果、真实员工权限或实际投递结果，也不能把 Jest 耗时等同于 Agent 响应时间。

## 尚未通过的全量检查

最终 `check:agent-startup` 在完成契约与 bundle 检查后，遇到 DashboardLayoutController 的 3 项失败：captured 2642 布局空洞、40 组件窄排行榜、unchanged compact 的 persisted 状态。该阶段为 244 passed / 3 failed；相关布局实现和断言未在本轮修改，未跳过失败以宣称全量通过。没有单独回放旧提交来确认这些失败的引入时间。

仓库 TypeScript 3.9.10 无法解析已安装依赖中的较新类型语法，全量 `tsc` 未通过。这阻止完整类型验证；不能用定向 Jest 通过替代全量类型检查。

提交前 Davinci 已无冲突接入远端 `f2703fb74`、`565ef4302` 两笔 iframe 修复。契约同步检查再次通过；对本轮测试和远端相关测试补跑 15 suites，338 passed / 1 failed。失败为远端 `iframeWidgetConfig.test.ts` 的 `prefers current Widget objects and appends concurrent additions`：期望一项 `_isNew`，实际为零。该测试和纯函数实现均与远端内容一致，本轮未修改；记录为提交前补充检查发现的独立问题，未放宽断言。

日志保存在本机：

- `/private/tmp/subscription-frontend-final.log`
- `/private/tmp/subscription-product-final.log`
- `/private/tmp/subscription-host-final.log`
- `/private/tmp/subscription-iframe-cached2.log`
- `/private/tmp/subscription-startup-final.log`
- `/private/tmp/subscription-fix-typecheck.log`
- `/private/tmp/subscription-rebase-validation.log`

## 下一轮线上验收

两端需要配套上线同一契约版本和 Skill。用上述五类需求及指标卡、趋势图、分组图的原生铃铛入口回放，记录模型时间、工具时间、配置轮数及最终规则；重点核对同环比原值/比较引用、当前筛选继承、协同空间归属、手动修改接续和异常后的恢复。在线耗时改善和实际投递正确性仍需这轮验收确认。
