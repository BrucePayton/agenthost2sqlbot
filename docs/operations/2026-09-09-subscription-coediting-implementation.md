# 订阅推送：人机接续编辑与保存闭环实施记录

## 范围与结论

基于当前本地代码落实人机共用七步配置表单的修复。修改涉及两个工程：

- `davinci`：`codex/collaborative-space-agui-implementation`，前端、Java 保存接口及回归测试。
- `claude_workspace_mvp`：`main`，正式契约、生成文件、实际浏览器加载的 embed 产物、Skill 与用例说明。

保留原有六个订阅工具，不新增独立草稿系统，不改 `CLAUDE.md`，不改 `davinc-data-mcp`。本次未提交、推送、部署，也未在 UAT 创建规则或发送消息。

**核心代码回归通过；完整前端回归有依赖阻塞；真实模型效果测试按用户要求跳过。** 代码回归不代表真实模型已经通过自然语言配置成功率、澄清轮次或响应耗时验收。

## 已落实的行为

1. **查看不等于修改。** 时间输入框关闭状态下不再因外部点击回写旧值；Agent 设置时间同步旧兼容字段。业务 revision 忽略触发配置规范化及 finalize 的 touched 状态。使用真实时间控件验证七步前后切换不改变配置或 revision。
2. **人机接续同一份表单。** 用户修改后，旧 revision 的 Agent 写入被拒绝；Agent 读取当前状态后只修改指定内容，不覆盖用户已改的时间和正文。revision 仅保留为内部并发保护，不要求用户管理版本。
3. **两个保存入口语义明确。** 新建规则由页面按钮保存为 `running`；Agent 保存为 `disabled`，即使用户在 Agent 确认框点击确认也一样。编辑已有规则不改变原状态。Java 创建接口校验显式状态，未传状态保持旧客户端的默认停用行为；请求启用时先做可运行校验。
4. **共用保存锁。** 保存中阻止另一入口并发写入或重复创建；确认期间用户修改或手动保存，Agent 原确认不能继续提交。明确业务拒绝恢复编辑；已保存或结果未知保持保护，避免重试产生重复规则。
5. **结果按可靠回执判断。** 两个创建入口共用回执检查，校验成功标识、规则 ID、归属及状态。HTTP 成功但回执缺失或不匹配不能宣称保存成功；后端明确拒绝保留业务原因。保存成功后页面返回失败也不能再次创建。
6. **操作与候选更准确。** 批量操作若刚写入的步骤内容被场景裁剪，整体拒绝并返回失败操作位置；不再先报告成功再丢配置。字段候选携带数据集级时间过滤约束，关键词只命中指标时也不丢失该约束；沿用候选上限与截断标识。
7. **Skill 与产物同步。** 使用 skill-creator 的精简原则，主 Skill 控制在 5142 字节，将细节保留在已有 references。补充接续编辑、保存状态和未知结果的处理要求；同步正式契约、Davinci 生成文件、Host 生成文件及实际提供的 embed.js，并更新启动一致性测试。

## 本地测试结果

各行存在覆盖重叠，不相加为唯一用例总数。

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 最新前端定向回归 | 14 suites / 335 tests 通过 | Controller、七步真实时间控件、Store、操作、候选、复核、保存接口、人机接续及确认期间竞争 |
| 订阅模块扩大回归 | 123 suites 通过，1375 tests 通过；7 suites 无法加载 | 7 个 Markdown/富文本套件均缺少本地 `@tiptap/core`，整个命令退出码为 1，不记为全量通过 |
| Java `MessageRuleServiceImplTest` | 97 tests 通过，0 失败/错误/跳过 | 实际执行 Maven 测试，含手动启用、Agent 停用、非法状态与启用前校验 |
| Host Python 定向回归 | 64 tests 通过 | 契约、工作区 Skill 约束、订阅工具模拟评估夹具 |
| Host JS 回归 | 154 tests 通过 | 桥接与协议等现有测试 |
| Agent 启动一致性 | 15 项契约同步 + 7 项 bootstrap 检查通过 | 检查配对工程与实际 embed 产物，不仅检查源码摘要 |
| Skill 检查 | 结构校验及 5200 字节预算通过 | 主文件 5142 字节 |
| 定向 TSLint / diff 空白检查 | 通过 | 新建回执检查、Store、Agent 保存、页面保存入口；两个工程 `git diff --check` |
| 完整 TypeScript 检查 | 未通过 | 当前项目/依赖诊断阻塞；本轮输出未匹配到改动的已跟踪文件，不能据此宣称全量类型检查通过 |
| 真实模型 Agent 测试 | 跳过 | 本地缺少 `ANTHROPIC_API_KEY`；用户明确没有配置并同意跳过，未继续寻找凭证 |

