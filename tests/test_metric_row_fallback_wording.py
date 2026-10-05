"""Guard the published sizing policy; these are not planner or visual tests."""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import load_contract_registry

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'workspaces/davinci-dashboard/.claude/skills/beautify-dashboard'


@pytest.fixture(scope='module')
def layout_contract():
    return load_contract_registry(ROOT / 'contracts/davinci-agent-v2.json').get(
        'dashboard.set_widget_layout'
    )


@pytest.mark.parametrize('surface', ['tool', 'sizing'])
def test_contract_describes_scoped_balanced_metric_rows(layout_contract, surface):
    text = (layout_contract.description if surface == 'tool' else
            layout_contract.input_schema['properties']['preset']['properties']['sizing']['description'])
    assert text.index('useful compatible other cards') < text.index('at least two automatic metrics')
    assert 'preserving reading order and group boundaries' in text
    assert 'no suitable other card' in text
    assert 'ordinary compact/reorder final root rows with automatic content sizing only' in text
    assert 'align retains original columns' in text
    assert 'existing mixed packing first' in text
    assert 'isolated row with at least two automatic metrics' in text
    assert 'whole available row width' in text
    assert 'integer 24-column grid' in text
    assert 'differ by at most one grid unit' in text
    assert 'equal height' in text
    assert 'smallest measured fitting common height within the bounded probe' in text
    assert 'never exceeding the original compact row maximum height' in text
    assert 'not a global minimum-height guarantee' in text
    assert 'measure both assigned widths and common height' in text
    assert 'preserve gaps' in text
    assert 'single metric stays compact' in text
    assert 'explicit preserve and size locks take priority' in text
    assert 'rankings stay narrow/tall' in text
    assert 'IDs, data and non-layout config remain unchanged' in text
    assert 'fallback leaves single metrics and explicit sizes unchanged' in text
    assert "disabled during approved grouping's temporary root sizing preview (capturePlan)" in text
    assert 'does not resize existing containers or normalize their children' in text
    assert 'existing container membership remains protected' in text


@pytest.mark.parametrize('relative', [
    'SKILL.md',
    'references/layout-fast.md',
    'references/beautification.md',
    'references/davinci-tools.md',
])
def test_skill_routes_share_the_conditional_row_policy(relative):
    text = (SKILL / relative).read_text(encoding='utf-8')
    assert text.index('先尝试') < text.index('至少 2 张自动指标卡')
    for phrase in [
        '有业务用途且兼容的其他卡片', '保持阅读顺序和分组边界',
        '仅适用于普通 compact/reorder 的自动 content 最终根行', 'align 保留原列',
        '先走现有混排', '没有合适的其他卡片', '孤立行中至少 2 张自动指标卡',
        '整行可用宽度', '24 列整数栅格', '最多相差 1 格', '等高',
        '实际分配宽度下有限探测中实测可容纳全部内容的最小共同高度',
        '不得超过原紧凑行最大高度', '不承诺全局最小高度',
        '实测分配宽度和共同高度', '保留卡间间距',
        '单指标保持紧凑', 'preserve 和显式尺寸锁优先',
        '排行榜保持窄而高', 'ID、数据和非布局配置不变',
        '回退不改动单指标及显式尺寸', '获批分组的临时根尺寸预览（capturePlan）禁用此回退',
    ]:
        assert phrase in text, (relative, phrase)
    assert '现有与新建分组的内部布局不受此变更影响' not in text
    assert '未获批移除的既有分组仍受保护' in text
    assert '不改变其容器尺寸、成员归属或内部子卡几何' in text
    assert '获批新组内部遵循相同业务区域原则' in text
    assert 'capturePlan 不先扩展根行尺寸' in text
    for obsolete in [
        '不为填行拉宽拉高', '不扩大指标卡', '不拉宽指标来填行',
        '不为填满容器拉大卡片', '不为填组拉大指标', '不拉大卡片或裁切内容填满',
        '等宽后完整容纳所有内容', '对组内与组间都检查紧凑',
        '获批新分组前的尺寸计算',
    ]:
        assert obsolete not in text, (relative, obsolete)


def test_contract_removes_blanket_metric_stretch_prohibitions(layout_contract):
    for obsolete in [
        'Metrics never stretch', 'without stretching metrics',
        'at those equal widths', 'applying the same compatible-card-first',
        'roots and sizing before approved new grouping only',
    ]:
        assert obsolete not in layout_contract.description


def test_approved_grouping_capture_plan_does_not_inflate_group_height(layout_contract):
    assert "disabled during approved grouping's temporary root sizing preview (capturePlan)" in (
        layout_contract.description
    )
    assert 'without root-row expansion inflating group height' in layout_contract.description


def test_row_fallback_uses_existing_preset_fields(layout_contract):
    properties = layout_contract.input_schema['properties']['preset']['properties']
    assert set(properties) == {
        'mode', 'orderedWidgetIds', 'scopeOrders', 'groups', 'groupingConfirmed', 'regroup',
        'sizing', 'sizeOverrides', 'typeSizes',
    }
    validator = Draft202012Validator(layout_contract.input_schema)
    validator.validate({'preset': {'mode': 'compact', 'sizing': 'content'}})
    validator.validate({'preset': {'mode': 'align', 'sizing': 'preserve'}})
    validator.validate({'preset': {
        'mode': 'reorder', 'orderedWidgetIds': ['m1', 'm2'], 'sizing': 'preserve',
        'sizeOverrides': [{'widgetId': 'm1', 'width': 6, 'height': 4}],
    }})
