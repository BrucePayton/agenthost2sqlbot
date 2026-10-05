from __future__ import annotations

import copy
import html
import re
import textwrap
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "agenthost-data-mcp-sqlbot-report-flows.drawio"
CREATE_PREVIEW = ROOT / "docs" / "agenthost-data-mcp-sqlbot-create-open-preview.svg"
TICKET_PREVIEW = ROOT / "docs" / "agenthost-data-mcp-sqlbot-ticket-preview.svg"
DEVELOPMENT_PREVIEW = ROOT / "docs" / "agenthost-data-mcp-sqlbot-development-route-preview.svg"
DEVELOPMENT_CREATE_PREVIEW = ROOT / "docs" / "agenthost-data-mcp-sqlbot-development-create-preview.svg"
DEVELOPMENT_ASK_PREVIEW = ROOT / "docs" / "agenthost-data-mcp-sqlbot-development-ask-preview.svg"
REPORT_SOURCE = Path("/Users/a159358/Downloads/数巢问数智能体设计-2026-09-21.html")

COLORS = {
    "live": ("#E8F5E9", "#2E7D32", False),
    "code": ("#E3F2FD", "#1565C0", False),
    "partial": ("#FFF3E0", "#EF6C00", False),
    "external": ("#F5F5F5", "#757575", False),
    "gap": ("#FFEBEE", "#D32F2F", True),
    "poc": ("#F3E5F5", "#7B1FA2", False),
    "neutral": ("#FFFFFF", "#455A64", False),
}

TICKET_FAILURE_FLOWS = [
    ("t8", "tf1", "拒绝", "gap"),
    ("t11", "tf2", "不同源", "gap"),
    ("t14", "tf3", "一次重试后失败", "gap"),
]

ASK_FAILURE_FLOWS = [
    ("q07", "qe1", "拒绝", "gap"),
    ("q09", "qe2", "无权 / 不同源", "gap"),
    ("q11", "qe3", "一次重试后失败", "gap"),
]


def model(width: int, height: int):
    graph = ET.Element("mxGraphModel", {
        "dx": str(width), "dy": str(height), "grid": "1", "gridSize": "10",
        "guides": "1", "tooltips": "1", "connect": "1", "arrows": "1",
        "fold": "1", "page": "1", "pageScale": "1", "pageWidth": str(width),
        "pageHeight": str(height), "math": "0", "shadow": "0",
    })
    root = ET.SubElement(graph, "root")
    ET.SubElement(root, "mxCell", {"id": "0"})
    ET.SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    return graph, root


def text(root, cell_id, value, x, y, width, height, size=16, color="#263238"):
    cell = ET.SubElement(root, "mxCell", {
        "id": cell_id, "value": value, "vertex": "1", "parent": "1",
        "style": "text;html=1;strokeColor=none;fillColor=none;whiteSpace=wrap;"
                 f"align=left;verticalAlign=middle;fontSize={size};fontColor={color};",
    })
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height), "as": "geometry"
    })


def box(root, cell_id, value, x, y, width, height, status="neutral"):
    fill, stroke, dashed = COLORS[status]
    line = "dashed=1;dashPattern=8 4;strokeWidth=3;" if dashed else "strokeWidth=2;"
    cell = ET.SubElement(root, "mxCell", {
        "id": cell_id, "value": value, "vertex": "1", "parent": "1",
        "style": "rounded=1;whiteSpace=wrap;html=1;align=left;verticalAlign=middle;"
                 f"spacing=10;fontSize=13;fontColor=#263238;fillColor={fill};strokeColor={stroke};{line}",
    })
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height), "as": "geometry"
    })


def lane(root, cell_id, title, x, width, height, y=80):
    cell = ET.SubElement(root, "mxCell", {
        "id": cell_id, "value": title, "vertex": "1", "parent": "1",
        "style": "swimlane;html=1;rounded=0;startSize=42;horizontal=1;fillColor=#ECEFF1;"
                 "swimlaneFillColor=#FAFAFA;strokeColor=#B0BEC5;fontStyle=1;fontSize=14;",
    })
    ET.SubElement(cell, "mxGeometry", {
        "x": str(x), "y": str(y), "width": str(width), "height": str(height), "as": "geometry"
    })


def edge(root, cell_id, source, target, label="", status="neutral", points=None, horizontal=False):
    _, stroke, dashed = COLORS[status]
    line = "dashed=1;dashPattern=8 4;strokeWidth=3;" if dashed else "strokeWidth=2;"
    ports = ("exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
             if horizontal else
             "exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;entryDx=0;entryDy=0;"
             if points else "")
    cell = ET.SubElement(root, "mxCell", {
        "id": cell_id, "value": label, "edge": "1", "parent": "1", "source": source, "target": target,
        "style": "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;" + ports +
                 f"endArrow=block;endFill=1;strokeColor={stroke};{line}",
    })
    geometry = ET.SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry"})
    if points:
        array = ET.SubElement(geometry, "Array", {"as": "points"})
        for x, y in points:
            ET.SubElement(array, "mxPoint", {"x": str(x), "y": str(y)})