扩大回归未安装或升级依赖来绕过缺失问题，沿用开始实施时已有的 node_modules 链接。缺少的富文本依赖与完整类型检查仍需在依赖齐全的构建环境复验。

主要命令（在各自工程目录运行）：

```sh
# davinci/server
mvn -o -DskipTests=false -Dmaven.test.skip=false -Dtest=MessageRuleServiceImplTest test

# davinci/webapp
npm run check:agent-startup

# claude_workspace_mvp，使用已准备好的 Python 测试环境
python -m pytest tests/test_agui_contracts.py tests/test_workspace_registry_davinci.py tests/test_subscription_agui_simulator.py
npm run test:js
python scripts/generate-davinci-contracts.py --check
```

前端定向测试覆盖 `SubscriptionMessageRuleAgentFlow`、`SpaceMessageRuleAgentController`、`spaceMessageRuleOptions`、`api`、`draft/`、`StepActions`、`SubscriptionDraftStoreIntegration`、`NativeControls` 与 `saveMessageRule`。

## 性能与剩余验收边界

- 接续编辑并保存的确定性链路测试断言没有数据预览、查询取数和规则列表请求，创建请求只有一次。
- 配置阶段沿用元数据、静态校验及增量修改，不默认触发昂贵预览或试发；六工具不增加。
- 没有真实模型输出，因此不提供成功率、P50/P95 或“Agent 效果通过”的结论。
- `saveOutcome=unknown` 只表示无法确认是否保存；`get_context` 可观察但不会自动解除保护。该状态仍需核对原请求或后端保存记录，不能刷新后盲目重建。本次未引入后端幂等平台或自动对账任务。
- 发布时需配套部署 Java 保存接口、Davinci 前端与 Host 契约/embed 产物。旧后端固定停用或不返回可靠创建回执时，前端不能保证手动保存启用；不得以再次创建或额外启用请求掩盖版本不配套。

## 缺失修复恢复与复验（2026-09-09）

提交前发现上述 Davinci 修改的大部分已不在工作区，且不存在对应提交或 stash；本记录不推断是谁或哪条命令移除了修改。按用户“先恢复这些缺失修复”的要求执行：

- 从本轮先前 Jest sourcemap 中恢复 21 个前端实现/测试文件的原始源码，经逐文件差异检查后应用；Java、类型声明、保存提示从同一任务原始补丁恢复，未重新设计行为。
- 保留仍在工作区的 API、共享回执校验和启动检查修改。
- 当前 Davinci HEAD 为 `9203c616a`，它已使用 Host `34123ce` 的仪表盘契约；Host 因而从 `e0e712e` 快进到这一对应的已提交版本，再合回本轮订阅改动。没有把旧契约覆盖到仪表盘新能力上，也没有合入两端更晚的 AI 排序提交。
- Host 合回时仅两个生成产物冲突，正式源契约自动合并成功。重新生成契约；embed 只替换重新编译的 V2 契约模块，模块外内容保持对应 HEAD 原样，避免本机依赖版本差异带入无关变动。
- 通过现有 `--bundle space-message-rule` 严格同步，确认订阅以外的前端契约未改变。当前两端源摘要一致：`db7b7349ba95c93b41edff0dc0d0f2f6c9edde96185072182f6d3a79fb816816`。
- 恢复后重新执行：前端定向 14 suites / 335 tests、Java 97 tests、Host Python 64 tests、Host JS 154 tests 均通过；启动一致性 15 项与 bootstrap 7 项通过，定向 TSLint、生成检查与 diff 空白检查通过。
- 此轮未重跑此前受依赖阻塞的全量前端/类型检查；真实模型测试仍按用户要求跳过。没有提交、推送或部署。

恢复后的补丁和文件摘要另存于 `/private/tmp/subscription-restored-20260909.OWlZLO`，供后续提交前核对。Host 合并前的八个修改文件也保留在名为 `subscription-restore-host-alignment-20260909` 的 stash 中（对象 `0429d7fd27f69472846fa99d5b3cf8095dbd2d1b`）；它们已回放到工作区，不要再次 apply。
