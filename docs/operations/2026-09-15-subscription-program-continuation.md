# 订阅配置程序接力验收（2026-09-15）

本轮将明确需求后的连续动作从模型往返中移出，保留正文“正在配置/已配置”、原生页面切换及人工纠正。代码涉及配对 Host 与 Davinci；未部署，也未执行发送预览、保存或启用。

## 实现范围

- 复用 `space.message_rule.get_context.configurationIntent` 接收部分或完整的业务意图；所有业务路径先回显需求，时间明确即可先配置，不等数据引用齐备。
- Host 的 `subscription_executor.py` 仅选择下一次现有原生工具和编译已核验操作。使用当前原生回读、可信候选、任务标识及版本，不建立第二份可写配置或通用工作流 DSL。
- 程序路径在创建 SDK client 之前返回。工具仍经过既有 Hook、权限/版本检查、持久化账本、Bridge 及原生 Store；正文进度由真实派发和回执产生。
- 明确空间、固定接收对象、仪表盘及原生 AI 能力可按唯一可信候选继续。目录截断、同名来源、业务日期/指标/粒度、动态员工映射等缺口交回模型核验，禁止猜测。空间目录当前不自动翻页。
- 必填标题依据原生校验公布；首次准备内容就补已知名称标题。部分业务缺口只暂停受影响操作；全部七类内容继续使用现有组件结构。
- 图表日期/比较差异复用真实查询引用、当前筛选和比较能力；保留原值及其他查询。修正比较变量的公共回读，失配时阻止错误交付。
- 用户新输入暂停旧意图，在途工具身份保留；时间 A→B→A 可以正确重写，已完成的不相关内容不重复覆盖。程序和 SDK 工具回执明确区分；真正 SDK 待回结果在恢复后消费。
- 业务识别齐备、无未决项且当前任务/版本的原生 finish 成功后，直接展示实际总结。指引用户在“命名并保存”右下角手动发送预览并保存。
- 修正真实富文本编辑器的换行丢失及显示同步触发虚假 onChange，保留真实输入与版本冲突保护。

## 本地验证

| 验证 | 本轮结果 |
| --- | --- |
| 契约两端生成、Host embed 构建 | 通过，保留既有严格 schema 与 32KB 单工具上限；重复定义改用本地引用 |
| 完整 `check:agent-startup` | 通过：7 套 258 项前端、90 项 Host、真实双 Origin iframe 启动与回执 |
| Controller 与候选能力 | 80 项通过，包括当前 taskId、原生必填字段、AI 组件能力及旧回执隔离 |
| Host 运行时、执行器、账本、服务、持久化与 Runner | 359 项通过；覆盖新建草稿换任务、执行中补充需求、业务未完的结构 ready、恢复和旧协议兼容 |
| Host 全部 JS 测试 | 165 项通过 |
| 正文进度、人工切页、刷新恢复 | 3 项浏览器测试通过，正文消息不折叠为“已工作”，不抢手动导航 |
| Host 程序＋真实浏览器 Controller/Store | 3 项通过：通知、截图＋AI＋跳转、昨日成交量及日环比预警 |
| 原生比较引用与编辑器 | 32 项 Jest 和真实 Chromium 通过，见 Davinci 对应验收记录 |

真实程序链路测试使用明确的业务意图夹具，并禁止创建模型 client。三个场景都由生产 Host 续步逻辑执行真实原生工具，直到实际 `completion.status=ready`；验证 `taskId/revision` 一致、`saved=false`、`dataVerified=false`、只出现一次真实总结。通知及仪表盘先配置时间；预警保留原值、区域筛选和另一份独立查询，条件与正文变量分别绑定正确输出。上游资源和服务端静态校验使用隔离夹具，未访问业务数据或发送接口。

原生编辑器细节见配对 Davinci 的 `docs/subscription-native-editor-validation-2026-09-15.md`。临时补齐测试环境已声明的 Tiptap 依赖，未改项目依赖或锁文件。

本轮同步还发现 Host 唯一契约源与 Davinci HEAD 生成物已有差异：布局回执的 executionMode、remoteStatus、fallbackReason 三个字段缺失。已将 Davinci HEAD 的原有严格定义补回唯一源，再统一生成；未改布局算法，也未放宽未知字段检查。完整启动门禁随后通过。

## 复跑与证据

本地日志：`/private/tmp/subscription-program-startup.log`、`/private/tmp/subscription-program-controller.log`、`/private/tmp/subscription-program-js.log`、`/private/tmp/subscription-program-iframe.log`。这些临时日志不进入源码提交。

Host 最终回归日志为 `/private/tmp/subscription-program-host-final.log`。集中恢复测试经过实际 AGUI 路由、数据库恢复、TurnService、容器 worker 请求构造、Runner JSON 和 RuntimeRequest，验证服务端 origin 不被客户端覆盖、容器传递订阅检查点，以及仅真实 SDK 回执进入模型工具结果。两个连续的非订阅页面轮次验证 SDK 回执消费结果也会持久化，避免重复提交并保留原业务任务。Runner 使用可选窄字段 `subscription_task`，空检查点不改变旧请求序列化。

Host 程序与原生浏览器：

```sh
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=<本机已安装Chromium路径> \
  /private/tmp/agui-validation-venv/bin/python -m pytest -q \
  tests/live/test_subscription_program_continuation.py --tb=short
```

配对完整启动门禁在 Davinci 的 `webapp/` 运行：

```sh
DAVINCI_HOST_ROOT=<Host绝对路径> \
DAVINCI_HOST_PYTHON=<已安装依赖的Python路径> \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=<本机已安装Chromium路径> \
  npm run check:agent-startup
```

## 验证边界

本轮没有运行真实模型、线上数据 MCP 或用户真实资源验收。因此已验证的是“明确意图后的原生续步无需再调用模型”，不能据此宣称线上端到端耗时、首轮理解成功率或实际消息数据已达标。容器/持久化采用本地协议和服务测试，未部署容器环境。目标环境上线前需要配对更新两个仓库及实际 bundle，并核对原生配置后手动预览。
