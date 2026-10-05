# 订阅组件种子续配验收证据（2026-09-14）

本次原生确定性验收通过；真实模型验收未通过，停在首次模型响应之前。不得据此宣称模型已经正确续配、真实业务数据正确或真实用户验收通过。

## 验收边界

- 测试入口：`tests/live/test_subscription_widget_continuation.py`；浏览器构建入口：`tests/live/build_subscription_native_parent.cjs`。
- 真实部分：当前 Host ASGI、当前 DeepSeek 模型配置、浏览器内 Davinci Controller、Handoff、DraftPort、Store、原生解析/引用/配置回读/静态校验/finish。
- 替身部分：仪表盘、组件和动态模板资源，上游数据集与创建参数校验，以及编辑器的导航回调。未验证完整页面视觉、Bridge 双 Origin 或线上业务接口。
- fixture 在固定 `2026-08-01`、仅原值的组件种子上，额外加入区域筛选及第二份独立历史查询，用于检查保留性；不是原失败会话的逐字节重放。最终离线验收身份为虚构数字 `900001`，满足原生归属字段要求。
- 预览、业务查询、持久化和确认保存接口均禁止调用；浏览器外部网络被拦截。模型仅获得实际工具契约和原生回执，不获得测试中的预期 patch。

## 实际执行结果

| 验收 | 结果 | 证据 |
|---|---|---|
| 真实浏览器内确定性续配 | 通过，最终运行 `1 passed in 1.37s` | 固定日期改为滚动昨日；已有有效引用在 `metadataReady=false` 时可用；原值保留并新增日环比差异率；区域与第二查询保留 |
| 阈值与变量语义 | 通过 | `10000` 绑定原值、`-0.1` 绑定日环比，同一 AND 组；正文两个占位绑定同查询的对应输出。故意交换阈值或正文绑定，验收能检出失败 |
| finish | 通过 | `ready`、回执 revision 等于草稿 revision、`saved=false`、`dataVerified=false`；草稿仍 active、disabled，导航到 finalize；未重新搜索 dataset/field，未查询、预览或保存 |
| 真实模型经 Host 首轮 | 阻塞 | `deepseek-v4-pro-0813`、low；合法 Host 请求耗时 `62.195s` 后返回 `SUBSCRIPTION_NO_PROGRESS`；总耗时 `64.445s`；1 轮、0 前端工具、0 runtime 工具、无正文 |

这次模型失败是首响应前无活动超时，不能等同于原失败会话中种子成功返回后约 180 秒未继续调用工具。未看到 Host 的有效模型结果，也不能只根据这份报告断言网关返回了 502。已停止外部尝试，没有切换模型、放宽证书校验或注入答案后重试。

此前的 harness 初始化认证及非法 `viewMode` 调整均发生在模型请求前，已修复。真实模型侧仅完成一次合法 Host 执行尝试。

脱敏报告：`/private/tmp/subscription-widget-live-evidence/subscription-live-evaluation.json`。报告仅保留业务输入、正文、工具调用/原生结果、计数与错误；不保留密钥、provider URL、原始思考或 SDK stderr。最终测试还会输出 `native-widget-smoke.json` 至 pytest 临时目录。

## 复跑基线

以下为最终离线验收的源文件 SHA-256；真实模型报告记录的主 Skill hash 与下列主 Skill 一致。后续运行 harness 会在开始时自动记录全部 Skill 引用、契约与 fixture hash。

| 文件 | SHA-256 |
|---|---|
| `contracts/davinci-agent-v2.json` | `fa425a9a7a5e1c1ac706af84ce672c19c1b975f7b0167f16b38c17c0d0554fe8` |
| Davinci `subscriptionWidgetComparison.json` | `2371700c67ca00270c646153dfde751ab0ad2ac28b977dd92aa3214c5f513f8b` |
| `configure-subscription-rule/SKILL.md` | `fd4253b58837af2db46c111a152aadd172d51ae2e9e6bbdb891983c1b6449b10` |
| `references/state-and-operations.md` | `294c3f7cb6a68fc4ebd68bb79e9df884646706acedd699eaaad818460657969b` |
| `references/workflow.md` | `ca02cd0541faeb89cc38eae66a8180df095e09cf0f6f4b17f00a3b41ade8d68e` |

## 运行方式

离线 smoke 不调用模型；真实模型测试默认跳过，必须显式 opt-in。模型固定读取当前受管 workspace，认证仅复用标准环境变量或用户现有 Claude provider 配置。总上限 14 个 Host 轮次、900 秒、750,000 输入 token 与 30,000 输出 token；保持单次决策上限 180 秒、默认无活动上限 60 秒。

```sh
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=<已安装 Chromium 路径> \
  /private/tmp/agui-validation-venv/bin/python -m pytest -q \
  tests/live/test_subscription_widget_continuation.py

RUN_LIVE_SUBSCRIPTION_WIDGET_CONTINUATION=1 \
SUBSCRIPTION_LIVE_REPORT_DIR=/private/tmp/subscription-widget-live-evidence \
PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=<已安装 Chromium 路径> \
  /private/tmp/agui-validation-venv/bin/python -m pytest -q \
  tests/live/test_subscription_widget_continuation.py::test_real_model_finishes_widget_continuation_without_sending
```
