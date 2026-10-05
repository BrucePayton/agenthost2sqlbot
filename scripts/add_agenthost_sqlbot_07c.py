"""Insert the verified 07C status page without regenerating older drawio tabs."""

from __future__ import annotations

import re
from itertools import pairwise
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
PREVIEW = ROOT / "docs/agenthost-data-mcp-sqlbot-service2-catalog-preview.svg"
PAGE_ID = "page-07c"
PAGE_NAME = "07C-服务2目录接口与本地问数联调现状"


def graph() -> ET.Element:
    result, root = model(2155, 2070)
    text(root, "title", "<b>07C · 服务2目录接口与本地问数联调现状</b><br>"
         "<font color='#607D8B'>绿色为实测通过；橙色为局部验证；红色为服务2反向回调阻塞</font>",
         30, 15, 2070, 55, 24)
    for cell_id, label, x, width in [
        ("lane0", "调用方 / 前端", 20, 220),
        ("lane1", "本机 AgentHost :8765", 240, 365),
        ("lane2", "服务2 SQLBot :8002", 605, 365),
        ("lane3", "StarRocks / hive", 970, 335),
        ("lane4", "本机 SQLBot 隔离联调", 1305, 405),
        ("lane5", "服务2问数边界", 1710, 425),
    ]:
        lane(root, cell_id, label, x, width, 1970)

    boxes = [
        ("c01", "<b>1 请求目录</b><br>GET /api/sqlbot/catalog<br>按 DW / DM / RPT 浏览", 32, 155, 196, 108, "neutral"),
        ("c02", "<b>2 宿主身份与范围门禁</b><br>本机 mock 身份；仅映射 4/5/6<br>目录开关独立于动态问数", 252, 290, 341, 112, "live"),
        ("c03", "<b>3 管理账号换取 Token</b><br>服务2 /mcp/access_token<br>仅服务端持有管理凭证", 617, 425, 341, 108, "live"),
        ("c04", "<b>4 校验数据源映射</b><br>/datasource/list：ID 4/5/6<br>名称与 StarRocks 类型均匹配", 617, 560, 341, 112, "live"),
        ("c05", "<b>5 连接检查</b><br>/datasource/check/{id}<br>DW / DM / RPT 均为 true", 982, 695, 311, 108, "live"),
        ("c06", "<b>6 读取已选表与表备注</b><br>/datasource/tableList/{id}<br>DW 23 / DM 19 / RPT 8", 617, 830, 341, 112, "live"),
        ("c07", "<b>7 读取字段与自定义备注</b><br>/datasource/fieldList/{ds}/{table}<br>共 3318 字段；3269 条非空备注", 617, 965, 341, 112, "live"),
        ("c08", "<b>8 实时 Schema 对照</b><br>/datasource/getFields/{ds}/{table}<br>三组样表字段均匹配；巡检表 105/105", 982, 1100, 311, 116, "live"),
        ("c09", "<b>9 宿主合并并过滤</b><br>只返回已选表/字段及备注<br>不返回连接配置、密码或签名密钥", 252, 1240, 341, 116, "live"),
        ("c10", "<b>10 返回本地目录 API</b><br>连接、表、字段、实时 Schema<br>50 表 / 3318 字段逐项一致", 32, 1380, 196, 118, "live"),
        ("l01", "<b>L1 隔离宿主 + 本机 SQLBot</b><br>Host :18765 ↔ SQLBot :8000<br>以本机 SECRET_KEY 签 Assistant JWT", 1317, 315, 381, 118, "partial"),
        ("l02", "<b>L2 07B Ticket 回调实测</b><br>Certificate 仅携带本次 Ticket<br>本机 SQLBot 回调宿主；计数 2 次", 1317, 515, 381, 118, "live"),
        ("l03", "<b>L3 单表真实问数成功</b><br>巡检表 105 字段；生成 SELECT COUNT<br>SQLBot Record + 1 行查询结果", 1317, 715, 381, 118, "live"),
        ("r01", "<b>R1 服务2高级小助手已创建</b><br>zp-qwen3.8-max；实例密钥验签通过<br>assistant/start 可创建 Chat", 1722, 315, 401, 118, "partial"),
        ("r02", "<b>R2 服务2反向回调超时</b><br>→ 10.181.21.143:8766<br>问数请求未取得动态数据源", 1722, 515, 401, 118, "gap"),
        ("r03", "<b>R3 当前启用边界</b><br>:8765 目录 API 已启用<br>服务2高级小助手问数仍待回调通路", 1722, 715, 401, 118, "partial"),
        ("outcome", "<b>当前验收结论</b><br>服务2经接口提供连接状态、已选表、实时 Schema 与表/字段备注：50 表、3318 字段，和导入清单逐项一致。"
         + "<br>本机隔离链路已完成单表真实问数；服务2高级小助手的端到端问数仍被反向回调网络阻塞。", 252, 1650, 1450, 132, "partial"),
    ]
    for cell_id, value, x, y, width, height, status in boxes:
        box(root, cell_id, value, x, y, width, height, status)

    main = ["c01", "c02", "c03", "c04", "c05", "c06", "c07", "c08", "c09", "c10"]
    for number, (source, target) in enumerate(pairwise(main), 1):
        edge(root, f"c_edge{number}", source, target, status="code")
    edge(root, "c_edge_local1", "l01", "l02", status="code")
    edge(root, "c_edge_local2", "l02", "l03", status="code")
    edge(root, "c_edge_remote1", "r01", "r02", status="gap")
    edge(root, "c_edge_remote2", "r02", "r03", status="gap")
    edge(root, "c_edge_result", "c10", "outcome", status="code")
    edge(root, "c_edge_local_result", "l03", "outcome", status="partial")
    edge(root, "c_edge_remote_result", "r03", "outcome", status="gap")
    return result


def main() -> None:
    original = DRAWIO.read_text(encoding="utf-8")
    tree = ET.fromstring(original)
    existing = tree.find(f"./diagram[@id='{PAGE_ID}']")
    if existing is not None:
        raise RuntimeError(f"{PAGE_ID} already exists; review before replacing")
    new_graph = graph()
    diagram = ET.Element("diagram", {"id": PAGE_ID, "name": PAGE_NAME})
    diagram.append(new_graph)
    ET.indent(diagram, space="  ", level=1)
    xml = ET.tostring(diagram, encoding="unicode")
    match = re.search(r'<diagram id="page-07b".*?</diagram>', original, re.DOTALL)
    if match is None:
        raise RuntimeError("07B page not found")
    updated = original[:match.end()] + "\n  " + xml + original[match.end():]
    updated = re.sub(r'(<mxfile\b[^>]*\bpages=")[^"]+', lambda m: m.group(1) + str(len(tree.findall('diagram')) + 1), updated, count=1)
    parsed = ET.fromstring(updated)
    assert int(parsed.get("pages")) == len(parsed.findall("diagram"))
    DRAWIO.write_text(updated, encoding="utf-8")
    build_graph_svg(PREVIEW, new_graph)


if __name__ == "__main__":
    main()
