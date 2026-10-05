"""Add the detailed local SQLBot / service-2 datasource boundary to the flow file."""

from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree as ET

from generate_agenthost_sqlbot_drawio import (
    box,
    build_graph_svg,
    edge,
    lane,
    model,
    text,
)

ROOT = Path(__file__).resolve().parents[1]
DRAWIO = ROOT / "docs/agenthost-data-mcp-sqlbot-report-flows.drawio"
PREVIEW = ROOT / "docs/agenthost-data-mcp-sqlbot-local-sqlbot-service2-preview.svg"
PAGE_ID = "page-07d"
PAGE_NAME = "07D-服务2供动态源与本机SQLBot执行链路"


def graph() -> ET.Element:
    result, root = model(2460, 2460)
    text(root, "title", "<b>07D · 服务2供动态数据源 × 本机 SQLBot 执行</b><br>"
         "<font color='#607D8B'>对应 07B 第 10 步：服务2提供已选表、字段和备注；宿主组装动态数据源，本机 SQLBot 负责 NL2SQL、执行和结果。</font>",
         30, 15, 2380, 55, 24)
    for cell_id, label, x, width in [
        ("lane0", "用户 / 前端", 20, 270),
        ("lane1", "本机 AgentHost / Data MCP", 290, 410),
        ("lane2", "服务2：动态数据源提供方", 700, 415),
        ("lane3", "本机 SQLBot：问数执行方", 1115, 415),
        ("lane4", "StarRocks / hive", 1530, 340),
        ("lane5", "验收证据 / 边界", 1870, 570),
    ]:
        lane(root, cell_id, label, x, width, 2350)

    nodes = [
        ("d01", "<b>1 用户提问</b><br>本机问数 Session<br>选 DW / DM / RPT", 32, 150, 246, 108, "neutral"),
        ("d02", "<b>2 绑定身份与 Session</b><br>确认 agent / datasetRefs<br>签发 60s 一次提问 Ticket", 302, 285, 386, 108, "live"),
        ("d03", "<b>3 签本机 Assistant JWT</b><br>使用本机 SQLBot 的 SECRET_KEY<br>Certificate 仅携带本次 Ticket", 302, 420, 386, 108, "live"),
        ("d04", "<b>4 assistant/start 或 question</b><br>本机 SQLBot :8000<br>创建 / 复用 Chat", 1127, 555, 391, 108, "live"),
        ("d05", "<b>5 动态数据源回调</b><br>本机 SQLBot → Host<br>GET /api/sqlbot/datasources + Ticket", 1127, 690, 391, 108, "live"),
        ("d06", "<b>6 校验 Ticket</b><br>user / agent / session / questionHash<br>过期、重放、超次数均拒绝", 302, 825, 386, 108, "live"),
        ("d07", "<b>7 宿主向服务2取源</b><br>管理 Token；限定 ID 4/5/6<br>list / tableList / fieldList 实时读取", 712, 960, 391, 112, "live"),
        ("d08", "<b>8 服务2目录接口返回</b><br>已选表、字段、导入备注<br>DW 23 / DM 19 / RPT 8；共 3318 字段", 712, 1095, 391, 112, "live"),
        ("d09", "<b>9 服务2返回源元数据</b><br>50 张已选表 / 3318 字段 / 备注<br>不含 DB 密码、SQL 查询及数据行", 712, 1230, 391, 120, "live"),
        ("d10", "<b>10 / 07B-10 宿主完成回调</b><br>实时元数据 + 本地 StarRocks 连接配置<br>返回动态源；不一致即拒绝", 302, 1375, 386, 108, "live"),
        ("d11", "<b>11 本机 SQLBot 生成 SQL</b><br>NL2SQL / SELECT<br>仅此执行端连接 StarRocks", 1127, 1510, 391, 108, "live"),
        ("d12", "<b>12 StarRocks 执行</b><br>hive.dw / hive.dm / hive.rpt<br>分别实测 COUNT 返回 1 行", 1542, 1645, 316, 108, "live"),
        ("d13", "<b>13 SSE + Record Data</b><br>SQL、Record ID、结果行<br>由本机 SQLBot 提供", 1127, 1780, 391, 108, "live"),
        ("d14", "<b>14 宿主回显</b><br>标准化、缓存、审计<br>返回表格与依据", 302, 1915, 386, 108, "live"),
        ("s01", "<b>服务2接口边界</b><br>服务2仅返回已选表/字段/备注；完整动态源由宿主结合本地连接配置组装，不从服务2读取密码。", 1882, 990, 546, 118, "live"),
        ("s02", "<b>本机隔离链路已实测</b><br>DW、DM、RPT 分别问数成功；三源联合小助手也成功返回 RPT COUNT 查询。", 1882, 1250, 546, 118, "live"),
        ("s03", "<b>网络边界已验证</b><br>Host → 服务2接口可达；本机 SQLBot → 隔离 Host 回调可达。此方案无需服务2反向访问本机。", 1882, 1510, 546, 118, "live"),
        ("s04", "<b>验收边界</b><br>已验证 50 表元数据和 4 次真实提问；其余表的逐表问数、跨源关联与用户级权限尚未验收。", 1882, 1770, 546, 118, "partial"),
        ("outcome", "<b>状态判定</b><br>07D 第 9～14 步已串联实测：服务2目录 → 宿主组装动态源 → 本机 SQLBot → StarRocks → 结果回显。"
         + "DW / DM / RPT 单源与三源联合提问均成功；服务2本身没有对本机反向回调。", 302, 2180, 1556, 126, "live"),
    ]
    for cell_id, value, x, y, width, height, status in nodes:
        box(root, cell_id, value, x, y, width, height, status)

    main = [f"d{number:02d}" for number in range(1, 15)]
    for number, (source, target) in enumerate(zip(main, main[1:]), 1):  # noqa: RUF007
        edge(root, f"d_edge{number}", source, target, status="code")
    edge(root, "d_edge_result", "d14", "outcome", status="code")
    return result


def main() -> None:
    original = DRAWIO.read_text(encoding="utf-8")
    parsed = ET.fromstring(original)
    existing = parsed.find(f"./diagram[@id='{PAGE_ID}']") is not None
    new_graph = graph()
    diagram = ET.Element("diagram", {"id": PAGE_ID, "name": PAGE_NAME})
    diagram.append(new_graph)
    ET.indent(diagram, space="  ", level=1)
    match = re.search(r'<diagram id="page-07d".*?</diagram>' if existing else
                      r'<diagram id="page-07c".*?</diagram>', original, re.DOTALL)
    if match is None:
        raise RuntimeError("Expected 07C/07D page not found")
    serialized = ET.tostring(diagram, encoding="unicode")
    if existing:
        updated = original[:match.start()] + serialized + original[match.end():]
    else:
        updated = original[:match.end()] + "\n  " + serialized + original[match.end():]
    updated, count = re.subn(r'(<mxfile\b[^>]*\bpages=")[^"]+',
                             lambda item: item.group(1) + str(len(parsed.findall("diagram")) + (not existing)), updated, count=1)
    assert count == 1
    complete = ET.fromstring(updated)
    assert int(complete.get("pages")) == len(complete.findall("diagram"))
    DRAWIO.write_text(updated, encoding="utf-8")
    build_graph_svg(PREVIEW, new_graph)


if __name__ == "__main__":
    main()
