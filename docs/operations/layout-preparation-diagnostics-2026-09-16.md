# 布局候选来源诊断

## 范围

本轮发布补齐诊断，不修改候选生成规则或承诺 case-037 已修复。工作区内此前的区域阅读算法修改不包含在本轮诊断提交中。

前端 `preparation-v1` 记录原始几何、尺寸锁、模式、观测与兜底原因、精确尺寸测量、可选预算耗尽、增长过滤、像素/网格可读下限、organize 裁剪、最终候选，以及内容复核后的候选删除。指标卡单尺寸不再只能靠猜测归因为超时。

Host embed 将 sidecar 与求解几何分离；服务端清洗后绑定同一个 sessionId/toolCallId/layoutRunId。原有 problem/result、solverRevision、搜索计数与拒绝原因、候选质量、frontendOutcome 保留，另记录输入摘要、准备/计算阶段耗时。metadata 不进入求解器或子进程。旧客户端缺少字段仍正常求解。

## 读取

在 Host 仓库使用既有脚本；认证配置继续保存在本机 Git 外，不打印或提交凭证：

```sh
python3 scripts/layout_diagnostics.py --session <session-id>
python3 scripts/layout_diagnostics.py --run-id <layoutRunId>
```

新增根字段 `preparationDiagnostics`、`preparationDiagnosticsStatus`。状态为 missing/captured/truncated/invalid。`missing` 通常表示旧前端或调用方没有上传，不等于没有执行测量。按 nodeId 和事件先后查看各阶段 inputCount/outputCount、sizes、reason 和剩余预算；以 problem.nodes[].shapes 为最终完整准入集合。被截断的样本不能用来断言某个候选从未生成。

## 有界与隐私

- 最多600条准备事件，每事件32个尺寸样本，全局4096个样本；截断明确标记。优先保留原始/最终尺寸及过滤摘要，必要时淘汰测量明细。
- 仅记录允许的数字几何和代码定义的枚举；不存标题、数值、数据集、完整配置、DOM文本、原始异常内容。服务端只接收属于本次 problem 的节点ID。
- 继续使用 `<app_data_dir>/layout-diagnostics/runs.sqlite3`，7天/1000条/总64MiB/单条2MiB的既有限制。异常metadata不影响有效求解，落盘失败由 diagnosticsStored 提示。
- 不是无界搜索分支日志。未发出 solve 请求之前就失败的准备过程、浏览器断网未上传的记录、已过期记录无法由这份运行快照恢复；旧运行也不能补回当时没有采集的字段。

## 发布与生效

1. 先将本轮 Host 提交更新到服务部署目录，使用已经重建的 `app/web/static/embed.js`，然后重启 Agent 服务。只重启仍指向旧代码的进程不生效。
2. 构建并发布配套 Davinci 前端提交。浏览器测量/过滤的采集逻辑在前端，**本轮不能只重启后端**。先更新 Host 再发布前端，避免新前端被旧 Host 当成未知 problem 字段拒绝。
3. 刷新看板与智能体 iframe，重新执行一次布局。旧 layoutRunId 不会凭空增加新证据。
4. 代理用新 sessionId 或 layoutRunId 查询：确认准备 version=preparation-v1、状态为 captured 或明确的 truncated，并检查同一运行的 frontendOutcome、persisted 与实际坐标。

未自动发布、重启或改写线上看板。原始 S01-S07、board333真实40卡、冷/热30秒与线上保存刷新验收仍需真实输入和环境，不能用本轮日志单元测试替代。

## 本轮验证

日志专项并发回归：前端4套件125项、后端诊断/路由/查询93项、embed传输4项、图片归档90项通过。后端bootstrap/契约91项通过；使用本机Chrome完成真实双Origin iframe两项回归。构建和契约摘要检查通过。后端93项也在仅含待提交文件的独立快照上并发通过，不依赖工作区未提交的算法修改。

完整 `check:agent-startup` 首次运行未全绿：控制器隐藏标签页用例有一次空洞断言失败，独立并发复跑当前树及HEAD均120项通过，仍保留其时序风险。扩展AcceptanceController的compact alternative断言当前树和HEAD均失败，属于基线已有问题。没有跳过这些失败后宣称全套验收通过。限定变更文件的TypeScript检查未发现新增诊断，控制器两项既存TS2322与HEAD一致。
