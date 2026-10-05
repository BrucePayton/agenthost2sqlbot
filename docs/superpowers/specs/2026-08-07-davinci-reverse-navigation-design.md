# Davinci 反向导航与同轮仪表盘解读修复设计

## 背景与根因

现有 MVP 只验证了从仪表盘读取数据后导航到数据集。真实测试从数据集页面发出“帮我解读仪表盘”时暴露两个契约缺口：

1. `navigateTo` 的 JSON Schema 只要求 `destination`，但 Mock Host 对 `dashboard` 强制要求 `resourceId="1024"`，因此模型合法调用 `{"destination":"dashboard"}` 时收到 `TARGET_NOT_FOUND`。
2. 前端工具目录按 Run 起始页面固定。Run 从数据集页面开始时没有 `dashboard.capture_current_view`，即使导航成功，模型也不能在同一 Run 中继续读取仪表盘。

## 已选方案

采用稳定工具目录与动态执行权限分离：

- AG-UI Run 和 Claude SDK 在本 MVP 中始终声明 `navigateTo` 与 `dashboard.capture_current_view` 两个受控前端工具。
- `HostContext.supportedCapabilities` 继续表示当前页面立即可执行的能力；Mock Host 在执行时仍按当前路由拒绝非法 Capture。
- `navigateTo({destination: "dashboard"})` 在单仪表盘 Mock 中默认解析为 `/dashboard/1024`。
- 如果调用显式携带非 `1024` 的 dashboard `resourceId`，继续返回 `TARGET_NOT_FOUND`。
- 导航成功后，Tool Result continuation 必须携带父页面最新 `HostContext`。服务端先校验 ACK 的目标、路径和 `contextVersion + 1`，再原子更新当前 Run 的 Bridge Context，最后解除 Pending Future。
- 后续 `dashboard.capture_current_view` 使用更新后的 dashboard Context 做版本绑定，因此可以在同一个 Claude Run 内完成导航、读取和解读。

## 数据流

```text
Dataset HostContext v2
  -> User: 帮我解读仪表盘
  -> Claude: navigateTo({destination: dashboard})
  -> Host: /dashboard/1024 + UI_ACK v3 + Dashboard HostContext v3
  -> Tool Result continuation validates ACK and updates Bridge to v3
  -> Claude: dashboard.capture_current_view({})
  -> Host: Dashboard Snapshot v3
  -> Bridge validates Snapshot against v3
  -> Claude: completes interpretation in the original Turn
```

## 错误处理与安全边界

- HostContext 只能从与 Tool Result 同源、同 nonce、同 Session/Run 的 continuation 更新。
- 导航 ACK、HostContext 和 Snapshot 的 `contextVersion` 必须一致；不一致返回 `CONTEXT_STALE`。
- 数据集页面直接调用 Capture 仍返回 `CAPABILITY_UNAVAILABLE`。
- 未知 dashboard ID 仍返回 `TARGET_NOT_FOUND`。
- 不扩大 postMessage Origin、资源 ID、Session 所有权或 Tool Result 大小边界。
- 本修复不引入真实 Davinci 路由发现、动态仪表盘列表或跨 Run Handoff。

## 验收 Gate

1. 单元测试证明缺省 dashboard ID 可导航，错误 ID 被拒绝。
2. Bridge 测试证明合法导航 ACK 会把 Context 从 dataset v2 更新到 dashboard v3，随后 Snapshot v3 可完成。
3. 浏览器 Gate 从 `/datasets` 发出一次请求，验证 URL 切到 `/dashboard/1024`、同一 Session 不变、同一 Turn 依次出现 `navigateTo` 和 `dashboard.capture_current_view`，最终回复包含 `4,734`、`-10.88%`、`40,388,380`。
4. 现有 dashboard Capture、dashboard → datasets、过期 Context、伪造消息、超时与 iframe 移除 Gate 保持通过。
5. 可选真实 `qwen3.8-max` Gate 覆盖反向导航与同轮解读；仅在显式开关下产生费用。
