# 仪表盘枚举值核验：修复与验收

## 结论

需要修改现有工具的能力。复用前端已有的原生枚举接口，让 `dashboard.get_filter_field_options` 在指定字段时返回实际 `label/value`，并在 `dashboard.apply_widget_spec` 写入前校验真实值。没有新增服务、接口或同义词字典，也没有扩大目录检索总额度。

基本原则：字段描述用于判断“可能该用哪个字段”；实际枚举回执用于判断“应该写入什么值”。`source: tool_verified` 是模型提交的声明，本身不是证据。

状态：本地代码已实现并验证，未对线上仪表盘执行写入。线上实际枚举和模型完整重放仍需配套发布后验收。

## 这次会话发生了什么

会话：`5a9782ca-a2a0-4c61-b1d7-3f5fd89d8d90`。以下依据导出的实际工具调用与回执，不将模型的自述当作工具执行证据。

- 数据集为 `warehouseTopic:10`。`catalog.search_fields(query="交易方式")` 返回字段 `410`，描述为“订单所属交易方式”。这能确认字段，不能确认枚举值。
- 该轮没有调用枚举查询。随后 `dashboard.apply_widget_spec` 将 `value: ["上门"]` 与 `source: "tool_verified"` 一并写入；后续用户纠正后才改为 `["上门订单"]`。
- 旧 `dashboard.get_filter_field_options` 只返回筛选字段、操作符、`needEnumValues` 和现有筛选引用，确实不返回真实枚举。单改描述会夸大能力。
- `limit=3` 和 `maxMatchedFields=3` 控制返回数量，不是调用次数。当前 Host 默认允许每轮 4 次共享 catalog 检索；这次导出没有“第 5 次被拒绝”的回执，因此不能把模型所说的余额为零认定为服务端已拦截。

本次没有带用户登录态查询线上字段 `410` 的实际枚举；不能仅因后续输入了“上门订单”就把它认定为生产接口已确认的存储值。

## 修改后的工具流程

1. 通过 catalog 或当前组件确定数据集与字段。
2. 对支持原生枚举的字段，调用现有工具读取实际候选，支持尚未创建组件的数据集：

```json
{
  "datasetUid": "10",
  "datasetType": "warehouseTopic",
  "fieldId": "410",
  "query": "上门",
  "limit": 20
}
```

3. 根据返回的 `enumValues.items` 选择业务匹配项，写入其 **value，保留原类型**。例如接口返回 `{ "label": "上门订单", "value": "上门订单" }` 时才写入 `"上门订单"`；若真实 value 是数字，就写数字。此处只是行为示例，不预设生产值。
4. 有多个合理业务候选时需要澄清；查询为空或失败时不得猜值。`exhaustive: false` 表示上游有返回上限，不能据此断言某值不存在。`nextCursor` 仅翻页本次接口返回的候选。
5. `apply_widget_spec` 对原生枚举的等值、排除和集合筛选，在 dryRun、探数和保存之前调用同一枚举接口核验。不存在匹配回执时返回 `ENUM_VALUE_UNVERIFIED`；接口失败返回 `ENUM_VALUES_UNAVAILABLE`，本次不会保存。

字段来源使用授权数据集元信息，并与最终编译配置保持一致。日期快捷表达式、文本包含等条件仍按其原有语义处理；未标为原生枚举的字段返回 `FIELD_NOT_ENUM`，不强行当作有限字典。

枚举读取复用 `fetchFieldValues` → `/api/v3/customDashboard/widget/datasetFieldEnumValues`（历史组件仍沿用原有兼容路径）。它不占 catalog 发现额度。Host 提示和耗尽提示已明确区分目录发现与已知字段的取值核验，原有重复调用与并发保护保留。

## 验收清单

在包含配套 FE、Host 契约及 Host embed 构建产物的环境，新建会话并输入原请求。检查工具调用和最终保存配置：

| 场景 | 预期 |
| --- | --- |
| “上门”存在唯一业务匹配候选 | 先读取字段枚举，配置写入回执的真实 value，不直接写用户简称 |
| 候选包含不同业务类型 | 模型列出业务差异并澄清，不把所有候选自动加入筛选 |
| value 为数字 0 或其他数字 | 查询、配置及回执保留数字类型 |
| 模型伪造 `source: tool_verified`，value 无匹配 | `ENUM_VALUE_UNVERIFIED`，没有保存、没有探数 |
| 枚举接口异常 | `ENUM_VALUES_UNAVAILABLE`，没有保存；不能假装核验成功 |
| 目录额度耗尽但数据集、字段已知 | 仍可调用枚举读取工具，不需要增加 catalog 额度 |
| 目标数据集尚无组件 | 仍能读取枚举候选 |

工具硬校验保证值有实际枚举依据；候选是否符合业务含义仍由模型判断或用户澄清，不能把“存在这个值”当成“业务选择必然正确”。本次写入拦截覆盖标准 `apply_widget_spec` 路径。

## 本地验证

| 验证 | 结果 |
| --- | --- |
| 枚举控制器、原生 API 适配器回归 | 44 条通过，覆盖伪造值、数字值、上游失败、分页、来源及创建前查询 |
| Host runtime 与契约回归 | 190 条通过，含 catalog 耗尽后的枚举调用与 schema 校验 |
| `check:agent-startup` | 通过；包含 FE 258 条、Host 90 条、实际双 Origin iframe 2 条，以及生成文件/bundle 检查 |
| 扩展筛选与图表交互回归 | 118 条通过，1 条存量富文本测试受旧 jsdom/Enzyme 环境阻塞；双开联动/明细菜单用例通过 |
| 扩展 Host workspace 回归 | 204 条通过，1 条存量订阅 Skill 预算失败：6908 字节超过 5200；HEAD 内容也为 6908，本次未修改该 Skill |
| 静态检查 | 两仓 `git diff --check` 通过；受改文件未新增 TypeScript/TSLint/Ruff 诊断 |

没有升级依赖、放宽契约或预算断言来掩盖存量问题。相关本地日志位于 `/private/tmp/enum-focused-final.log`、`/private/tmp/enum-host-green.log`、`/private/tmp/enum-agent-startup.log`、`/private/tmp/enum-fe-regressions.log`、`/private/tmp/enum-host-final.log`。

## 发布范围

FE 控制器与生成契约、Host 源契约与生成文件、Host runtime 指引、重建后的 `embed.js` 需要配套发布。契约唯一源为 Host `contracts/davinci-agent-v2.json`，不能手改摘要。

同时更新了仓库内 `locate-data/references/tools.md` 的枚举用法；若目标环境使用数据库导入的 Skill 版本，按现有 Skill 发布流程更新引用文件。旧会话可能保留旧快照，验收使用新会话。本次没有操作远端 Skill 或服务。
