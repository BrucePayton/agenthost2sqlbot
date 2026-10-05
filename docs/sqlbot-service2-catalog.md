# 服务2 StarRocks 数据源目录接口

本机 AgentHost 通过服务2 `http://10.193.65.41:8002` 的管理接口读取已配置的数据源。接口只返回 DW、DM、RPT 三组已勾选的表及字段；表备注和字段备注优先使用 SQLBot 中导入的自定义备注。数据库连接配置、SQLBot 管理凭证和签名密钥不会返回给调用方。

本机访问入口为 `http://127.0.0.1:8765`：

| 接口 | 用途 |
| --- | --- |
| `GET /api/sqlbot/catalog` | 三个数据源的名称、状态、已选表数量及与配置清单的一致性 |
| `GET /api/sqlbot/catalog/{dw\|dm\|rpt}/connection` | 通过服务2实时检查 StarRocks 连接 |
| `GET /api/sqlbot/catalog/{dw\|dm\|rpt}/tables` | 已勾选的表和表备注 |
| `GET /api/sqlbot/catalog/{dw\|dm\|rpt}/tables/{table_name}/schema` | 已勾选的字段、字段备注，并与实时数据库 Schema 对照 |

最后一个接口默认进行实时 Schema 查询。传入 `?live=false` 可仅读取服务2保存的字段配置和备注。`live=true` 时，每个字段增加 `in_live_schema`、`live_type`、`live_comment`。

例如：

```sh
curl 'http://127.0.0.1:8765/api/sqlbot/catalog/dw/tables/dw_centre_inspection_report_info/schema'
```

部署配置位于 `.runtime/starrocks-poc/catalog-api.env`（权限 `0600`），数据源 ID 映射为 DW=4、DM=5、RPT=6；完整元数据清单位于 `.runtime/starrocks-poc/manifest.json`。目录接口单独启用，动态问数功能保持关闭，直到服务2能访问宿主的 Ticket 回调地址。
