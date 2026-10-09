# 07D：服务2供源、本机 SQLBot 执行联调

## 2026-10-09：10 题回归及仅返回图表类型

当前 Data Agent 只返回数据和 SQL 生成阶段给出的图表类型，不再调用图表生成模型或加载 G2 绘图。
本地 SQLBot 查询连接已分离连接/读取超时为 10/30 秒，避免沿用连接检查的 10 秒读取阈值。
服务2仍为 `10.193.65.41:8002`，未切换为 18000。完整验证及额度阻塞事项见
[10 题回归记录](sqlbot-regression-10-20261009.md)。下文图表绘制说明为历史行为。

## 2026-10-09：8765 Docker 入口恢复与配置归并

当前 Docker 入口为 `http://127.0.0.1:8765/data-agents`，配置唯一来源为
`.env.docker.local`。启动器生成 API/Worker 环境和清单挂载，不再加载旧的
`catalog-api.env`、`catalog-compose.env`、`catalog-override.yaml`、`api.env`、
`docker.env`、`local-docker.env`（以上均指 `.runtime/starrocks-poc/` 下的旧文件）。
旧文件已移出启动路径，只在 `.runtime/docker-web/retired-config-backup/` 保留回滚归档。
根目录 `.env` 和 18765 的 `service2-local-sqlbot.env` 属于其他已有入口，继续保留。

原本地 SQLite 有四个 Agent，而 Docker PostgreSQL 只有 RPT 与三源联合两个。
已从 SQLite 恢复缺失的 DW、DM 定义及绑定，保留其 Host ID 和已有 SQLBot
高级小助手 ID；已有两条 Docker 记录不被覆盖。四条记录均重新同步至本地
SQLBot，其回调统一指向 `http://host.docker.internal:8765/api/sqlbot/datasources`。
恢复前数据库快照和恢复证据位于 `.runtime/docker-web/restore-four-agents/`。
历史会话、结果与 Ticket 未迁移，SQLite 原数据未删除。

已验证三组数据集、3318 个字段、服务2的 23/19/8 张选表以及高级小助手回调可达。
以下各节是先前联调记录，其中的端口与配置路径不代表当前 Docker 启动配置。

2026-09-30 已打通流程图 07D 的第 9～14 步。隔离宿主运行在 `http://127.0.0.1:18765`，本机 SQLBot 位于 `127.0.0.1:8000`。服务2 `10.193.65.41:8002` 只提供所选表、字段和自定义备注；宿主实时读取并与完整 50 表清单核对，再结合宿主本地的 StarRocks 连接配置，组装 SQLBot 的动态数据源回调。服务2不反向访问本机，也不从其目录接口读取数据库密码。

验收记录：

| 场景 | 元数据范围 | 本机 SQLBot Record | 查询结果 |
| --- | --- | ---: | ---: |
| DW 单源 | 23 表、1400 字段 | 3 | `dw_centre_inspection_report_info`：273579281 |
| DM 单源 | 19 表、1384 字段 | 4 | `dm_centre_app_inspection_detail`：78265987 |
| RPT 单源 | 8 表、534 字段 | 5 | `rpt_centre_efficiency_staff_attendance_fs_summary`：74864 |
| DW+DM+RPT 三源 | 50 表、3318 字段 | 6 | RPT 上述表：74864 |

四次真实提问均返回 HTTP 200、SQL、Record ID 和一行结果；宿主记录了 8 次成功的动态源回调，结果缓存可通过 `/api/data-agents/{agent_id}/results/{result_id}` 读取。无效 Ticket 返回 HTTP 401。服务2目录若与清单中的已选表或字段不一致，回调返回 `sqlbot_catalog_mismatch`，不会静默回退到本地元数据。

运行配置位于 `.runtime/starrocks-poc/service2-local-sqlbot.env`（仅本机、权限 600）；`scripts/run_service2_local_sqlbot.sh` 启动/恢复 `agenthost-07d` 容器，绑定 `127.0.0.1:18765` 并设置为 `unless-stopped`。代码使用 `DATA_AGENT_STARROCKS_METADATA_SOURCE=service2` 开启实时供源，`SQLBOT_CATALOG_*` 指向服务2，`SQLBOT_*` 指向本机 SQLBot，`STARROCKS_*` 仅由宿主保存。完整清单为 `.runtime/starrocks-poc/manifest.json`，与服务2选表一致；先前的 `local-manifest.json` 每组只有 1 表，不适用于本次全量验收。

本次已验证动态源元数据覆盖全部 50 表，并对四个提问执行了真实查询；其余表的逐表问数、跨源关联及用户级库表字段/行权限不在本次验收范围。


## 2026-09-30：恢复本地 18080 入口