def page_legend():
    graph, root = model(1900, 1120)
    text(root, "title", "<b>数巢问数智能体 × Agent Host × Data MCP × SQLBot</b><br>"
         "<font color='#607D8B'>更新版报告：逻辑域、物理服务与证据等级</font>", 40, 20, 1700, 55, 24)
    labels = [("LIVE · 运行验证", "live"), ("CODE · 源码支持", "code"),
              ("PARTIAL · 部分支持", "partial"), ("EXTERNAL · 外部待确认", "external"),
              ("GAP · 未实现/需开发", "gap")]
    for i, (label, status) in enumerate(labels):
        box(root, f"lg{i}", f"<b>{label}</b>", 60 + (i % 3) * 500, 115 + (i // 3) * 100, 430, 66, status)
    systems = [
        ("用户 / Davinci 前端", "创建、发布、切换、展示"),
        ("Agent Host", "Session、澄清、改写、分析；不持有智能体主数据"),
        ("逻辑 Data MCP 域", "智能体、权限、Ticket、投影、SQLBot 编排、缓存"),
        ("线上 Java Asset MCP", "当前仅资产、Schema、字段、显式权限；12 个只读工具"),
        ("SQLBot REST 8000", "Assistant、Chat、NL2SQL、执行、SSE、Record Data"),
        ("davinci-api / StarRocks", "权限事实、基础 SQL、受控执行数据面"),
    ]
    text(root, "boundary", "<b>物理与逻辑边界</b>", 60, 345, 400, 40, 20)
    for i, (name, desc) in enumerate(systems):
        box(root, f"sys{i}", f"<b>{name}</b><br>{desc}", 60 + (i % 3) * 500, 400 + (i // 3) * 120, 430, 82, "neutral")
    box(root, "warning", "<b>关键判定</b><br>当前 Java MCP ≠ 报告中的完整 Data MCP；Python davinci_data_mcp ≠ 线上实例；"
        "报告设计 ≠ 已实现；Mock 永远不提高生产支持度。", 60, 680, 1430, 100, "partial")
    box(root, "scope", "<b>硬约束</b><br>Agent Host 与 SQLBot 源码保持不变；只修改 AgentHost2SQLbot；"
        "缺失能力在真实顺序中使用红色加粗虚线；SQLBot 主链只使用 8000。", 60, 810, 1430, 92, "code")
    return graph


def page_baseline():
    graph, root = model(1900, 1360)
    text(root, "title", "<b>更新版报告功能基线与真实效果</b><br>"
         "<font color='#607D8B'>15 个章节归并为 10 个可验收功能域</font>", 40, 20, 1700, 55, 24)
    cards = [
        ("01", "范围与边界", "前端直连 Data MCP 创建；Host 只做会话/分析；SQLBot 写 SQL"),
        ("02", "创建与开启", "Data MCP REST/DB/Assistant 生命周期；Host 只接可信 agentId"),
        ("03", "一次提问", "澄清 → data.ask → Ticket → start/question → callback → data"),
        ("04", "数据集与字段", "≤5 表全量；授权后 Top40；一次 Top100；cannot_generate"),
        ("05", "SQLBot 接口", "Assistant REST、SSE、Record Data；mcp_assistant 仅 Spike"),
        ("06", "结果语义", "sql-data 非行数据；logicalSql ≠ executedSql"),
        ("07", "分析与图表", "Host 分析；前端组件；SQLBot chart 只作建议"),
        ("08", "权限与安全", "可信身份、Ticket 绑定、最小账号、密钥轮换、下载再鉴权"),
        ("09", "数据模型", "智能体/session/ticket/cache 在 Data MCP；Host 不建智能体表"),
        ("10", "部署与风险", "线上 Java MCP；Python 非线上；SQLBot 版本/digest 待对齐"),
    ]
    for i, (rid, title_, body) in enumerate(cards):
        box(root, f"card{i}", f"<b>RPT-{rid} · {title_}</b><br>{body}",
            70 + (i % 2) * 840, 115 + (i // 2) * 205, 760, 145, "neutral")
    box(root, "gate", "<b>支持度规则</b><br>每个功能点必须同时给出报告目标、责任系统、源码证据、运行证据、"
        "失败路径和生产验收条件；设计、源码、线上实例和 Mock 分栏记录。", 70, 1160, 1600, 105, "partial")
    return graph


def create_spec():
    names = ["用户 / 前端", "Agent Host", "逻辑 Data MCP", "线上 Java ACL MCP", "SQLBot REST", "Data MCP DB / 审计"]
    widths = [230, 285, 320, 285, 285, 310]
    nodes = [
        ("c1", 0, 145, "<b>选择 1–5 数据集</b><br>名称、简介、datasetRef", "neutral", 78),
        ("c2", 2, 145, "<b>POST /data-agents</b><br>可信 Davinci 身份、参数校验<br><font color='#1565C0'>POC CODE</font>", "code", 104),
        ("c3", 3, 270, "<b>access.check_resources</b><br>159358 成功/字段子集；149502 拒绝<br><font color='#2E7D32'>LIVE</font>", "live", 104),
        ("c4", 2, 390, "<b>复核全部数据集权限</b><br>UI 筛选不是安全边界<br><font color='#1565C0'>POC CODE · fail closed</font>", "code", 100),
        ("c5", 5, 390, "<b>写智能体草稿</b><br>data_agent + data_agent_dataset + audit<br><font color='#1565C0'>POC CODE</font>", "code", 106),
        ("c6", 2, 520, "<b>同步 SQLBot Assistant</b><br>已有 assistantId 时更新；创建失败不发布<br><font color='#EF6C00'>POC PARTIAL</font>", "partial", 96),
        ("c7", 4, 520, "<b>创建 type=1 Assistant</b><br>POST /api/v1/system/assistant<br><font color='#1565C0'>CODE + OpenAPI</font>", "code", 100),
        ("c8", 5, 645, "<b>保存 assistantId / 发布</b><br>同步失败不发布；删除远端补偿待补<br><font color='#EF6C00'>POC PARTIAL</font>", "partial", 108),
        ("c9", 0, 790, "<b>切换已发布智能体</b><br>选择 agentId", "neutral", 78),
        ("c10", 2, 790, "<b>GET visible agents</b><br>按已认证 owner 隔离<br><font color='#1565C0'>POC CODE</font>", "code", 96),
        ("c11", 1, 915, "<b>创建 Host Session</b><br>现有 Session/Workspace 基础<br><font color='#2E7D32'>LIVE</font>", "live", 96),
        ("c12", 1, 1035, "<b>可信 agentId bootstrap</b><br>服务端绑定 user / agent / Host Session<br><font color='#1565C0'>HOST CODE</font>", "code", 100),
        ("c13", 1, 1160, "<b>装载问数上下文</b><br>澄清/改写/分析 Skill<br><font color='#EF6C00'>PARTIAL</font>", "partial", 96),
        ("c14", 0, 1275, "<b>进入可提问状态</b><br>此时不创建 SQLBot Chat", "neutral", 82),
        ("c15", 4, 1275, "<b>SQLBot Chat 不存在</b><br>首次 data.ask 才 start<br><font color='#2E7D32'>时序可支持</font>", "live", 88),
    ]
    flows = [
        ("c1", "c2", "创建", "code"), ("c2", "c3", "权限复核", "code"),
        ("c3", "c4", "授权结果", "code"), ("c4", "c5", "全部通过", "code"),
        ("c5", "c6", "agentId", "partial"), ("c6", "c7", "管理服务账号", "partial"),
        ("c7", "c8", "assistantId", "partial"), ("c9", "c10", "列表", "code"),
        ("c10", "c11", "agentId", "code"), ("c11", "c12", "Session", "code"),
        ("c12", "c13", "可信上下文", "partial"), ("c13", "c14", "AG-UI", "neutral"),
        ("c14", "c15", "不调用 SQLBot", "neutral"),
    ]
    return names, widths, nodes, flows


def ticket_spec():
    names = ["用户 / 前端", "Agent Host", "逻辑 Data MCP / Data Agent", "外部 Asset ACL MCP", "SQLBot 高级小助手", "MySQL / StarRocks", "结果与失败终态"]
    widths = [210, 275, 350, 300, 320, 270, 310]
    nodes = [
        ("t1", 0, 135, "<b>1 用户提问</b><br>当前问数智能体会话", "neutral", 78),
        ("t2", 1, 245, "<b>2 可信会话上下文</b><br>服务端取得 user / agent / session<br><font color='#2E7D32'>HOST CODE</font>", "code", 96),
        ("t3", 1, 365, "<b>3 澄清与问题改写</b><br>生成可独立执行问题<br><font color='#EF6C00'>HOST PARTIAL</font>", "partial", 94),
        ("t4", 1, 485, "<b>4 调用 data.ask</b><br>进程内 MCP；身份与 session 由 Host 注入<br><font color='#2E7D32'>HOST CODE</font>", "live", 108),
        ("t5", 2, 620, "<b>5 读取智能体与 Chat 映射</b><br>签 Ticket：user / agent / session / questionHash / 60s<br><font color='#1565C0'>POC CODE</font>", "code", 116),
        ("t6", 4, 765, "<b>6 assistant/start 或 question</b><br>Assistant JWT + 请求级 Certificate<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 104),
        ("t7", 4, 895, "<b>7 GET 动态数据源回调</b><br>X-Davinci-Ticket 原样转发<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 104),
        ("t8", 2, 1025, "<b>8 校验 Ticket</b><br>TTL、绑定关系、原子计数、最多两次回调<br><font color='#1565C0'>POC CODE</font>", "code", 108),
        ("t9", 3, 1165, "<b>9 access.check_resources</b><br>按 ticket 用户重新复核全部资源<br><font color='#2E7D32'>客户端 + 外部工具 LIVE</font>", "live", 108),
        ("t10", 3, 1300, "<b>10 catalog.get_dataset_schema</b><br>授权字段、requiredFilters、metadataVersion<br><font color='#2E7D32'>客户端 + 外部工具 LIVE</font>", "live", 110),
        ("t11", 2, 1440, "<b>11 同源校验与字段投影</b><br>字段一致才放行；必保字段 + Top40<br><font color='#D32F2F'>同源 assetRef 尚未取得</font>", "gap", 116),
        ("t12", 2, 1585, "<b>12 构造动态数据源</b><br>连接 + tables[].sql + 授权 fields<br><font color='#1565C0'>POC CODE；配置后启用</font>", "code", 112),
        ("t13", 4, 1730, "<b>13 接收裁决后数据源</b><br>ACL 失败时不得获得 DB 凭据<br><font color='#1565C0'>SQLBOT CONTRACT</font>", "code", 104),
        ("t14", 4, 1865, "<b>14 NL2SQL / 校验 / 子查询替换</b><br>logicalSql → real_execute_sql<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 112),
        ("t15", 5, 2010, "<b>15 回调授权后连接数据库</b><br>测试可用高权限 MySQL；生产必须只读最小账号<br><font color='#7B1FA2'>POC 风险</font>", "poc", 116),
        ("t16", 4, 2155, "<b>16 SSE + Record Data</b><br>先取 recordId，再 GET /record/{id}/data<br><font color='#1565C0'>SQLBOT + POC CODE</font>", "code", 112),
        ("t17", 2, 2300, "<b>17 标准化 / 缓存 / evidence</b><br>首屏≤200、缓存≤1000、10min TTL、审计<br><font color='#1565C0'>POC CODE</font>", "code", 114),
        ("t18", 1, 2445, "<b>18 Host 业务分析</b><br>核对 SQL 与取数依据后作答<br><font color='#EF6C00'>HOST PARTIAL</font>", "partial", 102),
        ("t19", 0, 2580, "<b>19 表格 / 分析 / 图表</b><br>前端获得明确成功终态", "neutral", 88),
        ("tf1", 6, 1023, "<b>失败终态 A · invalid_ticket</b><br>无效、过期、完成或超次数<br><font color='#2E7D32'>FAIL-CLOSED CODE</font>", "live", 112),
        ("tf2", 6, 1440, "<b>失败终态 B · asset_mismatch</b><br>无权、超时、Schema 不同源或需行过滤<br><font color='#2E7D32'>FAIL-CLOSED CODE</font>", "live", 112),
        ("tf3", 6, 1867, "<b>失败终态 C · cannot_generate</b><br>仅一次 Top100；仍失败则终止<br><font color='#1565C0'>POC CODE</font>", "code", 108),
    ]
    flows = [
        ("t1", "t2", "原问题", "neutral"), ("t2", "t3", "可信上下文", "partial"),
        ("t3", "t4", "自足问题", "partial"), ("t4", "t5", "data.ask", "code"),
        ("t5", "t6", "ticket/certificate", "code"), ("t6", "t7", "SQLBot 内部", "code"),
        ("t7", "t8", "Ticket", "code"), ("t8", "t9", "可信 obId + refs", "code"),
        ("t9", "t10", "authorized refs", "live"), ("t10", "t11", "授权 Schema", "gap"),
        ("t11", "t12", "同源且授权", "gap"), ("t12", "t13", "动态载荷", "code"),
        ("t13", "t14", "M-Schema", "code"), ("t14", "t15", "real_execute_sql", "neutral"),
        ("t15", "t16", "rows", "neutral"), ("t16", "t17", "recordId + data", "code"),
        ("t17", "t18", "标准结果", "code"), ("t18", "t19", "AG-UI", "partial"),
    ]
    return names, widths, nodes, flows


def swimlane_page(title_, subtitle, names, widths, nodes, flows, height, fixed_vertical=False,
                  side_flows=None):
    page_width = sum(widths) + 40
    graph, root = model(page_width, height + 110)
    text(root, "title", f"<b>{title_}</b><br><font color='#607D8B'>{subtitle}</font>", 30, 15, page_width - 100, 55, 24)
    xs = [20]
    for width in widths[:-1]:
        xs.append(xs[-1] + width)
    for i, (name, x, width) in enumerate(zip(names, xs, widths)):
        lane(root, f"lane{i}", name, x, width, height)
    positions = {}
    for node_id, lane_idx, y, label, status, node_height in nodes:
        x, width = xs[lane_idx] + 12, widths[lane_idx] - 24
        positions[node_id] = (x, y, width, node_height)
        box(root, node_id, label, x, y, width, node_height, status)
    for i, (source, target, label, status) in enumerate(flows):
        points = None
        if fixed_vertical:
            sx, sy, sw, sh = positions[source]
            tx, ty, tw, _ = positions[target]
            middle_y = round((sy + sh + ty) / 2)
            points = [(round(sx + sw / 2), middle_y), (round(tx + tw / 2), middle_y)]
        edge(root, f"edge{i}", source, target, label, status, points)
    for i, (source, target, label, status) in enumerate(side_flows or []):
        edge(root, f"side-edge{i}", source, target, label, status, horizontal=True)
    return graph


def development_route_spec():
    names = [
        "用户 / 前端", "Agent Host", "逻辑 Data MCP", "Java ACL / davinci-api",
        "SQLBot 高级小助手", "MySQL / 结果缓存", "分析与展示",
    ]
    widths = [270, 305, 390, 305, 330, 305, 330]
    nodes = [
        ("d01", 0, 900, "<b>1 创建智能体</b><br>名称、简介、1–5 个数据集<br><font color='#D32F2F'>前端待开发</font>", "gap", 88),
        ("d02", 2, 1015, "<b>2 REST 创建 / 发布</b><br>校验身份、数据集数量与状态<br><font color='#D32F2F'>Data MCP 待开发</font>", "gap", 92),
        ("d03", 3, 1135, "<b>3 授权、字段、基础 SQL</b><br>Java ACL 已有目录/显式权限；完整供数接口缺失", "partial", 96),
        ("d04", 2, 1260, "<b>4 持久化并同步 Assistant</b><br>data_agent / dataset / outbox<br><font color='#D32F2F'>待开发</font>", "gap", 98),
        ("d05", 4, 1385, "<b>5 创建 type=1 Assistant</b><br>动态数据源回调；一智能体一小助手<br><font color='#1565C0'>SQLBot 已支持</font>", "code", 100),
        ("d06", 0, 1510, "<b>6 开启已发布智能体</b><br>选择 agentId，进入对话", "gap", 82),
        ("d07", 1, 1620, "<b>7 Bootstrap Host Session</b><br>服务端注入可信 agentId；Host 不存主数据", "gap", 94),
        ("d08", 1, 1740, "<b>8 澄清并改写问题</b><br>形成可独立执行的问题<br><font color='#EF6C00'>现有能力部分复用</font>", "partial", 94),
        ("d09", 1, 1860, "<b>9 调用 data.ask</b><br>agentId + question + sessionKey<br><font color='#D32F2F'>MCP 工具待开发</font>", "gap", 98),
        ("d10", 2, 1985, "<b>10 校验并签发 Ticket</b><br>绑定 user/agent/session/question；TTL 60s<br>生成 Assistant token", "gap", 104),
        ("d11", 4, 2115, "<b>11 start / question</b><br>携带请求级 Certificate<br><font color='#1565C0'>SQLBot 已支持</font>", "code", 94),
        ("d12", 4, 2235, "<b>12 回调动态数据源</b><br>GET /api/sqlbot/datasources<br>X-Davinci-Ticket", "code", 98),
        ("d13", 2, 2360, "<b>13 Ticket 校验与供数</b><br>权限后字段投影 Top40/Top100<br>行列权限写入 fields + tables[].sql", "gap", 108),
        ("d14", 5, 2495, "<b>14 SQLBot 直连 MySQL</b><br>仅 SELECT-only 账号；生成并执行 SQL<br><font color='#7B1FA2'>POC 使用 knowledge.sync_job</font>", "gap", 104),
        ("d15", 4, 2625, "<b>15 SSE + Record Data</b><br>消费执行状态，再取 /record/{id}/data<br><font color='#1565C0'>SQLBot 接口已支持</font>", "code", 102),
        ("d16", 2, 2755, "<b>16 标准化与缓存</b><br>SQL、columns、rows≤200、evidence、chartHint", "gap", 98),
        ("d17", 1, 2880, "<b>17 Host 分析</b><br>核对依据，形成解释与图表决策", "gap", 90),
        ("d18", 6, 2995, "<b>18 前端原生展示</b><br>表格 / 图表切换；明确成功或失败终态", "gap", 96),
    ]
    flows = [
        ("d01", "d02", "REST", "gap"), ("d02", "d03", "数据集 refs", "gap"),
        ("d03", "d04", "授权快照", "gap"), ("d04", "d05", "管理 API", "gap"),
        ("d05", "d06", "发布完成", "gap"), ("d06", "d07", "agentId", "gap"),
        ("d07", "d08", "可信上下文", "gap"), ("d08", "d09", "自足问题", "gap"),
        ("d09", "d10", "data.ask", "gap"), ("d10", "d11", "ticket/certificate", "gap"),
        ("d11", "d12", "SQLBot 内部", "code"), ("d12", "d13", "Ticket", "gap"),
        ("d13", "d14", "虚拟表 + 只读连接", "gap"), ("d14", "d15", "执行结果", "gap"),
        ("d15", "d16", "recordId + rows", "gap"), ("d16", "d17", "结构化结果", "gap"),
        ("d17", "d18", "AG-UI / 结果引用", "gap"),
    ]
    return names, widths, nodes, flows


REPORT_RULES = [
    ("§1/§5", "创建不经 Host", "前端直连 Data MCP REST；Host 只在对话时登场"),
    ("§2", "单一逻辑 Data MCP", "REST、data.ask、Ticket、回调、权限与缓存同属一个逻辑域"),
    ("§3", "一智能体一 Assistant", "一个 data_agent 映射一个 SQLBot type=1 高级小助手"),
    ("§4/§7", "≤5 数据集 + 字段投影", "授权后 Top40；cannot_generate 时仅一次 Top100"),
    ("§4/§10", "权限烘入虚拟表", "使用 tables[].fields 与 tables[].sql；不使用 SQLBot rule"),
    ("§1/§8", "SQLBot 零改码", "只使用高级小助手、SSE、Record Data 与直接数据库执行"),
    ("§6/§9", "Host 分析、前端画图", "Data MCP 返回 rows≤200；前端原生图表组件渲染"),
    ("§3/§11", "Host 不存智能体主数据", "智能体、dataset、ask_session、ticket、cache 均归 Data MCP"),
]


DEVELOPMENT_CARDS = [
    ("dev-fe", "前端", "列表/创建/发布/停用；≤5 数据集选择；开启会话；表格/图表/依据展示", "app/web", "code"),
    ("dev-host", "Agent Host", "可信 agentId bootstrap；内置 data.ask MCP；结果组件；业务分析仍需真实验收", "app/runtime · app/web", "partial"),
    ("dev-dm", "Data MCP 核心", "智能体 REST/表；Ticket；动态回调；字段投影；TTL 缓存与审计", "app/data_mcp", "code"),
    ("dev-sb", "SQLBot 适配", "管理登录；Assistant 同步；JWT；start/question；SSE 与 Record Data", "app/sqlbot（SQLBot 源码不改）", "partial"),
    ("dev-sec", "身份与数据库安全", "159358 服务端身份；ACL 二次复核；凭证脱敏；回调 CIDR 可配；最小账号待置换", "app/data_mcp · 本地 secrets", "partial"),
    ("dev-accept", "验收与文档", "合同/集成测试与 Drawio 状态回填完成；真实同源 E2E 仍受门禁阻塞", "tests · docs", "partial"),
]


def page_development_route():
    width, height = 2360, 4230
    graph, root = model(width, height)
    text(root, "dev-title", "<b>功能开发清单与预期调用 / 实现路线</b><br>"
         "<font color='#607D8B'>权威源：数巢问数智能体设计-2026-09-21.html · 本页确认后才进入功能开发</font>",
         35, 18, 2200, 60, 24)
    box(root, "dev-legend-existing", "<b>蓝/绿实线</b><br>源码或运行已验证", 45, 95, 330, 72, "code")
    box(root, "dev-legend-gap", "<b>红色粗虚线</b><br>当前缺失，需投入开发", 400, 95, 360, 72, "gap")
    box(root, "dev-legend-poc", "<b>紫色边框</b><br>POC 与生产报告存在差异", 785, 95, 390, 72, "poc")
    box(root, "dev-legend-gate", "<b>确认门禁</b><br>本 TAB 经用户确认前，不开始功能开发", 1200, 95, 600, 72, "partial")

    text(root, "report-title", "<b>A. 报告约束核对</b>", 45, 195, 600, 36, 19)
    for i, (section, title_, body) in enumerate(REPORT_RULES):
        col, row = i % 4, i // 4
        box(root, f"rule-{i}", f"<b>{section} · {title_}</b><br>{body}",
            45 + col * 570, 245 + row * 150, 535, 115, "neutral")

    text(root, "route-title", "<b>B. 预期完整调用链</b><br>"
         "<font color='#607D8B'>步骤 1–5 创建阶段不经过 Host；步骤 6 起 Host 参与开启、澄清、data.ask 与分析</font>",
         45, 565, 1700, 48, 19)
    names, widths, nodes, flows = development_route_spec()
    xs = [20]
    for lane_width in widths[:-1]:
        xs.append(xs[-1] + lane_width)
    for i, (name, x, lane_width) in enumerate(zip(names, xs, widths)):
        lane(root, f"dev-lane-{i}", name, x, lane_width, 2360, y=630)
    positions = {}
    for node_id, lane_idx, y, label, status, node_height in nodes:
        x, node_width = xs[lane_idx] + 12, widths[lane_idx] - 24
        positions[node_id] = (x, y, node_width, node_height)
        box(root, node_id, label, x, y, node_width, node_height, status)
    for i, (source, target, label, status) in enumerate(flows):
        sx, sy, sw, sh = positions[source]
        tx, ty, tw, _ = positions[target]
        middle_y = round((sy + sh + ty) / 2)
        edge(root, f"dev-edge-{i}", source, target, label, status,
             [(round(sx + sw / 2), middle_y), (round(tx + tw / 2), middle_y)])

    text(root, "checklist-title", "<b>C. 功能开发清单、代码架构位置与验收责任</b>", 45, 3290, 1300, 40, 19)
    for i, (cell_id, title_, body, location, status) in enumerate(DEVELOPMENT_CARDS):
        col, row = i % 3, i // 3
        box(root, cell_id, f"<b>{title_}</b><br>{body}<br><font color='#607D8B'>落点：{location}</font>",
            45 + col * 760, 3350 + row * 225, 720, 185, status)

    box(root, "poc-diff", "<b>POC 与生产报告的显式差异</b><br>"
        "① 报告目标为 StarRocks，本地验证使用 MySQL knowledge.sync_job；"
        "② 报告期望独立 Data MCP，本项目先同进程分模块；"
        "③ POC 数据集没有 Davinci datasetId，由真实 MySQL provider 实施固定用户、表、字段和 baseSQL 白名单；"
        "④ 线上 Java 服务正式合并、Davinci 基础 SQL/行权限接口和正式前端契约仍是生产缺口。",
        45, 3825, 1470, 205, "poc")
    box(root, "approval-gate", "<b>用户确认门禁</b><br>交付本 TAB、SVG/PNG 预览和报告一致性结果后停止。"
        "只有用户明确确认布局、职责、链路和开发清单，才开始 Data MCP、Host、前端、MySQL 和 SQLBot 联调开发。",
        1550, 3825, 755, 205, "partial")
    return graph


def page_development_overview():
    width, height = 2240, 2100
    graph, root = model(width, height)
    text(root, "dev-title", "<b>07 · 功能实施状态与真实验收门禁</b><br>"
         "<font color='#607D8B'>创建/发布/开启与一次提问已拆到 07A、07B；蓝绿为已落地，红色虚线为仍阻塞真实链路的缺口</font>",
         35, 18, 2140, 60, 24)
    box(root, "dev-legend-existing", "<b>蓝/绿实线</b><br>源码或运行已验证", 45, 100, 330, 72, "code")
    box(root, "dev-legend-gap", "<b>红色粗虚线</b><br>未满足报告，需开发", 400, 100, 350, 72, "gap")
    box(root, "dev-legend-poc", "<b>紫色边框</b><br>仅测试阶段接受", 775, 100, 340, 72, "poc")
    box(root, "dev-legend-gate", "<b>真实验收门禁</b><br>同源资产、部署 Secret 与运行凭证齐备后执行 E2E", 1140, 100, 620, 72, "partial")

    text(root, "report-title", "<b>A. 与报告逐项对齐的硬约束</b>", 45, 210, 900, 36, 19)
    for i, (section, title_, body) in enumerate(REPORT_RULES):
        col, row = i % 4, i // 4
        box(root, f"overview-rule-{i}", f"<b>{section} · {title_}</b><br>{body}",
            45 + col * 535, 265 + row * 145, 505, 108, "neutral")

    text(root, "components-title", "<b>B. 开发清单与替换位置</b>", 45, 590, 900, 36, 19)
    overview_cards = [
        ("overview-host", "Agent Host", "ClaudeAgentRuntime；可信会话；进程内 data.ask；结果组件与 AG-UI", "app/runtime/claude.py · app/web", "partial"),
        ("overview-orchestrator", "Data Agent 编排", "智能体 REST/表、Ticket、回调、字段投影、TTL 缓存、审计", "app/data_mcp", "code"),
        ("overview-acl", "外部 Asset ACL MCP", "确定性 MCP 客户端；check_resources + get_dataset_schema；失败关闭", "app/data_mcp/asset_mcp.py", "live"),
        ("overview-runtime", "数据运行时 Provider", "同源字段校验、受控 baseSQL 与连接载荷；assetRef 待配置", "app/data_mcp/providers.py", "partial"),
        ("overview-sqlbot", "SQLBot 适配", "管理登录、Assistant CRUD、运行容器 Secret、JWT 鉴权已验证；问数受同源门禁阻塞", "app/sqlbot/client.py", "code"),
        ("overview-web", "前端与验收", "创建/发布/开启；表格/图表/依据；配置阻塞项；自动化链路测试", "app/web · tests · docs", "partial"),
    ]
    for i, (cell_id, title_, body, location, status) in enumerate(overview_cards):
        col, row = i % 3, i // 3
        box(root, cell_id, f"<b>{title_}</b><br>{body}<br><font color='#607D8B'>落点：{location}</font>",
            45 + col * 715, 650 + row * 205, 680, 165, status)

    text(root, "gates-title", "<b>C. 真实端到端验收门禁</b>", 45, 1095, 900, 36, 19)
    gates = [
        ("gate-asset", "同源资产", "先证明 MCP resource 与 knowledge.sync_job 是同一资产；不得用无关有权样本代替", "gap"),
        ("gate-secret", "SQLBot SECRET_KEY", "已取运行容器部署级 Secret 并通过临时 Assistant JWT 鉴权；API Key Secret 未混用", "live"),
        ("gate-network", "双向网络", "SQLBot 可达回调与数据库；Data Agent 可达 SQLBot 与外部 MCP", "partial"),
        ("gate-security", "测试账号风险", "高权限 MySQL 仅用于测试；只存本地环境变量；生产切换只读最小账号", "poc"),
        ("gate-row", "行权限事实", "Asset MCP 未提供基础 SQL/行谓词；非空 requiredFilters 正样本仍缺失", "gap"),
        ("gate-failclosed", "失败关闭", "MCP 拒绝/超时、ticket 异常、schema 不一致均不得下发连接", "live"),
    ]
    for i, (cell_id, title_, body, status) in enumerate(gates):
        col, row = i % 3, i // 3
        box(root, cell_id, f"<b>{title_}</b><br>{body}",
            45 + col * 715, 1155 + row * 180, 680, 140, status)

    box(root, "overview-boundary", "<b>最终边界</b><br>SQLBot 不得绕过 Data Agent 使用静态数据源；只有 Ticket 与外部 MCP 复核通过后，"
        "Data Agent 才返回临时连接和裁决后 Schema。随后仍由 SQLBot 执行 SQL——这是报告零改 SQLBot 的既定方案。",
        45, 1570, 1390, 150, "code")
    box(root, "overview-approval", "<b>当前实施结论</b><br>SQLBot、数据库和部署 Secret 已就绪；真实 E2E 不伪造通过。"
        "当前核心阻塞仅剩同源 assetRef；行权限与回调 CIDR 仍需生产验收。", 1470, 1570, 690, 150, "partial")
    return graph


def development_create_spec():
    names = ["用户 / 前端", "逻辑 Data MCP / Data Agent", "外部 Asset ACL MCP", "Data Agent DB", "SQLBot 管理 API", "Agent Host"]
    widths = [250, 370, 330, 300, 330, 320]
    nodes = [
        ("a01", 0, 140, "<b>1 选择 1–5 个数据集</b><br>名称、简介、datasetRef<br><font color='#EF6C00'>POC UI 已有；目录仍是固定 Provider</font>", "partial", 110),
        ("a02", 1, 285, "<b>2 POST /api/data-agents</b><br>只信服务端身份；校验数量与状态<br><font color='#1565C0'>POC CODE</font>", "code", 108),
        ("a03", 2, 430, "<b>3 access.check_resources</b><br>以可信 obId=159358 复核全部 refs<br><font color='#2E7D32'>客户端 + 外部工具 LIVE</font>", "live", 116),
        ("a04", 2, 575, "<b>4 catalog.get_dataset_schema</b><br>取得授权字段、requiredFilters、版本<br><font color='#2E7D32'>客户端 + 外部工具 LIVE</font>", "live", 116),
        ("a05", 1, 720, "<b>5 同源映射门禁</b><br>MCP resource ↔ knowledge.sync_job / 物理字段<br><font color='#D32F2F'>代码已失败关闭；同源 assetRef 尚缺</font>", "gap", 116),
        ("a06", 3, 865, "<b>6 保存草稿与数据集快照</b><br>data_agent + data_agent_dataset + audit<br><font color='#1565C0'>POC CODE</font>", "code", 110),
        ("a07", 1, 1010, "<b>7 幂等同步高级小助手</b><br>configuration 只含回调地址与凭证映射<br><font color='#1565C0'>POC CODE</font>", "code", 116),
        ("a08", 4, 1155, "<b>8 创建 type=1 Assistant</b><br>管理账号调用；不登记静态数据源<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 108),
        ("a09", 3, 1300, "<b>9 保存 assistantId / 发布</b><br>同步失败不得发布；更新/删除需补偿<br><font color='#EF6C00'>POC PARTIAL</font>", "partial", 112),
        ("a10", 0, 1460, "<b>10 开启已发布智能体</b><br>用户选择 agentId 进入对话", "neutral", 86),
        ("a11", 1, 1585, "<b>11 校验可见性并绑定 Session</b><br>POST /{agentId}/open<br><font color='#1565C0'>POC CODE</font>", "code", 106),
        ("a12", 5, 1725, "<b>12 创建 data-question Session</b><br>服务端注入可信 agentId；Host 不存主数据<br><font color='#1565C0'>HOST CODE</font>", "code", 112),
        ("a13", 5, 1870, "<b>13 装载问数 Workspace</b><br>ClaudeAgentRuntime + data.ask MCP + 分析规则<br><font color='#EF6C00'>PARTIAL</font>", "partial", 112),
        ("a14", 0, 2015, "<b>14 进入可提问状态</b><br>此时尚未创建 SQLBot Chat", "neutral", 88),
    ]
    flows = [
        ("a01", "a02", "创建", "partial"), ("a02", "a03", "可信 obId + refs", "code"),
        ("a03", "a04", "全部授权", "live"), ("a04", "a05", "授权 Schema", "gap"),
        ("a05", "a06", "同源", "gap"), ("a06", "a07", "agentId", "code"),
        ("a07", "a08", "管理 API", "code"), ("a08", "a09", "assistantId", "code"),
        ("a09", "a10", "发布完成", "partial"), ("a10", "a11", "agentId", "code"),
        ("a11", "a12", "可信绑定", "code"), ("a12", "a13", "Session", "code"),
        ("a13", "a14", "AG-UI", "partial"),
    ]
    return names, widths, nodes, flows


def development_ask_spec():
    names = ["用户 / 前端", "Agent Host", "逻辑 Data MCP / Data Agent", "外部 Asset ACL MCP", "SQLBot 高级小助手", "MySQL / 缓存", "安全失败终态"]
    widths = [220, 285, 360, 315, 330, 285, 320]
    nodes = [
        ("q01", 0, 140, "<b>1 用户提问</b><br>已开启的问数智能体 Session", "neutral", 82),
        ("q02", 1, 260, "<b>2 澄清并改写</b><br>生成可独立执行问题<br><font color='#EF6C00'>HOST PARTIAL</font>", "partial", 100),
        ("q03", 1, 390, "<b>3 调用 data.ask</b><br>Claude SDK 进程内 MCP；注入 user/session<br><font color='#2E7D32'>POC LIVE</font>", "live", 108),
        ("q04", 2, 530, "<b>4 读取 agent / ask_session</b><br>签发 60s Ticket；生成 Assistant JWT<br><font color='#2E7D32'>运行容器 SECRET_KEY 已验证</font>", "live", 116),
        ("q05", 4, 675, "<b>5 assistant/start 或 question</b><br>Certificate 仅携带本次 Ticket<br><font color='#1565C0'>SQLBOT + POC CODE</font>", "code", 110),
        ("q06", 4, 815, "<b>6 GET 动态数据源回调</b><br>X-Davinci-Ticket 原样转发<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 108),
        ("q07", 2, 955, "<b>7 校验 Ticket</b><br>user / agent / session / questionHash / 原子次数<br><font color='#1565C0'>POC CODE</font>", "code", 112),
        ("q08", 3, 1095, "<b>8 二次权限复核</b><br>access.check_resources + get_dataset_schema<br><font color='#2E7D32'>CLIENT + LIVE MCP</font>", "live", 112),
        ("q09", 2, 1235, "<b>9 同源校验 + 字段投影</b><br>仅授权字段；必保字段 + Top40<br><font color='#D32F2F'>同源 assetRef 尚未取得</font>", "gap", 112),
        ("q10", 2, 1375, "<b>10 返回动态数据源</b><br>DB 连接 + tables[].sql + fields<br><font color='#1565C0'>POC CODE；配置后启用</font>", "code", 112),
        ("q11", 4, 1515, "<b>11 NL2SQL / 校验 / 子查询替换</b><br>只允许 SELECT / WITH<br><font color='#1565C0'>SQLBOT CODE</font>", "code", 110),
        ("q12", 5, 1655, "<b>12 回调授权后连接 MySQL</b><br>测试高权限账号；生产切只读最小账号<br><font color='#7B1FA2'>POC 风险</font>", "poc", 112),
        ("q13", 4, 1795, "<b>13 SSE + Record Data</b><br>sql-data 是状态；行数据另取 record/{id}/data<br><font color='#1565C0'>CODE</font>", "code", 116),
        ("q14", 2, 1940, "<b>14 标准化 / 缓存 / 审计</b><br>首屏≤200、缓存≤1000、10min TTL、evidence<br><font color='#1565C0'>POC CODE</font>", "code", 114),
        ("q15", 1, 2085, "<b>15 Host 核对并分析</b><br>SQL、口径和字段依据一致后作答<br><font color='#EF6C00'>HOST PARTIAL</font>", "partial", 108),
        ("q16", 0, 2225, "<b>16 表格 / 分析 / 图表</b><br>前端原生组件渲染", "neutral", 88),
        ("qe1", 6, 955, "<b>拒绝 A · invalid_ticket</b><br>缺失、过期、完成或超次数<br><font color='#2E7D32'>FAIL-CLOSED CODE</font>", "live", 112),
        ("qe2", 6, 1235, "<b>拒绝 B · no_permission</b><br>MCP 拒绝/超时、Schema 不同源或需行过滤<br><font color='#2E7D32'>FAIL-CLOSED CODE</font>", "live", 112),
        ("qe3", 6, 1514, "<b>拒绝 C · cannot_generate</b><br>Top100 仅重试一次；仍失败即终止<br><font color='#1565C0'>POC CODE</font>", "code", 112),
    ]
    flows = [
        ("q01", "q02", "问题", "neutral"), ("q02", "q03", "自足问题", "partial"),
        ("q03", "q04", "data.ask", "code"), ("q04", "q05", "JWT + Ticket", "live"),
        ("q05", "q06", "SQLBot 内部", "code"), ("q06", "q07", "Ticket", "code"),
        ("q07", "q08", "可信 obId + refs", "code"), ("q08", "q09", "授权 Schema", "gap"),
        ("q09", "q10", "fieldsExposed", "gap"), ("q10", "q11", "动态载荷", "code"),
        ("q11", "q12", "real_execute_sql", "neutral"), ("q12", "q13", "rows", "neutral"),
        ("q13", "q14", "recordId + data", "code"), ("q14", "q15", "标准结果", "code"),
        ("q15", "q16", "AG-UI", "partial"),
    ]
    return names, widths, nodes, flows


def validate_spec_geometry(label: str, names, widths, nodes, flows) -> None:
    """Fail generation when nodes overlap or an explicit main-line segment crosses a node."""
    if len(names) != len(widths):
        raise RuntimeError(f"{label}: 泳道名称与宽度数量不一致")
    xs = [20]
    for lane_width in widths[:-1]:
        xs.append(xs[-1] + lane_width)
    rectangles = {}
    for node_id, lane_idx, y, _value, _status, height in nodes:
        if node_id in rectangles:
            raise RuntimeError(f"{label}: 重复节点 id {node_id}")
        rectangles[node_id] = (xs[lane_idx] + 12, y, widths[lane_idx] - 24, height)
    node_ids = set(rectangles)
    for source, target, _edge_label, _status in flows:
        if source not in node_ids or target not in node_ids:
            raise RuntimeError(f"{label}: 连线端点不存在 {source}->{target}")
    items = list(rectangles.items())
    for index, (left_id, (lx, ly, lw, lh)) in enumerate(items):
        for right_id, (rx, ry, rw, rh) in items[index + 1:]:
            overlap_x = max(lx, rx) < min(lx + lw, rx + rw)
            overlap_y = max(ly, ry) < min(ly + lh, ry + rh)
            if overlap_x and overlap_y:
                raise RuntimeError(f"{label}: 节点重叠 {left_id} / {right_id}")
    for source, target, _edge_label, _status in flows:
        sx, sy, sw, sh = rectangles[source]
        tx, ty, tw, _th = rectangles[target]
        source_x, target_x = sx + sw / 2, tx + tw / 2
        middle_y = (sy + sh + ty) / 2
        min_x, max_x = sorted((source_x, target_x))
        for node_id, (nx, ny, nw, nh) in rectangles.items():
            if node_id in {source, target}:
                continue
            crosses_horizontal = min_x < nx + nw and max_x > nx and ny < middle_y < ny + nh
            if crosses_horizontal:
                raise RuntimeError(f"{label}: 连线 {source}->{target} 横穿节点 {node_id}")


def validate_side_flow_geometry(label: str, names, widths, nodes, side_flows) -> None:
    xs = [20]
    for lane_width in widths[:-1]:
        xs.append(xs[-1] + lane_width)
    rectangles = {
        node_id: (xs[lane_idx] + 12, y, widths[lane_idx] - 24, height)
        for node_id, lane_idx, y, _value, _status, height in nodes
    }
    for source, target, _edge_label, _status in side_flows:
        sx, sy, sw, sh = rectangles[source]
        tx, ty, _tw, th = rectangles[target]
        line_y = sy + sh / 2
        if not ty < line_y < ty + th:
            raise RuntimeError(f"{label}: 失败分支 {source}->{target} 未水平对齐")
        for node_id, (nx, ny, nw, nh) in rectangles.items():
            if node_id in {source, target}:
                continue
            if sx + sw < nx + nw and tx > nx and ny < line_y < ny + nh:
                raise RuntimeError(f"{label}: 失败分支 {source}->{target} 横穿节点 {node_id}")


def validate_report_alignment(drawio_text: str) -> list[str]:
    if not REPORT_SOURCE.exists():
        raise FileNotFoundError(f"报告源文件不存在: {REPORT_SOURCE}")
    raw = REPORT_SOURCE.read_text(encoding="utf-8")
    report_text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    report_text = re.sub(r"\s+", " ", report_text)
    checks = {
        "创建不经 Host": "创建不经 Host",
        "逻辑 Data MCP / data.ask": "data.ask",
        "最多 5 个数据集": "最多选 5 个数据集",
        "SQLBot 零改码": "SQLBot 零改码",
        "前端原生图表": "前端原生图表组件",
        "字段 Top40": "前 40 字段",
        "Host 不存智能体": "Host 不存任何智能体对象",
    }
    missing_report = [label for label, phrase in checks.items() if phrase not in report_text]
    required_diagram = ["07-开发总览与验收门禁", "07A-创建发布与开启实施路线",
                        "07B-一次提问与动态回调实施路线", "data.ask", "tables[].sql", "rows≤200",
                        "Agent Host", "逻辑 Data MCP", "外部 Asset ACL MCP", "SQLBot", "当前实施结论"]
    missing_diagram = [phrase for phrase in required_diagram if phrase not in drawio_text]
    if missing_report or missing_diagram:
        raise RuntimeError(f"报告核对失败: report={missing_report}, drawio={missing_diagram}")
    return list(checks)


def build_drawio():
    create = create_spec()
    ticket = ticket_spec()
    validate_spec_geometry("02A", *ticket)
    validate_spec_geometry("07A", *development_create_spec())
    validate_spec_geometry("07B", *development_ask_spec())
    validate_side_flow_geometry("02A", *ticket[:3], TICKET_FAILURE_FLOWS)
    validate_side_flow_geometry("07B", *development_ask_spec()[:3], ASK_FAILURE_FLOWS)
    mxfile = ET.Element("mxfile", {
        "host": "app.diagrams.net", "modified": "2026-09-21T18:00:00.000Z",
        "agent": "Codex", "version": "24.7.17", "type": "device", "compressed": "false",
    })
    pages = [
        ("page-00", "00-图例与系统边界", page_legend()),
        ("page-01", "01-报告功能基线与预期效果", page_baseline()),
        ("page-02", "02-问数智能体创建与开启", swimlane_page(
            "问数智能体创建、发布与开启", "更新版：智能体主数据归 Data MCP；Host 只接可信 agentId",
            *create, 1375)),
        ("page-02a", "02A-新MCP与动态Ticket验证", swimlane_page(
            "Agent Host × 外部 Asset ACL MCP × 动态 Ticket × SQLBot 回调",
            "逻辑 Data MCP 是本项目编排域；外部 MCP 只提供资产/权限/Schema；SQLBot 仅在回调授权后连接数据库",
            *ticket, 2740, True, side_flows=TICKET_FAILURE_FLOWS)),
        ("page-07", "07-开发总览与验收门禁", page_development_overview()),
        ("page-07a", "07A-创建发布与开启实施路线", swimlane_page(
            "07A · 创建、发布与开启实施路线",
            "创建不经过 Agent Host；开启对话时才创建 Host Session；MCP 权限与同源资产是发布前门禁",
            *development_create_spec(), 2150, True)),
        ("page-07b", "07B-一次提问与动态回调实施路线", swimlane_page(
            "07B · 一次提问、动态 Ticket、回调与执行实施路线",
            "Host 负责对话与分析；Data Agent 确定性调用外部 MCP；SQLBot 仅在授权回调成功后连接数据库",
            *development_ask_spec(), 2350, True, side_flows=ASK_FAILURE_FLOWS)),
    ]
    # Later evidence tabs are maintained independently; preserve them on regeneration.
    if OUTPUT.exists():
        generated_ids = {page_id for page_id, _, _ in pages}
        for existing in ET.parse(OUTPUT).getroot().findall("diagram"):
            page_id = existing.get("id")
            graph = existing.find("mxGraphModel")
            if page_id and page_id not in generated_ids and graph is not None:
                pages.append((page_id, existing.get("name") or page_id, copy.deepcopy(graph)))
    mxfile.set("pages", str(len(pages)))
    for page_id, name, graph in pages:
        diagram = ET.SubElement(mxfile, "diagram", {"id": page_id, "name": name})
        diagram.append(graph)
    ET.indent(mxfile, space="  ")
    drawio_text = ET.tostring(mxfile, encoding="unicode")
    validate_report_alignment(drawio_text)
    OUTPUT.write_text(drawio_text, encoding="utf-8")


def build_svg(path: Path, title_, subtitle, names, widths, nodes, flows, height, fixed_vertical=False,
              side_flows=None):
    scale = 0.82
    xs = [20]
    for width in widths[:-1]:
        xs.append(xs[-1] + width)
    canvas_width = int((sum(widths) + 40) * scale)
    canvas_height = int((height + 130) * scale)
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": str(canvas_width),
                             "height": str(canvas_height), "viewBox": f"0 0 {canvas_width} {canvas_height}"})
    ET.SubElement(svg, "rect", {"width": str(canvas_width), "height": str(canvas_height), "fill": "#FFFFFF"})
    style = ET.SubElement(svg, "style")
    style.text = "text{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;fill:#263238}.arrow{fill:none;marker-end:url(#a)}"
    defs = ET.SubElement(svg, "defs")
    marker = ET.SubElement(defs, "marker", {"id": "a", "markerWidth": "8", "markerHeight": "8", "refX": "7", "refY": "4", "orient": "auto"})
    ET.SubElement(marker, "path", {"d": "M0,0 L8,4 L0,8 z", "fill": "#455A64"})
    t = ET.SubElement(svg, "text", {"x": "24", "y": "30", "font-size": "20", "font-weight": "700"}); t.text = title_
    s = ET.SubElement(svg, "text", {"x": "24", "y": "52", "font-size": "12"}); s.text = subtitle
    for i, (name, x, width) in enumerate(zip(names, xs, widths)):
        sx, sw = x * scale, width * scale
        ET.SubElement(svg, "rect", {"x": str(sx), "y": str(70 * scale), "width": str(sw),
            "height": str(height * scale), "fill": "#FAFAFA", "stroke": "#B0BEC5"})
        ET.SubElement(svg, "rect", {"x": str(sx), "y": str(70 * scale), "width": str(sw),
            "height": str(42 * scale), "fill": "#ECEFF1", "stroke": "#B0BEC5"})
        label = ET.SubElement(svg, "text", {"x": str(sx + 8), "y": str(97 * scale), "font-size": "11", "font-weight": "700"}); label.text = name
    positions = {}
    for node_id, lane_idx, y, label, status, node_height in nodes:
        positions[node_id] = (xs[lane_idx] + 12, y, widths[lane_idx] - 24, node_height, status, label)
    for source, target, _, status in flows:
        sx, sy, sw, sh, _, _ = positions[source]; tx, ty, tw, th, _, _ = positions[target]
        if fixed_vertical:
            x1, y1 = (sx + sw / 2) * scale, (sy + sh) * scale
            x2, y2 = (tx + tw / 2) * scale, ty * scale
            mid_y = (y1 + y2) / 2
            path_data = f"M{x1},{y1} L{x1},{mid_y} L{x2},{mid_y} L{x2},{y2}"
        else:
            x1, y1, x2, y2 = (sx + sw) * scale, (sy + sh / 2) * scale, tx * scale, (ty + th / 2) * scale
            if tx < sx:
                x1, x2 = sx * scale, (tx + tw) * scale
            mid = (x1 + x2) / 2
            path_data = f"M{x1},{y1} L{mid},{y1} L{mid},{y2} L{x2},{y2}"
        _, stroke, dashed = COLORS[status]
        attrs = {"d": path_data, "class": "arrow",
                 "stroke": stroke, "stroke-width": "3" if dashed else "2"}
        if dashed:
            attrs["stroke-dasharray"] = "8 4"
        ET.SubElement(svg, "path", attrs)
    for source, target, _, status in side_flows or []:
        sx, sy, sw, sh, _, _ = positions[source]
        tx, ty, tw, th, _, _ = positions[target]
        x1, y1 = (sx + sw) * scale, (sy + sh / 2) * scale
        x2, y2 = tx * scale, (ty + th / 2) * scale
        _, stroke, dashed = COLORS[status]
        attrs = {"d": f"M{x1},{y1} L{x2},{y2}", "class": "arrow",
                 "stroke": stroke, "stroke-width": "3" if dashed else "2"}
        if dashed:
            attrs["stroke-dasharray"] = "8 4"
        ET.SubElement(svg, "path", attrs)
    for x, y, width, node_height, status, label in positions.values():
        fill, stroke, dashed = COLORS[status]
        attrs = {"x": str(x * scale), "y": str(y * scale), "width": str(width * scale),
                 "height": str(node_height * scale), "rx": "8", "fill": fill, "stroke": stroke,
                 "stroke-width": "3" if dashed else "2"}
        if dashed:
            attrs["stroke-dasharray"] = "8 4"
        ET.SubElement(svg, "rect", attrs)
        plain = label.replace("<b>", "").replace("</b>", "").replace("<br>", "\n")
        while "<font" in plain:
            start = plain.index("<font"); end = plain.index(">", start); plain = plain[:start] + plain[end + 1:]
            plain = plain.replace("</font>", "")
        for line_no, line in enumerate(plain.split("\n")[:4]):
            label_el = ET.SubElement(svg, "text", {"x": str((x + 9) * scale), "y": str((y + 25 + line_no * 20) * scale),
                "font-size": "10", "font-weight": "700" if line_no == 0 else "400"})
            label_el.text = line
    ET.indent(svg, space="  ")
    path.write_text(ET.tostring(svg, encoding="unicode"), encoding="utf-8")


def _style_value(style: str, key: str, default: str) -> str:
    match = re.search(rf"(?:^|;){re.escape(key)}=([^;]+)", style)
    return match.group(1) if match else default


def _plain_lines(value: str, width: int, font_size: int) -> list[str]:
    normalized = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", "", normalized)
    normalized = html.unescape(normalized)
    max_chars = max(8, int(width / max(font_size * 0.9, 1)))
    lines: list[str] = []
    for part in normalized.splitlines() or [""]:
        lines.extend(textwrap.wrap(part, width=max_chars, break_long_words=True,
                                   break_on_hyphens=False) or [""])
    return lines


def build_graph_svg(path: Path, graph: ET.Element):
    width = int(graph.attrib["pageWidth"])
    height = int(graph.attrib["pageHeight"])
    svg = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg", "width": str(width),
                             "height": str(height), "viewBox": f"0 0 {width} {height}"})
    ET.SubElement(svg, "rect", {"width": str(width), "height": str(height), "fill": "#FFFFFF"})
    style_el = ET.SubElement(svg, "style")
    style_el.text = "text{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif;fill:#263238}"
    defs = ET.SubElement(svg, "defs")
    for color in sorted({value[1] for value in COLORS.values()}):
        marker_id = "arrow-" + color.removeprefix("#")
        marker = ET.SubElement(defs, "marker", {"id": marker_id, "markerWidth": "8", "markerHeight": "8",
            "refX": "7", "refY": "4", "orient": "auto", "markerUnits": "strokeWidth"})
        ET.SubElement(marker, "path", {"d": "M0,0 L8,4 L0,8 z", "fill": color})

    cells = graph.findall("./root/mxCell")
    geometries: dict[str, tuple[float, float, float, float]] = {}
    for cell in cells:
        geo = cell.find("mxGeometry")
        if geo is not None and cell.attrib.get("vertex") == "1":
            geometries[cell.attrib["id"]] = tuple(float(geo.attrib.get(key, 0)) for key in ("x", "y", "width", "height"))

    for cell in cells:
        if cell.attrib.get("vertex") != "1" or "swimlane" not in cell.attrib.get("style", ""):
            continue
        x, y, w, h = geometries[cell.attrib["id"]]
        ET.SubElement(svg, "rect", {"x": str(x), "y": str(y), "width": str(w), "height": str(h),
            "fill": "#FAFAFA", "stroke": "#B0BEC5", "stroke-width": "1"})
        ET.SubElement(svg, "rect", {"x": str(x), "y": str(y), "width": str(w), "height": "42",
            "fill": "#ECEFF1", "stroke": "#B0BEC5", "stroke-width": "1"})
        label = ET.SubElement(svg, "text", {"x": str(x + 10), "y": str(y + 27), "font-size": "13", "font-weight": "700"})
        label.text = html.unescape(cell.attrib.get("value", ""))

    for cell in cells:
        if cell.attrib.get("edge") != "1":
            continue
        source, target = cell.attrib.get("source"), cell.attrib.get("target")
        if source not in geometries or target not in geometries:
            continue
        sx, sy, sw, sh = geometries[source]
        tx, ty, tw, _ = geometries[target]
        coords = [(sx + sw / 2, sy + sh)]
        points = cell.findall("./mxGeometry/Array/mxPoint")
        coords.extend((float(p.attrib["x"]), float(p.attrib["y"])) for p in points)
        coords.append((tx + tw / 2, ty))
        path_data = "M" + " L".join(f"{x},{y}" for x, y in coords)
        style = cell.attrib.get("style", "")
        stroke = _style_value(style, "strokeColor", "#455A64")
        dashed = "dashed=1" in style
        attrs = {"d": path_data, "fill": "none", "stroke": stroke,
                 "stroke-width": "3" if dashed else "2", "marker-end": f"url(#arrow-{stroke.removeprefix('#')})"}
        if dashed:
            attrs["stroke-dasharray"] = "8 4"
        ET.SubElement(svg, "path", attrs)

    for cell in cells:
        if cell.attrib.get("vertex") != "1" or "swimlane" in cell.attrib.get("style", ""):
            continue
        x, y, w, h = geometries[cell.attrib["id"]]
        style = cell.attrib.get("style", "")
        font_size = int(float(_style_value(style, "fontSize", "13")))
        is_text = style.startswith("text;")
        if not is_text:
            fill = _style_value(style, "fillColor", "#FFFFFF")
            stroke = _style_value(style, "strokeColor", "#455A64")
            dashed = "dashed=1" in style
            attrs = {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "rx": "9",
                     "fill": fill, "stroke": stroke, "stroke-width": "3" if dashed else "2"}
            if dashed:
                attrs["stroke-dasharray"] = "8 4"
            ET.SubElement(svg, "rect", attrs)
        lines = _plain_lines(cell.attrib.get("value", ""), int(w - 18), font_size)
        line_height = font_size + 6
        start_y = y + (font_size + 6 if is_text else 24)
        for line_no, line in enumerate(lines[:max(1, int((h - 12) / line_height))]):
            label = ET.SubElement(svg, "text", {"x": str(x + (0 if is_text else 10)),
                "y": str(start_y + line_no * line_height), "font-size": str(font_size),
                "font-weight": "700" if line_no == 0 else "400"})
            label.text = line
    ET.indent(svg, space="  ")
    path.write_text(ET.tostring(svg, encoding="unicode"), encoding="utf-8")


if __name__ == "__main__":
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    build_drawio()
    build_svg(CREATE_PREVIEW, "问数智能体创建、发布与开启", "更新版：智能体主数据归 Data MCP；Host 只接可信 agentId", *create_spec(), 1375)
    build_svg(TICKET_PREVIEW, "Agent Host × 外部 Asset ACL MCP × 动态 Ticket × SQLBot 回调",
              "逻辑 Data MCP 是本项目编排域；SQLBot 仅在授权回调后连接数据库",
              *ticket_spec(), 2740, True, side_flows=TICKET_FAILURE_FLOWS)
    build_graph_svg(DEVELOPMENT_PREVIEW, page_development_overview())
    build_svg(DEVELOPMENT_CREATE_PREVIEW, "07A · 创建、发布与开启实施路线",
              "创建不经过 Agent Host；开启对话时才创建 Host Session", *development_create_spec(), 2150, True)
    build_svg(DEVELOPMENT_ASK_PREVIEW, "07B · 一次提问、动态 Ticket、回调与执行实施路线",
              "Data Agent 确定性调用外部 MCP；SQLBot 仅在授权回调成功后连接数据库",
              *development_ask_spec(), 2350, True, side_flows=ASK_FAILURE_FLOWS)
    print(OUTPUT)
    print(CREATE_PREVIEW)
    print(TICKET_PREVIEW)
    print(DEVELOPMENT_PREVIEW)
    print(DEVELOPMENT_CREATE_PREVIEW)
    print(DEVELOPMENT_ASK_PREVIEW)
