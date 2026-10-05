# 订阅原生工具改造验收记录

日期：2026-09-17。本轮修改 Host、Davinci 与仓库 Skill；提交和推送按用户后续授权执行。未发布托管 Skill、部署或重启服务。原生数据预览、发送预览、保存、启用和业务发送均未执行。

## 实施范围

- 模型直接使用五个现有 space.message_rule 工具；get_context只读，撤出统一入口及全局业务计划编译/续跑。删除五个旧运行模块，迁移有价值回归后退休十三个旧架构测试。
- Host仅保留原需求/补答/可信候选/原生当前回读、真实SDK回执与正文进度。过期task/revision或换空间后的结果不作为当前事实，不重放旧计划。
- 原生工具首批补缺少的必填标题/名称，明确值、模板、锁与人工编辑优先；components为正文主表达，兼容快捷参数严格校验。保留原生all、no-op、多查询和原值/比较输出身份。
- Skill统一原生流程：回显、独立确定项先填、定向查资源、依赖成批配置、按真实回读总结、交接用户在“命名并保存”预览后保存。新增五条完整参数路径并与正式Schema校验。
- 实际入口测试发现并修复既有SpaceHome/Detail缺失fetchSpaceMemberPage导出：通过现有成员接口进行有界分页适配；没有mock掉该生产模块。
- 工具契约、两端生成物与Host embed已同步；原生约束outputKey需queryKey同步进正式Schema。不得依靠模型反复试错发现已知格式规则。

## 已执行验证

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| Host核心及订阅/契约综合回归 | 362通过；后续输出别名约束另作针对性补验 | /private/tmp/subscription-native-root-regression.log |
| 聊天前端JS | 197通过 | /private/tmp/subscription-native-js-final.log |
| Davinci原生Controller/Flow/Operations/API相关 | 364通过；最终Schema相关Controller 69、Flow 23另验通过 | 原生侧分组日志，见下方最终核验 |
| 原生隔离浏览器回归 | 10通过，14.25秒；输入及成功输出均经过真实前端validateSchema | /private/tmp/subscription-native-direct-browser.log |
| 正文进度刷新恢复 | 1通过 | /private/tmp/subscription-native-body-progress.log |
| 真实配对页面阶段联动/人工切页 | 1通过 | /private/tmp/subscription-native-body-navigation.log |
| 完整check:agent-startup | 通过，含260项前端、94项Host及2项真实iframe测试 | /private/tmp/subscription-native-agent-startup-escalated.log |
| 最终契约/五场景参数示例 | 11通过，4.63秒 | /private/tmp/subscription-native-final-schema.log |
| 负责范围完整单测 | 176通过、1项既有dashboard Skill预算失败 | /private/tmp/subscription-native-owned-unit.log |
| 五场景自然语言、真实模型 | 5次均未通过，未进入配置阶段 | /private/tmp/subscription-native-nl-0917/ |

原生浏览器覆盖：指定空间从个人上下文真实list/open、首次默认标题、纯通知格式/群、仪表盘内容、日期筛选、原值/日环比独立输出、异常订单动态员工、同源多query、人工修改、no-op和53列引用。它使用真实Controller/Store/Bridge，资源与校验服务由隔离上游fixture提供，不证明生产数据或实际消息效果。

正文测试使用真实Host bundle及配对父页面；首次因默认仓库路径不同跳过的联动项，已指定当前Davinci路径补跑通过。首次完整启动在沙箱内浏览器启动被拒，取得本机浏览器执行权限后重跑完整门禁通过；没有跳过或修改断言来放行。

## 真实模型结果

五场景通过独立进程、数据库和浏览器并发执行，使用配置中的同一模型；没有固定模型回答或预填最终配置。每份报告记录实际契约、Skill、runtime哈希。SDK初始化后没有收到任何模型正文、思考或工具调用，约60秒的无进展保护触发；整个用例还包含初始化/收尾耗时。

| 场景 | 总耗时 | 首次模型内容 | 首次配置/完成 | 结果 |
| --- | --- | --- | --- | --- |
| 固定群通知 | 66.328 秒 | 未收到 | 未进入 | SUBSCRIPTION_NO_PROGRESS |
| 仪表盘截图/AI/跳转 | 66.174 秒 | 未收到 | 未进入 | SUBSCRIPTION_NO_PROGRESS |
| 定时四列表格 | 65.525 秒 | 未收到 | 未进入 | SUBSCRIPTION_NO_PROGRESS |
| 指标原值/日环比预警 | 66.116 秒 | 未收到 | 未进入 | SUBSCRIPTION_NO_PROGRESS |
| 异常订单/动态负责人 | 67.43 秒 | 未收到 | 未进入 | SUBSCRIPTION_NO_PROGRESS |

不能把上述耗时解释成找数、绑定或原生工具变慢，也不能宣称端到端成功或性能改善。modelTurns缺少usage记录，保持未知，不伪写成0；实际工具调用为0。独立最小请求也未收到HTTP响应头：同provider/model、无工具无Skill的“只回复OK”请求提权后15.7秒ConnectError；无鉴权连接检查5.065秒ConnectTimeout，异常链含SSLWantReadError，无代理环境变量。SDK启动成功不代表模型API已连通。证据将当前阻塞定位到HTTP响应前的连接/TLS阶段，尚不能区分网络链路与服务端握手故障。

## 已知基线与未覆盖边界

- 工作区全量Skill预算检查仍受既有configure-dashboard-widget/SKILL.md影响：HEAD与当前均6601字节，门槛6144。本轮未修改该文件或扩大预算；订阅Skill、CLAUDE.md及locate-data保持各自预算。此基线失败不与本轮回归通过混算。
- 本地Skill不是线上已发布版本；旧冻结会话明确提示升级，仍保留页面配置。真实上线需配对发布Host/Davinci，并确认新会话实际Skill版本。
- 默认规则名仍由服务端校验唯一性；若重名，仅修名称并重新finish。用户明确指定名称冲突需业务确认，不重建或重查全部数据。
- 未实际验证生产目录/ACL全量资源、真实数据结果、飞书消息展示、手动发送预览、保存或启用。

## 最终核验

- 最终契约摘要为 `04da91f881357248c2b2c5559e0d93770c50bee96cb7c9c94ec60484ffe560af`。bind/upsert的outputKey依赖使用两端均支持的等价Schema表达；不扩充前端校验器。
- 五例模型失败报告使用此前的 `dc324dc...` 契约版本；后续仅修正别名参数依赖及示例，因连接/TLS仍不可达，未再次启动五例空等。失败报告保留原指纹，不冒充最终版本成功验收。
- 本轮修改后的三个入口文件均在既有预算内：CLAUDE.md 4996/5000字节、订阅Skill 5145/5200、locate-data 5579/5600。保留无关未跟踪文件。
- 最终摘要的完整启动检查通过：同步15项、构建1项、Davinci 260项、Host 94项、真实iframe 2项；两端生成检查与diff检查通过。最终原生浏览器10项通过，同时校验两端Schema；普通指标、有效别名、upsert/bind缺queryKey负例全部覆盖。
