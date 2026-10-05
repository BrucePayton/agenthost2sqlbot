# 布局诊断留档与本地查询

布局求解会返回 `layoutRunId`，用于在工具调用结束后查询本次几何输入、求解结果、决策计数和前端回报。运维人员可在自己的电脑运行 `scripts/layout_diagnostics.py`，无需 SSH 登录服务端。脚本仅发送 GET 查询，不执行布局、不保存看板、不修改凭证。

## 留档内容与边界

- `layoutRunId` 是本次求解的 UUID；`sessionId`、`toolCallId` 用于关联会话和工具调用。
- `problem` 保存几何约束，包括组件标识、父子关系、顺序、尺寸候选和原始位置；`result` 保存求解结果。记录还包括时间戳、`state`、`solverRevision` 和 `trace.counters`。`solverRevision` 是布局模块 Python 源文件的内容指纹，不是 Git 提交号。
- 记录仅包含几何和诊断元数据，不包含业务图表数据、SQL、图表标题、原始工具回执或完整 DOM。它仍包含会话、调用及组件标识，应按内部运维数据管理；不要把业务文本塞入标识字段。
- 求解响应中的 `diagnosticsStored=true` 表示最终快照写入成功，不表示前端已应用或看板已保存。诊断写入失败不会阻断求解；`diagnosticsStored=false` 时即使拿到 ID，也可能查不到最终记录。
- `state=finished` 只表示计算结束，须结合 `result.status` 判断是否有可用结果。计算异常可记录为 `compute_error`，只保存异常类型，不保存原始异常消息。中断时可能仅留下 `state=running` 的初始快照。

### 前端证据不是完整页面快照

`frontendOutcome` 来自前端工具回执的白名单字段，包括 `status`、`persisted`、`executionMode`、`fallbackReason`、`changes` 和 `changesTruncated`。其中 `persisted` 是前端报告值，不是服务端重新读取看板后的独立校验。

上报为 best-effort：每次请求约 2 秒超时，失败不会把已经成功保存的布局变成错误，也没有持久化重试队列。当前前端每个会话/调用组合最多关联最近 4 个运行 ID，内存映射最多保留 64 个组合。页面关闭、网络异常或映射淘汰都可能导致 `frontendOutcome=null`，不能据此断言保存失败。

`changes` 取自回执中的 `layoutChanges`，最多保留 200 项，记录组件应用后的网格坐标 `id/x/y/w/h`。上游已截断或超过 200 项时，`changesTruncated=true`；即使为 `false`，也只是回执所提供的变化列表，不是全量 DOM、全部组件或完整看板状态。

## 服务端部署

### 生效验收

1. 配套部署本次 Host 后端及其 embed 构建产物，以及 Davinci 前端；两端必须使用同步生成的同一份工具契约。仅更新 Python 后端不能获得完整的前端执行回报。
2. 按下文配置可写持久化的 `APP_DATA_DIR`，启用 Inspector 查询并通过 HTTPS 暴露受保护的接口。多实例部署需保证查询能访问执行求解的实例所使用的诊断数据库。
3. 在 Codex 所在电脑一次性设置下文的私有配置文件；不要将密码发送到聊天或提交 Git。之后 Codex 可直接执行查询脚本，不需要用户逐次登录服务器找日志。
4. 发布后刷新看板页面，重新执行一次美化布局。从回执取得 `layoutRunId`，或按会话查询最新运行；确认输入、求解结果以及可用的 `frontendOutcome` 能关联到同一次执行。

只有部署后的新求解能留档，无法补回历史截图对应的运行记录。本次增加诊断能力，并未据此宣称修复截图中的布局问题；真实 S01-S07、原始 40 卡片及冷/热运行 30 秒目标仍需用对应真实输入回归。

### 数据目录与保留策略

数据库固定放在 `APP_DATA_DIR/layout-diagnostics/runs.sqlite3`。`APP_DATA_DIR` 未显式配置时，应用默认使用 `./data`，因此默认相对路径为 `./data/layout-diagnostics/runs.sqlite3`。生产部署应让 `APP_DATA_DIR` 指向应用进程可写的持久卷，跨进程重启保留同一个目录；未共享该目录的不同实例拥有各自的诊断记录。

首次访问时自动创建 SQLite 数据库，新建诊断目录权限为 `0700`，数据库权限设为 `0600`。诊断存储使用该目录下的独立 SQLite 文件，不随主业务数据库连接地址切换。

| 限制 | 当前实现 |
| --- | --- |
| 保留时间 | 按 `createdAt` 保留 7 天，前端回报不会延长寿命 |
| 条数 | 保存快照时保留最新最多 1000 条 |
| 总容量 | 保存快照时按序列化 JSON payload 累计，逻辑上限 64 MiB |
| 单条 | 序列化 JSON payload 最多 2 MiB，前端回报合并后也检查 |

以上值是代码常量，当前没有对应环境变量开关。清理是惰性的：读、写及前端回报访问数据库时清理超过 7 天的记录；保存快照时再按条数和总容量淘汰旧记录，没有定时清理任务。前端回报更新仅检查单条上限，不立即重算 64 MiB 总量，下一次保存快照才重新淘汰。

