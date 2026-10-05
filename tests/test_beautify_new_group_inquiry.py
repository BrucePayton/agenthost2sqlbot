"""Documentation guards, supplementary to the real-model multi-turn gate."""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import load_contract_registry

SKILL = (Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
         / ".claude/skills/beautify-dashboard")


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_semantic_pairs_precede_default_original_order_without_dropping_empty_cards(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "同一指标、时间范围与筛选口径",
        "数量/金额图与结构占比图相邻",
        "语义配对优先于桶内原相对顺序",
        "用户明确顺序优先",
        "无数据卡仍保留 ID、数据和配置",
        "不得仅凭无数据把卡片排到末尾",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_adjust_order_defaults_to_regrouping_with_one_exclusive_strategy(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "A/B/C 三种策略互斥",
        "选择 3A/3B/3C 即授权全量重新编排",
        "只排序、不分组",
        "指标卡即核心指标",
        "指标卡之间保持原相对顺序",
        "非指标卡之间也保持原相对顺序",
    ):
        assert requirement in text, (relative, requirement)
    assert "是否创建原生平铺分组" not in text, relative


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_option_three_rebuilds_flat_groups_but_preserves_tabs(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "选择 3A/3B/3C 即授权全量重新编排",
        "拆除全部已有原生平铺布局",
        "Tab 容器及其成员归属保持不变",
        "释放出的子卡与当前根卡片一起",
        "不得再次询问保留旧平铺还是全部重新分组",
        "regroup.containerWidgetIds",
        "不得包含 Tab",
        "可靠关联集合",
        "每个新平铺分组至少包含 2 张卡片",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_option_three_needs_no_follow_up_grouping_answer(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "无需追问是否创建分组",
        "无需等待“是/否”回答",
        "不得再次询问保留旧平铺还是全部重新分组",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_new_flat_groups_fill_root_rows_and_use_contrasting_frames(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "单个平铺独占 24 列",
        "组内少于 3 张卡片且全部为指标卡或排行榜",
        "两个平铺同行各占 12 列",
        "不允许 3 个及以上平铺共享一行",
        "两组都升级为各自 24 列独占一行",
        "先固定 24/12 外框",
        "调用一次功能 2 紧凑布局",
        "不得枚举其他外框宽度、跨组搜索或缩放紧凑结果",
        "全部可见子卡的实际背景色",
        "禁止灰色或近灰色",
        "所有外框候选",
        "冲突的指标卡浅色背景",
        "不限制语义分组本身的成员数量",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_semantic_grouping_uses_a_bounded_single_pass_decision(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "每张卡只生成一次 `topicKey` 和 `stageRank`",
        "不得写多轮自然语言分析",
        "精简数据配置指纹",
        "同阶段允许依据数据关系和卡片类型兼容性灵活调整顺序",
        "不得交给模型重新分类",
        "生成 `orderedWidgetIds` 和 `groups` 后不得重新推演",
        "明显且兼容的配对",
        "组间按最小 `stageRank`",
        "首张卡原始位置",
        "可靠关联集合直接建组",
        "不设置卡片数量上限",
        "数据配置相关性",
        "展示可行性",
        "不设分组数量上限",
        "不维护业务维度白名单",
        "不得在调用工具前逐张复述完整卡片清单",
        "不得展示候选方案、自我反驳",
    ):
        assert requirement in text, (relative, requirement)
    for obsolete in ("最多 4 个分组", "直接取前 4 个", "凑满 4 组"):
        assert obsolete not in text, (relative, obsolete)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_semantic_grouping_has_one_unambiguous_non_backtracking_formula(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "每张卡只生成一次 `topicKey` 和 `stageRank`",
        "先按数据配置相关性与卡片类型形成关联集合",
        "组内保持所选 3B/3C 的阶段方向",
        "组间按最小 `stageRank`",
        "展示可行时允许跨阶段成组",
        "不得重新分类",
        "仅执行一次集合校验",
        "总数、唯一性、全集相等",
        "通过后立即调用工具",
    ):
        assert requirement in text, (relative, requirement)


def test_skill_puts_option_three_latency_protocol_before_the_general_menu():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    protocol = text.index("## 3A/3B/3C 强制低延迟协议")
    menu = text.index("## 四类操作")
    assert protocol < menu
    for requirement in (
        "本节优先于后文所有调整顺序说明",
        "立即调用一次 `davinci_planner.plan_semantic_grouping`",
        "只传所选 `strategy`",
        "Host 从完整结构回执提取真正可见卡片",
        "忽略 Tab 及其成员、空文本实现卡片和隐藏占位节点",
        "仅将规则无法可靠判断的疑难卡片交给一次 AI 判断",
        "原样传给 `dashboard.set_widget_layout`",
        "Host 紧凑布局链路",
        "不得再做第二次语义分类",
        "任何第二套分组方案都视为错误",
        "不得读取组件配置",
        "不得在思考中复述卡片清单",
        "立即调用 `dashboard.set_widget_layout`",
        "必须完全省略根层 `items`",
    ):
        assert requirement in text
    for obsolete in (
        "选择 3 还必须询问下述原生平铺分组问题",
        "先取得下述新建分组选择",
        "传入所选 A/B/C 和每个可移动有效根卡",
        "仅传入所选 A/B/C 和可移动有效根卡",
        "当前美化使用前端确定性布局，不依赖 Host 求解器",
    ):
        assert obsolete not in text


@pytest.mark.parametrize(
    "relative",
    ["SKILL.md", "references/beautification.md", "references/layout-fast.md"],
)
def test_layout_guidance_uses_only_the_host_compact_layout_path(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    assert "Host 紧凑布局链路" in text
    assert "前端确定性排布，不依赖 Host 求解器" not in text


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_b_and_c_keep_semantic_association_sets_contiguous_before_inner_order(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "先按内容关联形成语义关联集合",
        "同一集合的卡片必须连续排列",
        "B 在每个集合内按总览到明细排序",
        "C 在每个集合内按现象到原因排序",
        "无法可靠关联的卡片保持原相对顺序",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_zero_changes_do_not_prove_optimal_layout(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    assert "零变更不等于布局最优" in text
    assert "业务区域" in text
    assert "固定组数" in text
    assert "非布局配置" in text


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_option_three_regroups_all_flat_children_without_a_second_confirmation(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "拆除全部已有原生平铺布局",
        "释放出的子卡与当前根卡片一起",
        "无需再次确认旧平铺移除清单或精确分组提案",
        "Tab 容器及其成员归属保持不变",
        "每个新平铺分组至少包含 2 张卡片",
        "不创建 Tab",
        "关联性不足时不创建分组",
        "在最终结果中说明未分组原因",
    ):
        assert requirement in text, (relative, requirement)
    for obsolete in (
        "没有旧容器也必须询问是否新建分组",
        "3A/3B/3C 都必须经过独立的新建分组询问",
        "先询问“保留现有分组／全部重新分组”",
        "只处理当前根画布上未分组的卡片",
        "已有平铺和 Tab 的组件归属保持不变",
    ):
        assert obsolete not in text, (relative, obsolete)


@pytest.mark.parametrize("relative", ["SKILL.md", "references/beautification.md"])
def test_adjust_order_reuses_function_two_compaction_in_one_atomic_save(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "排序功能只负责确定顺序和可选分组",
        "复用功能 2 的紧凑布局",
        "根画布和每个平铺布局范围",
        "scopeOrders",
        "layoutWidgetId",
        "全部范围成功后一次原子保存",
        "任一范围失败则不保存任何修改",
        "不得新增、复制或改写另一套紧凑布局算法",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", [
    "SKILL.md", "references/beautification.md",
    "references/layout-fast.md", "references/davinci-tools.md",
])
def test_approved_inner_planning_is_distinct_from_protected_existing_groups(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for obsolete in (
        "现有与新建分组的内部布局不受此变更影响",
        "新建组仍沿用原有内容紧凑尺寸及像素到原生内部栅格的换算",
    ):
        assert obsolete not in text, (relative, obsolete)
    for requirement in (
        "未获批移除的既有分组仍受保护",
        "不改变其容器尺寸、成员归属或内部子卡几何",
        "获批新组内部遵循相同业务区域原则",
        "多宽度与指标卡尺寸候选联合比较",
        "保存前对最终候选执行精确内部 DOM 验证",
        "同行协调须以实际候选及验证证据为准",
        "capturePlan 不先扩展根行尺寸",
        "同时关闭根级宽度/高度填空（width/height gapfill）与指标行扩展（metricrowjustify）",
        "根级填空尺寸不传入 group",
        "group 使用内容尺寸与内部实时实测",
        "不表示禁用获批新组的内部候选规划",
        "显式 preserve 和尺寸锁优先",
        "ID、数据和非布局配置不变",
    ):
        assert requirement in text, (relative, requirement)


@pytest.mark.parametrize("relative", ["references/layout-fast.md", "references/davinci-tools.md"])
def test_table_content_sizing_can_widen_without_changing_height_or_width_locks(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "表格保持单表", "自动 content 允许横向扩展", "保持当前高度",
        "显式 width 锁与 preserve 优先", "不拆表、不减少数据行数",
    ):
        assert requirement in text, (relative, requirement)
    assert "未提供内容尺寸规则的表格保留原尺寸" not in text
    assert "加载中、隐藏、不可测固定字号、表格、容器和未知类型保留" not in text


@pytest.mark.parametrize("relative", ["SKILL.md", "references/davinci-tools.md"])
def test_color_styles_route_to_exact_metric_presets(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for payload in (
        '{"preset":{"theme":"plain","scope":"background"}}',
        '{"preset":{"theme":"colorful","variant":"metric-unified","scope":"colors"}}',
        '{"preset":{"theme":"colorful","variant":"metric-multicolor","scope":"colors"}}',
    ):
        assert payload in text, (relative, payload)
    for requirement in (
        "B1 指标卡统一颜色风",
        "B2 指标卡多彩风",
        "重复请求 B1 或 B2 时仍调用同一预置，由前端推进到下一套排序色板",
        "显式颜色使用 `colors:[...]` 数组",
        "非指标分析卡统一为白色背景",
        "保留指标卡已有业务语义色",
        "B2 至少需要 2 张适用指标卡",
        "只有 1 张时如实报告无变更、不保存，并建议改用 B1",
        "只说“颜色 1B”时追问 B1 还是 B2",
        '"colors":["#EAF2FF"]',
        '"colors":["#EAF2FF","#E8F7F2"]',
        "仅非 B1/B2 的自定义局部组件或特殊字段",
        "没有适用指标卡时如实报告无变更，不保存",
        "结果不确定时先检查实际结果，不盲目重放",
        "指标卡文字固定为深色，因此拒绝深色背景",
    ):
        assert requirement in text, (relative, requirement)
    assert "B 多彩背景风" not in text, relative


@pytest.mark.parametrize("relative", [
    "SKILL.md", "references/layout-fast.md", "references/davinci-tools.md",
])
def test_tables_use_root_half_width_capped_by_the_current_container(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    assert "表格" in text and "透视表" in text, relative
    for requirement in (
        "整屏可用宽度的一半", "当前容器可用宽度", "取两者较小值",
        "占满该容器", 'sizing:"preserve"',
    ):
        assert requirement in text, (relative, requirement)


def test_optional_candidate_timeout_does_not_mean_a_card_is_missing():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for requirement in (
        "可选尺寸候选搜索", "保留已验证候选", "当前合法尺寸",
        "不表示卡片不存在", "初始内容读取", "保存前最终验证",
    ):
        assert requirement in text, requirement


@pytest.mark.parametrize("relative", [
    "SKILL.md", "references/layout-fast.md", "references/davinci-tools.md",
])
def test_one_click_organize_is_documented_as_bounded_alignment_cleanup(relative):
    text = (SKILL / relative).read_text(encoding="utf-8")
    for requirement in (
        "一键整理仪表盘",
        'preset":{"mode":"organize","sizing":"content"}',
        "对齐",
        "减少可避免留白",
        "不追求全局最优",
    ):
        assert requirement in text, (relative, requirement)


def test_inner_candidate_planning_keeps_the_existing_tool_input_contract():
    """Candidate search and DOM validation are internal, not new model arguments."""
    root = Path(__file__).resolve().parents[1]
    contract = load_contract_registry(root / "contracts/davinci-agent-v2.json").get(
        "dashboard.set_widget_layout"
    )
    schema = contract.input_schema
    preset = schema["properties"]["preset"]["properties"]
    assert set(preset) == {
        "mode", "orderedWidgetIds", "scopeOrders", "groups", "groupingConfirmed", "regroup",
        "sizing", "sizeOverrides", "typeSizes",
    }
    assert "capturePlan" not in schema["properties"]
    validator = Draft202012Validator(schema)
    validator.validate({
        "expectedResourceRevision": 8,
        "preset": {
            "mode": "reorder", "orderedWidgetIds": ["metric-a", "metric-b"],
            "sizing": "content", "groupingConfirmed": True,
            "groups": [{"title": "Summary", "widgetIds": ["metric-a", "metric-b"]}],
        },
    })
