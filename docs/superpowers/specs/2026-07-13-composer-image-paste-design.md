# Composer 图片粘贴与 Qwen3.7-Plus 设计规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 项目目录 | `/Users/a110356/work/code/claude_workspace_mvp` |
| 文档日期 | 2026-07-13 |
| 文档状态 | 已确认，待书面审阅 |
| 功能范围 | Composer 粘贴图片、附件预览、附件数量防线、默认模型切换 |
| 默认模型 | `qwen3.7-plus` |

## 2. 一句话定义

用户可以像在 Claude App 中一样，将剪贴板中的一张或多张图片直接粘贴到聊天输入区；图片立即上传到当前 Session，显示可删除的缩略图，并在发送时通过现有附件链路作为多模态内容交给 `qwen3.7-plus`。

## 3. 背景与问题

当前系统已经支持通过文件选择器和拖放上传图片，也已经具备以下后端能力：

- 按 Session 隔离附件目录和数据库记录。
- 检测 PNG、JPEG、GIF、WebP 的真实文件类型。
- 限制单文件大小和单批上传数量。
- 在发送 Turn 时把图片编码为 Anthropic `image` 内容块。
- 在发送前删除 pending 附件，并定期清理过期 pending 附件。

当前缺口是 Composer 没有处理浏览器 `paste` 事件，附件栏也只显示通用 Chip，没有图片缩略图。现有默认模型 `qwen3.7-max` 只接受文本输入，因此即使前端完成粘贴，图片消息仍会失败。

## 4. 已确认决策

1. 服务默认模型永久切换为 `qwen3.7-plus`。
2. 粘贴图片后立即上传，不在浏览器内存中等待发送。
3. 支持一次粘贴一张或多张图片。
4. 图片和普通文件可以混合，每个 Turn 合计最多 5 个附件。
5. 复用现有附件上传接口、附件记录和 Turn 提交格式，不新增图片专用 API。
6. 图片只属于当前 Session；发送后绑定当前 Turn。
7. 图片预览包含缩略图、文件名、大小和删除按钮。
8. 纯文本粘贴保持浏览器默认行为。
9. 剪贴板同时包含图片和文本表示时优先处理图片，避免网页替代文本被重复插入。
10. 测试必须覆盖用户直接图片和工具返回图片，避免再次出现 `Unexpected item type in content`。
11. pending 附件是 Session 草稿；切换 Session 时只隐藏，重新进入或刷新后从服务端恢复，避免隐藏草稿占用附件额度。

## 5. 目标与非目标

### 5.1 目标

- 在输入框聚焦时支持 macOS 截图、复制图片及其他浏览器可提供的 `image/*` 剪贴板项。
- 粘贴后立即展示上传状态和可操作预览。
- 复用现有 Session、附件和 Turn 生命周期，保持数据边界一致。
- 在前端、附件服务和 Turn 服务三层共同保证每 Turn 最多 5 个附件。
- 使用当前服务 Key 和 `qwen3.7-plus` 完成真实多模态验收。

### 5.2 非目标

- 不实现图片编辑、裁剪、压缩、标注或 OCR 预处理。
- 不从剪贴板 HTML 中下载远程图片。
- 不允许在不同 Session 间复用 pending 附件。
- 不新增对象存储、外部文件服务或独立上传任务队列。
- 不在页面提供动态模型选择器。
- 不改变普通文件上传、拖放和 `@` 文件引用的既有语义。

## 6. 方案选择

### 6.1 采用方案：复用现有附件链路

```text
Clipboard paste
  -> 提取 image/* File
  -> 生成稳定的展示文件名
  -> 现有 uploadFiles(files)
  -> POST /api/sessions/{session_id}/attachments
  -> pending attachment records
  -> 缩略图预览
  -> POST /api/sessions/{session_id}/turns + attachment_ids
  -> Claude Runtime image content block
  -> qwen3.7-plus
```

该方案保留现有安全校验、Session 隔离、删除语义和清理机制，前后端改动最少。

### 6.2 未采用方案

- **浏览器本地暂存，发送时上传：** 页面刷新会丢失图片，发送失败和重试状态更复杂。
- **Base64 直接写入 Turn JSON：** 请求体膨胀，并绕过现有附件校验、持久化和生命周期管理。

