# 工具参数速查

数据检索参数（`analytics.*` / `catalog.*` / `access.*`）见 locate-data Skill 的 tools.md，本文件不重复。

## 消息与预警

- `dashboard.open_data_alert_config`: `{"widgetId":"10878"}`。只接收 widgetId；数据、筛选和身份来自可信页面。成功仅表示打开个人预警草稿（空间仪表盘也一样）；随后用订阅 Skill 的 get_context 接续原草稿，不重复创建。

## 原子创建和配置

可执行示例：用户明确要求最近 30 天，数据集和字段已由当前工具验证。

```json
{
  "create": {"chartType": 3001, "title": "近30天各城市奢侈品门店成交额"},
  "spec": {
    "dataset": {"datasetUid": "662", "datasetType": "warehouseTopic"},
    "metrics": [{"fieldId": "27068", "agg": "sum"}],
    "dimensions": [{"fieldId": "27119"}],
    "filters": [
      {"fieldId": "27121", "operator": "eq", "source": "user_stated", "valueExp": "last_days", "value": ["30"]}
    ],
    "sort": {"fieldId": "27068", "direction": "desc"}
  }
}
```

配置已有 Widget 时用 `"widgetId":"10814"` 替换 `create`。首次不确定时可加 `"dryRun":true`；验证通过后再用同一 spec 正式写入。发生版本冲突时读取最新上下文，把 `expectedResourceRevision` 更新为已观察到的版本，禁止盲重试。写入 `spec` 的 metrics/dimensions/filters **只传写入字段（fieldId/agg/operator/value/valueExp/direction 等），不要携带读回 `effectiveSpec` 里的 `name`/`type`**——写入校验会拒绝多余键（`INVALID_ARGUMENT: $.spec.filters[0].name is not allowed`）。

`limit` 仅排行榜（`"chartType": 11001`）可用；其他图表不要传。写成功返回的 `appliedSpec` 就是生效配置；`dashboard.get_widget_config` `{"widgetId":"10878"}` → `data.effectiveSpec` 只在 partial/warnings/核对他人改动时使用；一次要看多个组件时用 `dashboard.get_widget_config` `{"widgetIds":["10305","10306","10307"]}`（上限 30），结果在 `data.widgets[]`，不要逐个调用。读回数据：`dashboard.get_widget_data` `{"widgetIds":["10878"],"maxRows":20,"waitUntilSettled":true}`，行数看 `data.widgets[i].metadata.rowCount`（页面缓存行数，`metadata.total` 存在时才是查询总数）；`rows.length`/`maxRows`/`returnedRowCount` 都不证明 Top N。

改样式时用 `dashboard.get_widget_edit_capabilities` `{"widgetIds":["763","10287"],"sections":["appearance"]}`；只有要新建组件时才传 `"includeCreationOptions":true`。

### 分组趋势的回执

`dimensions:[{"fieldId":"339"},{"fieldId":"15124"}]` 表示日期横轴、战区分组时，
`appliedSpec.group` 应为 `{"enabled":true,"fieldId":"15124","name":"战区","type":"varchar"}`
（name/type 以真实字段元数据为准）。`get_widget_config` 的单条和批量 `effectiveSpec`
返回同形分组状态。`group:null` 表示未配置；旧回执未含 group、仅含两个 dimensions，
或探针返回多战区数据行，都不能证明渲染器已配置多条线。输入 `spec` 不接收回执的 group。

## 布局读写

`dashboard.get_structure` 用 `{"pageSize":100}` 开始，存在 `nextCursor` 时继续传
cursor，并保持同一 resourceRevision。它返回根组件和容器子组件；只有
`coordinateSpace:"root",layoutEditable:true` 可进入根画布布局写入。
`dashboard.set_widget_layout` 单次 items 最多 30 项，传读到的
`expectedResourceRevision`。写入回执可能列出最多 200 个全看板归一化变化；明细
截断时根据计数和 `readbackAction` 重新分页读取结构。
