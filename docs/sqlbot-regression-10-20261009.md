# SQLBot 10 题基线复现与优化记录（2026-10-09）

## 当前结论

已完成固定 10 题的修复前/后各一次真实请求；没有开展 500 题全量重跑。数据库连接超时根因已定位并修复；Data Agent 已改为返回查询数据和图表类型，不调用模型生成图表配置。模型额度不足阻塞多数问数与文本分析，因此不能宣称 10 题全部通过。

用户确认暂不切换服务 2：元数据来源仍为 `http://10.193.65.41:8002/`；本地 SQLBot 为 `http://127.0.0.1:8000/`；Host 为 `http://127.0.0.1:8765/`。`http://10.193.65.41:18000/` 可访问但未用于本次测试。

## 固定样本与实测结果

样本包含 DW 5、DM 3、RPT 2。前三类重点问题为 T018/T019/T020，另含简单计数、多表查询和历史纠错题；同一组问题使用新会话、原问法，不追加说明。

| 用例 | 分组 / 原问题 | 历史实际模型 | 历史附技术纠错 | 修复前记录 / 结果 | 修复后记录 / 结果 |
|---|---|---|---|---|---|
| T001 | DW / 昨天质检多少台？ | qwen3.8-max | 未见该标记 | 1419 / SQL 成功，图表额度不足 | 1429 / SQL 成功，类型 table；文本分析额度不足 |
| T010 | DW / 最近一周各品牌检出来的等级分布怎么样？ | qwen3.8-max | 未见该标记 | 1420 / 模型额度不足 | 1431 / 模型额度不足 |
| T018 | DW / 最近一周复检时，哪些质检项的结果和之前不一样？各有多少台？ | qwen3.8-max | 未见该标记 | 1421 / 模型额度不足 | 1432 / 模型额度不足 |
| T019 | DW / 这周X-RAY检出异常的多吗？正常和异常的量按质检项拆开看看。 | deepseek-v4-flash | 未见该标记 | 1422 / 模型额度不足 | 1433 / 模型额度不足 |
| T020 | DW / 最近一个月魔镜检出异常最多的是哪十个质检项？ | qwen3.8-max | 未见该标记 | 1423 / 模型额度不足 | 1434 / 模型额度不足 |
| T057 | DM / 拍拍最近一周售后申请比前一周多了多少？ | deepseek-v4-flash | 未见该标记 | 1424 / 模型额度不足 | 1435 / 模型额度不足 |
| T097 | DM / 这周IPQC和FQC哪个拦截得多？分别多少？ | deepseek-v4-flash | 是 | 1425 / 模型额度不足 | 1436 / 模型额度不足 |
| T168 | DM / 这周看图抽检哪些项目跟原来的结论不一样，各有多少单？ | deepseek-v4-flash | 是 | 1426 / YEARWEEK 函数不兼容 | 1437 / 模型额度不足 |
| T193 | RPT / 这周各运中收货量从高到低列一下。 | deepseek-v4-flash | 是 | 1427 / 模型额度不足 | 1438 / 模型额度不足 |
| T196 | RPT / 最近一周各运中的收货渠道分布怎么样？ | deepseek-v4-flash | 是 | 1428 / 模型额度不足 | 1439 / 模型额度不足 |

历史最终成功记录并非同一模型：4 题使用 qwen3.8-max，6 题使用 deepseek-v4-flash；当前均使用 qwen3.8-max。T097、T168、T193、T196 的历史最终结果含技术纠错上下文。不能把不同模型、不同重试条件下的历史成功率与本次原问法直接等同比较。历史模型配置已被替换，无法完整还原旧服务端点及所有采样参数，属于近似复现。

选中 10 题的 SQLBot 原始日志已核实：FILTER_TERMS、FILTER_SQL_EXAMPLE、FILTER_CUSTOM_PROMPT 均无命中。当前术语库、SQL 示例库为空；今天新增的两条复检专项提示已备份并删除，删除后自定义提示数量为 0。没有导入历史 SQL 为示例，也没有加入业务关键词或正则路由。

## 超时诊断与修复证据