本地 `uv run --frozen workspace-agent` 使用根目录 `.env`，监听 `18080`。此前这个入口加载 `knowledge_mysql` 配置及本地 `data/app.db`，而 07D 容器使用 `starrocks_poc` 和 Docker PostgreSQL；因此数据集与 Agent 列表为空并不表示原数据丢失。

已将根目录 `.env` 配置为读取完整 StarRocks 清单、实时访问服务2 `http://10.193.65.41:8002`，并使用本机 SQLBot `http://127.0.0.1:8000`。保留本地模拟用户 `159358` 和既有 Workspace 配置。页面入口为 `http://127.0.0.1:18080/data-agents`。

从原实例恢复当时仍存在的两个 Agent 定义及四条数据集绑定，保留 Host Agent ID，所有者映射到本地用户；分别创建独立的 SQLBot 高级小助手，回调 `http://host.docker.internal:18080/api/sqlbot/datasources`，避免改动 18765 实例的原绑定：

- `运营中心业务测试- RPT运营报表助手`：Host ID `5499f2ba-32a4-44b6-b31b-b53a04c96b6b`。
- `07D 三源联合问数验收`：Host ID `7ed0a89e-fe7c-489a-82a4-d44d0f30b5ac`。

原实例及其历史会话、结果、审计仍保留在 Docker PostgreSQL，本次仅恢复 Agent 定义与数据集绑定到本地 SQLite，不迁移或复活历史 Ticket。恢复前的配置、SQLite 快照及源 Agent 定义保存在 `.runtime/local/restore-07d-20260930/`；其中含敏感配置，不应提交版本库。

验证：18080 返回三组数据集、3318 个字段和两个已发布 Agent；服务2实时目录对应 23/19/8 张已选表，均与清单一致。通过三源 Agent 发起真实 COUNT 问数，返回 HTTP 200、SQLBot Record 13、一行结果；无效 Ticket 返回 401。原 18765 两个 Agent 的 SQLBot 绑定未改动。验证结果保存在上述备份目录的 `verification.json`。本次测试数值只代表查询时点，不应当作固定断言。


## 2026-09-30：数据 Agent 问数过程展示

18080 的数据 Agent 问数页面新增流式问数入口 `/api/data-agents/{id}/ask/stream`；原 `/ask` JSON 接口继续可用。SQLBot 的选源、SQL 生成、查询成功、图表生成、简要说明与模型输出实时转发；若上游返回 reasoning_content，也按阶段展示。完成后读取 SQLBot `/chat/record/{id}/log`，展示关键词、术语、SQL 示例、自定义提示词、选表、生成 SQL、执行查询、生成图表等实际存在的步骤，以及提示词、输入输出、耗时、Token 用量。没有产生的步骤或思考内容不虚构。

结果支持数据表、单指标卡、G2 柱/条/线/饼图、SQL 复制、当前数据 CSV 导出和数据依据；可按需发起数据分析、预测、推荐问题。每次后续操作重新校验当前用户与结果归属，并签发独立短时 Ticket；浏览器不接触 SQLBot Token、数据库密码或 Ticket。预测数据单独标注为模型预测，不与真实查询结果混淆。停止接收会取消宿主流并回收 Ticket，但 SQLBot 后台任务可能继续运行，界面明确提示这一边界。

执行明细随原结果缓存保存，沿用 10 分钟 TTL；本次会话的多次问题保留在页面，刷新页面并不恢复历史列表。完整历史检索不在本次改动范围。SQLBot 详情读取失败时保留查询结果并显示警告；失败或断开的流不显示为成功。执行信息中的已配置凭证值与敏感字段被清理，页面用 textContent 渲染模型内容。

验收：用户给定的“昨天收货次数，物品编号与收货时间组合去重”问题真实查询成功，初次验证 Record 16，结果 108810，8 项执行步骤可读取；分析、推荐、预测接口分别完成真实 SSE 请求。最终复测及缓存一致性证据见 `.runtime/local/question-stream-verification-final.json`，其他过程证据见 `.runtime/local/question-*-verification.json`。相关后端测试 17 项、JavaScript 测试 5 项通过，包括 SSE 中文分片、连接中断、多个图表指标、页面组件渲染、生产任务取消与凭证清理。Jev 浏览器自动验证受到元素定位/输入执行错误阻塞，未完成浏览器端到端视觉验收，不能将 API 与组件测试视为视觉验收通过。

主要文件：`app/sqlbot/client.py`、`app/data_mcp/service.py`、`app/data_mcp/routes.py`、`app/data_mcp/schemas.py`、`app/web/static/data-agent-chat.js`、`app/web/static/data-agents.js`、`app/web/static/data-agents.css`、`app/web/templates/data-agents.html`、`app/web/routes.py`。G2 使用 SQLBot 仓库已有版本的本地构建文件，许可证保留于 `app/web/static/vendor/G2-LICENSE`。
