# 数据集编辑反馈修复

对应飞书测试记录：<https://atrenew.feishu.cn/wiki/F8kgw2oEticsYpkBMJpc5JHanub>。
原始 Session 的关键失败发生在 2026-09-10。该修复涉及 Davinci 前端和
Agent Host，不修改 Data MCP，不部署或更改线上业务数据。

## 变更

- 普通数据源误传 `isQueryVar:true` 返回 `INVALID_ARGUMENT`，提示使用普通筛选。
  widget 的变量标识必须来自当前可用 `queryVars`；无效标识不再冒充权限拒绝。
  来源权限仍走原检查，缺日期仍拒绝写入。
- `get_source_fields` 只为原生支持查询变量的 widget 来源返回变量清单，避免
  把旧元数据中不可执行的变量暴露给 Agent。
- `EDITOR_ALREADY_OPEN` 保留拦截，但恢复动作改为 `get_context`，并明确
  `committed:false`；禁止为重复打开而丢弃现有草稿。
- Host 常驻指令、工具描述和 locate-data 的交接规则按实际回执报告状态：
  请求字段不等于已选字段；校验成功必须检查当前 `validation.valid`；
  `save_draft` 打开确认窗口不代表已保存。

## 回归范围

- Davinci 原始请求回归：`warehouseTopic:10`、日期字段 `339`、四个目标字段。
  元数据使用最小隔离样本，执行真实控制器及草稿准备逻辑。先复现错误权限码，
  再验证参数错误、日期为空时草稿不变，以及完整日期的成功写入。
- 数据集控制器、Hook、保存确认和市场工具：78 项通过。
- Host 工具契约、工具注册和发布预检：24 项通过。Skill 引导的其他 11 项通过。
  额外的 2 项旧测试仍写死五个全局 Skill，而未修改的现有 manifest 已有六个
  （新增项 beautify-dashboard）；这两项失败属于现有基线，本次未调整。
- `npm run check:agent-startup`：222 项前端、90 项 Host、2 项真实跨 Origin
  浏览器测试通过。浏览器测试使用当前工作区已有的 overlay 扩展，扩展本身不属于
  本次改动。首次 Chromium 启动被沙箱限制，沙箱外重跑通过。
- TSLint 在修改行无新增问题；市场控制器有 19 条既有格式告警。Host Ruff 通过。
- 真实模型回放入口：`tests/live/test_dataset_editor_feedback.py`，使用当前 Host
  指令和新 Session 的 Skill 快照；业务页面回执隔离，不调用真实业务接口。
  deepseek-v4-pro-0813 两项多轮测试通过（347 秒）：缺日期时先询问；补齐日期后
  成功走到保存确认，并明确尚未持久化；写入被拒后回读 `fieldIds:[]`、
  `validation.valid:false`，如实报告未生效并停止预览和保存。
  首轮沙箱内模型响应超时，未执行页面工具；沙箱外重跑通过。
  真实环境的创建、保存及回读仍需上线后验收。

```bash
RUN_LIVE_DAVINCI_SKILL_ROUTING=1 .venv/bin/python -m pytest \
  tests/live/test_dataset_editor_feedback.py -q
```

## 配套发布

提交前同步 Davinci 的 `b080a3df4` 后，独立提交副本的启动回归发现同事新增的
任务组件使用 `category:task`，但 `dashboard.get_widget_edit_capabilities`
的正式输出契约缺少该分类，导致 `creationOptions[23].category` 校验失败。
补齐正式契约中的原生 task 枚举并重建两端产物；回归明确检查六个任务选项
（执行人和关注人各三种），保留其余类型、字段和边界校验。

1. Davinci 和 Host 同步本次配对产物；正式源仍为 Host
   `contracts/davinci-agent-v2.json`，摘要按原始字节计算。两端生成文件和 Host
   `embed.js` 已重新生成，不能单独发布一端。
2. 线上 Skill 以管理库实际生效版本为准。部署时先用目标环境配置执行
   `scripts/publish_davinci_skills.py --dry-run`，查看来源和受保护版本；发布沿用
   该命令既有的同源更新流程，不覆盖管理员版本或归档状态。
3. 若使用管理员维护版本，通过现有 Skill 管理入口更新对应 locate-data，
   不擅自启用同事已停用的个人 davinci-dataset-discovery。
4. 新建验证 Session，核对其 Skill bundle hash，再运行缺日期、失败回执、
   补齐日期和保存确认场景。旧 Session 快照不能作为新版指令已生效的证据。

本次 locate-data bundle hash：
`sha256:048eb9f1b78e1dc317b3bd557bf330a695b3f9fcfb6b20a86b2b56bccacda212`。
可上传压缩包已用平台原生打包器生成并回读校验，未上传线上。