- 记录 1417 原先数据库执行约 20.51 秒后报 PyMySQL 2013 / timed out。
- 真实驱动检查证明：调用 SQLBot 的连接检查后，动态数据源配置被写为 timeout=10；后续查询池的 connect_timeout/read_timeout 均为 10 秒。
- StarRocks 会话 query_timeout 为 120 秒，Host HTTP 超时为 120 秒。问题不是 Host 的 120 秒阈值触发。
- 原 SQL 在独立连接 read_timeout=130 下执行 10.54 秒，返回 3 行，证明至少该次正常执行超过 10 秒读取窗口。驱动库具备失败后再次执行的逻辑；未取得原请求的逐次网络/服务端日志，不能把 20.51 秒精确拆成两次查询。
- EXPLAIN VERBOSE 显示 Hive 外表 partitions=2837/2837；inspection_dt 是非分区过滤，真正分区字段为 partition_date。两者关系未获验证，因此未擅自增加分区过滤或替换 SQL。
- 本地 SQLBot 补丁将动态 StarRocks/Doris 连接与读取超时分离为 10/30 秒，查询连接不再继承连接检查的 10 秒阈值；连接池按有效连接配置变化失效重建。保留数据库服务端配置。
- 修复后按实际 SQLBot 连接检查、版本探测、驱动池顺序执行原 SQL 四次：4.11 / 5.51 / 2.50 / 0.75 秒，全部返回 3 行，完整结果摘要与独立连接一致。实际连接参数为 connect=10、read=30。共享数据库缓存不受控，不能据此宣称 SQL 优化带来同幅度提速。

## 只返回图表类型

SQLBot 内部已有 QUERY_DATA 结束步骤，但原聊天 API 固定走 GENERATE_CHART。已增加 generate_chart 可选参数，默认 true 保持原 SQLBot 页面行为；本项目发送 false。执行完成后直接回传 SQL 生成时已有的 chart-type，不额外调用模型、不生成坐标轴配置、不绘制图表。缺失类型显示“未提供”，不靠字段关键词推测。

实际记录 1429：finish=true、error=null、chart=null，GENERATE_SQL/EXECUTE_SQL 成功，没有 GENERATE_CHART，Host 返回 chartHint.type=table。后续文本分析记录 1430 进入 ANALYSIS，但因模型额度不足失败；文本分析功能仍保留，用户可按需触发。

错误返回新增可区分的错误码和 record_id/stage/upstream_message；SSE 仅发出一次分类后的错误，并沿用凭据脱敏。Data Agent 页面保留数据表、SQL、导出及图表类型，移除 G2 图表绘制；无图表的记录不展示依赖图表配置的预测入口。

## 交付与验证

- 固定样本：`tests/fixtures/sqlbot-regression-10.json`。
- 回归入口：`scripts/sqlbot_sample_regression.py`，逐题新会话、无重试、保留查询与分析各自记录 ID，不保存查询结果集。
- SQLBot 可重建镜像补丁及部署/回滚说明：`deploy/sqlbot/`。
- 自动验证：26 项 Python 测试、5 项 JavaScript 测试通过；SQLBot 补丁测试使用真实提取的原版源码，未跳过。移除图表类型猜测后另行重跑受影响的数据 Agent 测试。
- 实测证据：`.runtime/sqlbot-regression-10/` 中的 baseline/after、record-evidence.json、effective-timeout.json、database-diagnostics.json、record-1417-direct-replay.json、driver-after.json。
- 私有配置备份为同目录 configuration-before.private.json，目录权限 700、文件权限 600，未提交版本库。

重新运行（使用新的输出目录）：

```sh
python -m scripts.sqlbot_sample_regression \
  --cases tests/fixtures/sqlbot-regression-10.json \
  --output .runtime/sqlbot-regression-10/next-run
```

## 剩余待办与限制

- [ ] 模型服务额度恢复后，用同一组 10 题完成原问法回归；当前 9 题未能生成有效 SQL，不能判定通过或语义退化。
- [ ] 额度恢复后，对受影响自然问法做额外 3 次验证；没有为额外 3 次稳定性验证继续请求已确认额度不足的模型。
- [ ] 核实 T168 的 YEARWEEK(datetime, tinyint) 方言问题是否在原问法下继续出现；不以特殊字符串改写 SQL 掩盖失败。
- [ ] 如仍需性能优化，取得 partition_date 与 inspection_dt 的业务/ETL 对应关系以及原请求 Profile，再设计保持口径的分区裁剪；目前无证据支持安全改写。
- [ ] 10 题业务正确性需逐题核实；执行完成、图表类型返回均不等同于口径正确。

没有修改远端数据库结构或参数，没有切换用户模型，没有开展助手动态发现改造。