64 MiB 是 JSON 的逻辑容量，不是磁盘配额。SQLite 页面、索引及事务日志有额外占用，删除记录不会自动缩小文件，当前也不执行 `VACUUM`。查询虽然是 HTTP GET，仍可能触发服务端过期记录清理；“本地只读查询”不意味着服务端数据库完全不写入。

### 启用运维查询

留档随布局求解执行，不依赖 Inspector 开关。跨会话的运维查询复用既有 Session Inspector 的 HTTP Basic 认证，没有单独的布局诊断账号或令牌。服务端配置如下：

| 服务端变量 | 要求 |
| --- | --- |
| `APP_SESSION_INSPECTOR_ENABLED` | 设置为 `true` 才注册 Inspector 路由，默认关闭 |
| `APP_SESSION_INSPECTOR_USERNAME` | 使用现有运维账号，默认 `inspector`，长度 1 到 64 字符 |
| `APP_SESSION_INSPECTOR_PASSWORD` | 启用时必须至少 24 个字符，否则应用配置校验失败 |

这套账号拥有既有 Inspector 访问能力，并非仅限单个布局或会话。按既有部署流程注入和保护凭证，不能将其下发到 iframe、浏览器前端配置或提交到仓库。本文不要求更换已有凭证；配置变更应由部署负责人通过正常发布流程生效。

对外入口必须使用 TLS/HTTPS，并提供有效、受客户端信任的证书。HTTP Basic 的 Base64 编码不是加密。反向代理需转发 `/api/inspector/dashboardLayoutRuns` 路径及认证头，避免记录认证头，并让查询 URL 直接指向最终 HTTPS 地址。脚本使用默认 TLS 验证，不提供跳过证书检查选项，也不跟随任何重定向。

## 本地一次性配置

脚本只依赖 Python 标准库，可使用项目的 `.venv/bin/python` 或本机 `python3`。以下命令均从仓库根目录执行。

默认配置路径是当前用户的 `~/.config/shucao/layout-diagnostics.json`，JSON 字段为 `baseUrl`、`username`、`password`。`baseUrl` 填应用入口，例如 `https://host.example.com`，不要重复附加 `/api/inspector/dashboardLayoutRuns`。如应用挂载在路径前缀下，可在 `baseUrl` 中保留该前缀。

首次使用可在本机终端执行下面的初始化命令。它交互读取既有运维凭证，密码不会显示或作为进程参数传入；以 `0600` 创建配置，已有文件时拒绝覆盖。

```bash
python3 - <<'PY'
import getpass
import json
import os
from pathlib import Path

path = Path.home() / ".config/shucao/layout-diagnostics.json"
base_url = input("HTTPS application URL: ").strip()
username = input("Inspector username [inspector]: ").strip() or "inspector"
password = getpass.getpass("Existing inspector password: ")
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as stream:
    json.dump({"baseUrl": base_url, "username": username, "password": password}, stream)
PY
```

脚本拒绝任何组用户或其他用户权限位非零的配置文件，例如 `0644`；已有配置可在本机执行 `chmod 600 ~/.config/shucao/layout-diagnostics.json`。检查针对权限位，不校验文件所有者或符号链接。即使使用环境变量，已存在的配置仍会先被读取并检查权限，不能通过环境变量绕过不安全或损坏的配置文件。

### 环境变量方式

也可由本机终端或凭证管理工具注入以下变量，无需创建配置文件。非空环境变量逐项覆盖 JSON 值；用户名缺省为 `inspector`。

| 本地脚本变量 | 对应值 |
| --- | --- |
| `LAYOUT_DIAGNOSTICS_BASE_URL` | JSON 的 `baseUrl` |
| `SESSION_INSPECTOR_USERNAME` | JSON 的 `username`，与服务端用户名一致 |
| `SESSION_INSPECTOR_PASSWORD` | JSON 的 `password`，与服务端密码一致 |

注意：脚本变量没有服务端的 `APP_` 前缀。单独设置 `APP_SESSION_INSPECTOR_PASSWORD` 不会为脚本提供认证信息，脚本也不会自动加载服务端 `.env`。不要把密码写进命令行、URL、聊天记录或 shell 历史；脚本没有 `--password` 参数。脚本本身仅检查密码非空，至少 24 字符的限制由服务端启用配置时执行。

## 查询方式

已知运行 ID 时查询完整记录：

```bash
python3 scripts/layout_diagnostics.py --run-id 00000000-0000-4000-8000-000000000001
```

只知道会话 ID 时先查询最近运行摘要，再用返回的 `layoutRunId` 查详情：

```bash
python3 scripts/layout_diagnostics.py --session 'your-session-id'
```

使用其他本地私有配置文件：

```bash
python3 scripts/layout_diagnostics.py --config /absolute/private/layout-diagnostics.json --session 'your-session-id'
```