## 7. 组件设计

### 7.1 `composer-paste.js`

新增独立、无网络依赖的 Composer 粘贴控制器，职责如下：

- 监听或处理输入框的 `paste` 事件。
- 从 `clipboardData.items` 中提取所有 `kind=file` 且 MIME 为 `image/*` 的项。
- 没有图片时返回未处理状态，不调用 `preventDefault()`。
- 有图片时阻止默认粘贴，把图片数组交给注入的上传回调。
- 为缺少或名称不明确的剪贴板文件生成 `clipboard-YYYYMMDD-HHmmss-N.<ext>` 展示名。
- 不读取 Session 状态、不发送 HTTP 请求、不直接渲染 DOM。

独立控制器沿用现有 `composer-autocomplete.js` 的模块边界，使剪贴板识别逻辑能够由 Node 测试直接覆盖。

### 7.2 `app.js`

`app.js` 负责把粘贴控制器接入现有页面状态：

- 仅在已选择 Session、当前无运行 Turn 且服务已连接时接受图片粘贴。
- 调用现有上传函数，不创建第二套上传实现。
- 引入明确的上传中状态，上传期间阻止发送和重复上传，但不锁定文本输入。
- 成功后把服务端返回的附件记录加入 `pendingAttachments`。
- 失败时保留输入文字和原有 pending 附件，并显示后端错误。
- Session 或 Workspace 切换时清空当前页面的 pending 附件列表，保持现有隔离语义。

### 7.3 附件预览

`renderAttachmentTray()` 按附件类型渲染：

- 图片使用 `content_url` 显示约 `72 x 72` 像素的缩略图。
- 同时显示原始文件名、格式化后的字节大小和独立删除按钮。
- 缩略图加载失败时退化为现有 `IMG` Chip，不影响删除和发送。
- 普通文件继续使用现有文件 Chip。
- 多附件横向排列；窄屏允许滚动且不得扩大页面宽度。

历史消息继续使用现有附件记录和内容 URL，不引入 Base64 页面持久化。

### 7.4 附件数量防线

现有上传接口只限制单批文件数，用户可能通过多次粘贴绕过每 Turn 最多 5 个附件的产品规则。实现时增加三层校验：

1. 前端在上传前检查 `pendingAttachments + pastedFiles`，超限时不发起请求。
2. `AttachmentService.upload()` 在事务边界内统计当前 Session 的 pending 附件，确保跨批次总数不超过 `MAX_FILES_PER_TURN`。
3. `TurnService` 校验提交的 `attachment_ids` 数量不超过 `MAX_FILES_PER_TURN`，防止直接调用 Turn API 绕过上传检查。

后端拒绝整批超限上传，不能遗留临时文件或部分数据库记录。

### 7.5 Session 草稿恢复

附件服务提供当前 Session pending 附件的只读列表，页面选择 Session 时加载该列表并恢复附件栏。列表只返回目标 Session 自己的 pending 记录，按创建时间稳定排序；已绑定 Turn 的附件不会进入草稿列表。

切换到其他 Session 时，当前附件栏立即替换为新 Session 的草稿。刷新页面或稍后返回原 Session 时重新加载其 pending 草稿，因此跨批次数量限制不会被不可见附件长期占用。

### 7.6 模型配置

有效默认模型来自服务启动环境的 `CLAUDE_MODEL`。本功能把启动服务配置改为：

```bash
CLAUDE_MODEL=qwen3.7-plus
```

Workspace 没有显式 `model` 时继续继承服务默认值，不在 `workspace.yaml` 重复写死模型。修改后重启 `com.codex.claude-workspace-agent`，并通过健康接口和 Workspace API 验证生效模型。

## 8. 交互规则

1. 输入框聚焦且粘贴内容没有图片时，浏览器照常插入纯文本。
2. 粘贴内容包含一张或多张图片时，页面阻止默认行为并上传全部图片项。
3. 同一个剪贴板项同时提供图片、HTML 和文本表示时，只消费图片文件，不插入伴随文本。
4. 上传期间 Composer 状态显示“正在上传图片”。
5. 上传完成后显示缩略图并恢复“就绪”。
6. 用户可以继续输入说明文字，也可以只发送图片。
7. 删除 pending 图片时调用现有附件删除接口；成功后再从页面状态移除。
8. Turn 运行期间、未选择 Session 或服务断开时，不处理图片上传；纯文本粘贴仍保持默认行为。
9. 发送失败时保留文本、文件引用和 pending 附件，允许原地重试。
10. 选择 Session 时从服务端加载该 Session 的 pending 草稿；不得显示其他 Session 的草稿。