`--run-id` 和 `--session` 必须且只能选一个，运行 ID 必须为 UUID。会话参数会进行 URL 查询编码。会话查询输出 `{"items": [...]}`，默认最近 10 条摘要，包含运行、会话、调用标识、创建时间、状态和源码指纹；单条查询输出完整记录 JSON。脚本不提供分页、全库遍历、`--limit` 或 `--tool-call-id` 参数。

成功时标准输出为格式化 JSON，退出码为 `0`；配置、网络或 HTTP 错误写入标准错误，退出码为 `1`；参数错误由 argparse 返回 `2`。请求设置 15 秒网络超时，响应体上限为 2 MiB。服务端允许的单条存储大小与 HTTP JSON 编码后的响应大小不完全相同，最终以脚本的响应字节检查为准。

### API 与权限

| API | 用途与访问控制 |
| --- | --- |
| `GET /api/inspector/dashboardLayoutRuns?sessionId=...` | 运维 Basic 认证，按已知会话查询；API 另支持 `toolCallId` 和 `limit=1..50`，默认 10 |
| `GET /api/inspector/dashboardLayoutRuns/{layoutRunId}` | 运维 Basic 认证，查询单条详情 |
| `GET /api/sessions/{sessionId}/dashboardLayoutRuns/{layoutRunId}` | 应用身份认证并检查会话所有权，不要求原工具调用仍活跃 |
| `POST /api/sessions/{sessionId}/dashboardLayoutRuns/{layoutRunId}/outcome` | 前端上报，检查会话所有权及匹配的 `toolCallId`；本地查询脚本不调用 |

诊断查询成功响应设置 `Cache-Control: no-store`。运维脚本使用前两条 API，不使用浏览器会话 Cookie。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| 缺少配置或密码 | 检查默认文件位置、自定义 `--config` 和本地变量名；缺失文件在环境变量齐全时是允许的 |
| 提示 `chmod 600` | 检查所选配置文件权限；即使有环境变量覆盖也会先检查文件 |
| 远端 HTTP 被拒绝 | 改用最终 HTTPS 地址；仅字面主机名 `localhost`、`127.0.0.1`、`::1` 允许 HTTP，本机测试可用 |
| URL 校验失败 | 入口必须有合法方案和主机，不允许内嵌用户名/密码、查询串或 fragment |
| HTTP 301/302/303/307/308 | 脚本故意拒绝重定向以防泄漏凭证；核对域名、路径前缀和代理规则，直接配置最终入口 |
| HTTP 401 | 检查既有 Inspector 用户名和密码；不要在排查输出中打印凭证 |
| HTTP 404 | Inspector 未启用/未部署、入口错误、记录未成功留档、超过 7 天、容量淘汰或实例数据目录不同均可能导致 |
| 会话列表为空 | 检查会话 ID、实际请求的实例及留档/保留情况；当前存储读失败也可能表现为空列表 |
| 网络或 TLS 错误 | 检查连通性、证书链和代理配置；不要关闭 TLS 验证 |
| 响应过大或非 JSON | 检查返回大小、应用入口与代理响应；脚本不会输出原始错误响应体 |
| 有结果但缺少前端回报 | 按 best-effort 上报边界检查；不能仅凭空值判断看板保存失败 |

服务端存储故障会记录 `layout_diagnostics_write_failed`、`layout_diagnostics_read_failed`、`layout_diagnostics_list_failed` 或 `layout_diagnostics_outcome_failed`。这些日志不会包含原始诊断 payload 或异常详情；结合持久卷权限、磁盘空间和同一实例的记录定位问题。

## 本地测试

### 本次验证记录（2026-09-15）

- 诊断 API、存储及本地查询脚本：37 项通过；Host bridge、工具 runner 和诊断上报：31 项通过。
- 前端布局回执与 launcher 专项：38 项通过。合入其他窗口更新后，数据集相关 53 项、订阅相关 84 项通过。
- 两端生成契约及 Host 构建产物一致性检查通过。
- 完整 `check:agent-startup` 中，258 项前端测试、91 项后端测试通过；最终真实 iframe 拖动用例为 1 失败、1 通过，完整门禁尚未通过。失败发生于等待拖动结束事件，不能作为发布验收已完成的证据。
- 两个 iframe 用例单独运行曾均通过，但整套运行仍出现上述不稳定性。旧版本隔离检查已复现动画未结束时测到 83px、结束后实际为 100px 的现象；测试现等待目标位置再保留原位移断言，未改产品拖动逻辑。结束事件超时仍需进一步定位，不能仅由这些证据认定其根因。
- 尚未执行线上部署或使用线上凭证查询；真实布局案例及性能目标的验证范围见“生效验收”。

```bash
.venv/bin/python -B -m pytest -q -p no:cacheprovider tests/test_layout_diagnostics_cli.py
```

测试使用临时 HOME、临时配置和 HTTP loopback 服务，通过子进程执行真实脚本，覆盖私有文件权限、缺配置、环境变量、只读 GET、远端 HTTP 拒绝及重定向凭证隔离。测试不连接线上服务，不读取实际本机凭证。