## 9. 安全与错误处理

- 浏览器 MIME 仅用于筛选候选项；服务端继续根据文件 Magic Bytes 判定真实类型。
- 服务端只接受既有白名单格式，拒绝空文件、可执行文件、伪造图片和未知二进制。
- 单文件大小继续受 `MAX_UPLOAD_SIZE_MB` 约束。
- 每 Turn 附件总数继续受 `MAX_FILES_PER_TURN` 约束，默认值为 5。
- 上传批次采用全有或全无语义；任一文件失败时回滚该批次文件和记录。
- 错误提示区分超量、超大、不支持格式、Session 不可用和网络失败。
- 附件内容不写入普通日志；API Key 不进入浏览器或数据库。

## 10. 测试设计

### 10.1 JavaScript 单元测试

- 纯文本粘贴不拦截默认行为。
- 单张 PNG 粘贴被提取并上传。
- 多张图片按剪贴板顺序交给上传回调。
- 图片和文本同时存在时只消费图片。
- 缺少文件名时生成稳定、带扩展名且同批不重复的展示名。
- 未选择 Session、Turn 运行中或正在上传时不触发图片上传。
- 超过剩余附件数量时不调用 API 并返回明确错误。

### 10.2 Python 后端测试

- 多批上传累计不超过 5 个时成功。
- 多批上传累计超过 5 个时整批拒绝。
- 拒绝后不遗留临时文件、最终文件或数据库记录。
- Turn API 拒绝超过限制的附件 ID。
- 一个 Session 的 pending 附件不影响另一个 Session。
- pending 列表只返回目标 Session 的未绑定附件，并按创建时间稳定排序。
- 图片仍由 Magic Bytes 识别，而非信任客户端 MIME。

### 10.3 Playwright 测试

- 向输入框派发包含真实 PNG `File` 的粘贴事件后出现缩略图。
- 缩略图显示文件名和大小，删除后附件消失。
- 发送请求包含正确的 `attachment_ids`，不把 Base64 写入 Turn JSON。
- 粘贴纯文本仍正常进入 textarea。
- 切换 Session 后待发送图片不会显示在另一个 Session。
- 切回原 Session 或刷新页面后恢复该 Session 的 pending 图片。
- 页面刷新后已发送历史附件仍可见。
- 手机宽度下多缩略图不造成横向页面溢出。

### 10.4 真实模型验收

当前 Key 已通过 `https://dashscope.aliyuncs.com/apps/anthropic/v1/messages` 对 `qwen3.7-plus` 的三条预检：

| 场景 | HTTP | Request ID |
| --- | --- | --- |
| 文本消息 | 200 | `89bc4aa2-47ca-977f-a180-e0832c8aa9f3` |
| 用户直接图片 | 200 | `9beaec75-44ab-9dd9-bc69-0e23f2704fa7` |
| `tool_result` 内嵌图片 | 200 | `35438709-7a52-9093-96b3-f4b6c08f8e0b` |

实现后的真实验收流程：

1. 重启服务并确认 Workspace 能力摘要显示 `qwen3.7-plus`。
2. 新建 Session，在输入框直接粘贴真实 PNG。
3. 输入“描述这张图片”并发送。
4. 确认模型返回与图片内容一致的描述。
5. 刷新页面，确认用户图片和模型回复仍存在。
6. 触发工具读取截图，确认不再出现 `Unexpected item type in content`。

## 11. 验收标准

- 用户无需打开文件选择器即可通过粘贴发送图片。
- 文本粘贴、文件选择、拖放、Skill 补全和 `@` 文件补全没有回归。
- 图片上传、删除、发送和历史恢复均遵守当前 Session 边界。
- 图片与文件合计超过 5 个时在前后端均被可靠拒绝。
- 默认模型实际生效为 `qwen3.7-plus`。
- 当前 Key 可完成直接图片和工具图片的真实调用。
- Python、JavaScript、Playwright 和真实模型验收全部通过。
