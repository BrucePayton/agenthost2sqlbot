from pathlib import Path

import pytest


def test_davinci_dashboard_template_loads_with_skill() -> None:
    from app.skills.bundle import load_bundle_from_directory
    from app.workspaces.registry import WorkspaceRegistry

    root = Path(__file__).resolve().parents[1] / "workspaces"
    registry = WorkspaceRegistry(
        root,
        default_model="qwen3.8-max",
        environ={
            "DAVINCI_DATA_MCP_URL": "http://127.0.0.1:8000/mcp",
        },
        allow_loopback_http_mcp=True,
    )
    entry = registry.get("davinci-dashboard")
    assert entry.available, entry.validation_errors
    manifest = entry.manifest
    assert manifest is not None
    # workspace 快照优先于全局默认，必须与当前默认模型一致，
    # 否则不带 request.model 的调用路径会静默落到另一个模型上。
    assert manifest.model == "deepseek-v4-pro-0813"
    assert manifest.skills == [
        "beautify-dashboard",
        "configure-dashboard-widget",
        "configure-subscription-rule",
        "interpret-dashboard",
        "locate-data",
        "manage-space",
    ]
    # A file can exist while invalid frontmatter prevents bootstrap from loading it.
    for name in manifest.skills:
        bundle = load_bundle_from_directory(entry.directory / ".claude/skills" / name)
        assert bundle.name == name
    assert "mcp__davinci_data__*" in manifest.allowed_tools
    assert "Bash" not in manifest.allowed_tools
    skill = entry.directory / ".claude/skills/configure-dashboard-widget/SKILL.md"
    assert skill.exists()
    beautify_skill = (
        entry.directory / ".claude/skills/beautify-dashboard/SKILL.md"
    )
    assert beautify_skill.exists()
    beautify_text = beautify_skill.read_text(encoding="utf-8")
    assert "不得询问用户选“紧凑”还是“对齐”" in beautify_text
    assert all(
        choice in beautify_text
        for choice in (
            "A 核心指标放顶部",
            "B 先总览再明细",
            "C 按现象到原因排列",
        )
    )
    assert "A 紧凑" not in beautify_text
    assert "B 对齐" not in beautify_text
    assert all(choice in beautify_text for choice in (
        "1. 颜色优化：A 素雅风；B1 指标卡统一颜色风；B2 指标卡多彩风。",
        "2. 紧凑布局", "3. 调整顺序", "4. 一键整理仪表盘",
        "直接进入有界决策",
    ))
    assert "B 多彩背景风" not in beautify_text
    assert "选择 4" in beautify_text
    assert '{"preset":{"mode":"organize","sizing":"content"}}' in beautify_text
    assert "重新调用内容紧凑预置一次" in beautify_text
    assert "不是平台硬下限" in beautify_text
    assert "不先读取结构、配置或能力" in beautify_text
    subscription_skill = (
        entry.directory / ".claude/skills/configure-subscription-rule/SKILL.md"
    )
    assert subscription_skill.exists()
    skill_text = skill.read_text(encoding="utf-8")
    # 数据检索规则已迁往 locate-data；本 Skill 只保留一句委托，不再重复。
    assert "Skill(locate-data)" in skill_text
    assert "analytics.resolve_data_requirements" not in skill_text
    assert "dashboard.apply_widget_spec" in skill_text
    assert "persisted:true" in skill_text
    assert "读回验证" in skill_text
    assert (skill.parent / "evals/cases.md").read_text(encoding="utf-8").count(
        "## 用例"
    ) >= 4
    claude_md = (entry.directory / "CLAUDE.md").read_text(encoding="utf-8")
    assert "最多可并行发起 4 个只读" in claude_md
    assert "写工具" in claude_md and "立即结束这条回复" in claude_md
    assert "个人空间仪表盘新建分组" in claude_md
    assert "仅空容器请求或数据绑定已确认时" in claude_md
    assert "个人空间仪表盘新建分组 → `workspace.dashboard_group.create`" in claude_md
    assert "协同空间目录分组 → `manage-space`" in claude_md


def test_subscription_skill_has_bounded_create_only_workflow() -> None:
    """Check discoverable references and contract-linked acceptance artifacts."""
    import json
    import re

    from app.agui.contracts import load_contract_registry

    project = Path(__file__).resolve().parents[1]
    root = project / "workspaces/davinci-dashboard"
    skill_root = root / ".claude/skills/configure-subscription-rule"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    tools = (skill_root / "references/tools.md").read_text(encoding="utf-8")
    assert "name: configure-subscription-rule" in skill
    for target in re.findall(r"\]\((references/[^)]+)\)", skill):
        path, _, anchor = target.partition("#")
        reference = skill_root / path
        assert reference.is_file()
        if anchor:
            headings = re.findall(r"^#{1,6}\s+(.+)$", reference.read_text(encoding="utf-8"), re.M)
            assert anchor in {heading.lower().replace(" ", "-") for heading in headings}
    registry = load_contract_registry(project / "contracts/davinci-agent-v2.json")
    actions = {
        item.action for item in registry.public_contracts
        if item.bundle == "space-message-rule"
    }
    assert len(actions) == 6
    assert all(f"## {action.rsplit('.', 1)[1]}" in tools for action in actions)
    assert "configure-subscription-rule" in (root / "CLAUDE.md").read_text(
        encoding="utf-8"
    )
    fixture = json.loads((skill_root / "evals/cases.json").read_text())
    entries = fixture["entries"]
    assert len(entries) == len({entry["id"] for entry in entries}) == 9
    assert sum(entry["shortcut"] for entry in entries) == 6
    assert all(entry["ownershipScope"] == "personal"
               for entry in entries if entry["shortcut"])
    assert sum(entry["ownershipScope"] == "space" for entry in entries) == 2
    cases = {case["id"]: case for case in fixture["cases"]}
    assert cases["weekly-alert-draft-only"]["expected"]["starts"] == 0
    assert cases["weekly-alert-draft-only"]["expected"]["creates"] == 0
    assert cases["explicit-save-disabled"]["expected"]["nativeConfirmation"] is False
    assert cases["explicit-save-disabled"]["expected"]["creates"] == 0
    assert cases["unknown-save"]["expected"]["retries"] == 0
    assert fixture["performance"]["defaultDataQueries"] == 0
    assert fixture["performance"]["defaultRuleListQueries"] == 0


def test_skill_references_use_frontend_time_conventions() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/configure-dashboard-widget"
    )
    text = (root / "references/fields-and-time.md").read_text(encoding="utf-8")
    assert (
        '"operator":"eq","source":"assumed","valueExp":"last_days","value":["7"]'
        in text
    )
    assert "past_7_days" not in text
    # 动态时间示例里不得出现 ge；ge 对普通固定值筛选仍然合法，故只检查时间小节
    time_section = text.split("## 时间表达")[1].split("##")[0]
    assert '"operator":"ge"' not in time_section
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "comparison" in skill and "weekSame" in skill and "周同比" in skill
    # "写工具发起后立即结束这条回复" 与 CLAUDE.md :25 重复，Task 8 Step 0 从本
    # Skill 删掉腾字节预算；同一条规则仍在 CLAUDE.md 里，由
    # test_davinci_dashboard_template_loads_with_skill 覆盖。
    errors = (root / "references/errors.md").read_text(encoding="utf-8")
    assert (
        "ELIGIBLE_DATE_FILTER_REQUIRED" in errors
    )  # 人工 apply_widget_edits 路径仍会返回


def test_skill_routes_discovery_definition_and_query_with_bounded_reads() -> None:
    """Keep task routing and evidence requirements without restoring dataset-only choice."""
    root = (
        Path(__file__).resolve().parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/locate-data"
    )
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    assert "不要再调用 resolve" in skill
    assert "默认 4 次" in skill and "不为用完额度继续探索" in skill
    assert all(
        section in skill for section in ("## 发现位置", "## 查看口径", "## 查询或配置")
    )
    assert "不先选表、不询问查数日期" in skill
    assert "refs.datasetRef" in skill and "refs.fieldRefs[].fieldRef" in skill
    assert "metrics[].fieldRef" in skill
    assert "真实定义" in skill and "按失败或上限说明未取得" in skill
    assert "立刻提一个二选一的问题并结束本轮" not in skill
    tools = (root / "references/tools.md").read_text(encoding="utf-8")
    assert '"fieldRefs":["warehouseTopic:307/12826"' in tools
    assert '"roles":["time"]' in tools
    assert "fieldCount" in tools and "truncated" in tools
    errors = (root / "references/errors.md").read_text(encoding="utf-8")
    assert "exceeds maximum allowed tokens" in errors
    assert "NO_MATCHING_DATASET" in errors


def test_metric_choice_precedes_deferrable_creation_without_losing_confirmed_work() -> (
    None
):
    """Keep workspace and skill ordering consistent; live replays validate actual behavior."""
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    configure = (root / ".claude/skills/configure-dashboard-widget/SKILL.md").read_text(
        encoding="utf-8"
    )
    locate = (root / ".claude/skills/locate-data/SKILL.md").read_text(encoding="utf-8")
    evals = (
        root / ".claude/skills/configure-dashboard-widget/evals/cases.md"
    ).read_text(encoding="utf-8")

    assert "数据集 + 指标 + 真实口径" in claude_md
    assert "缺定义定向补查" in claude_md
    assert "下一动作只能是提问并结束本轮" not in claude_md
    assert "历史目标不自动追加" in claude_md
    assert "先定位绑定、澄清口径再建容器" in claude_md
    assert "独立部分先做" in claude_md
    assert "指标名不是数据绑定" in claude_md
    assert "未绑定组件先 `catalog.search_datasets(limit=3,maxMatchedFields=3)`" in claude_md
    assert "仅空容器请求或数据绑定已确认时" in claude_md
    assert "先创建并打开空看板" not in claude_md
    assert "先完成必要业务澄清，再创建容器" in configure
    assert "口径检查点要求用户选择" in configure
    assert "## 口径检查点" in locate
    assert "不读整表" in locate
    assert "仅回答同环比选项不等于确认指标" in locate
    assert "开放搜索优先一次搜索，必要时一次定向 schema/search_fields" in locate
    assert "工具补最多两个 schema 定义" in locate
    assert "limit:3,maxMatchedFields:3" in locate
    assert "datasetRefs 最多两个已命中数据集" in locate
    assert "先不规划图表或创建容器" in configure
    assert "只检查已有配置时读回即完成" in configure
    assert "缺动作直接问" in claude_md
    assert "业务候选应有必要字段/口径证据" in claude_md
    assert claude_md.index("缺动作直接问") < claude_md.index("## 任务路由")
    assert "目标选定后" in claude_md
    manage = (root / ".claude/skills/manage-space/SKILL.md").read_text(encoding="utf-8")
    assert "动作不明或已有对象待用户选择" in manage
    assert "目标明确后" in manage
    assert "新建未绑定组件直接第 2 步" in configure
    existing_step = configure.split("1. **核对已有组件。**", 1)[1].split("2. **", 1)[0]
    assert existing_step.index("ui.open_dashboard") < existing_step.index(
        "dashboard.get_structure"
    )
    assert existing_step.index("dashboard.get_structure") < existing_step.index(
        "get_widget_config"
    )
    assert "已在目标页复用有效上下文" in existing_step
    assert "目标页的 `dashboard_structure`" in existing_step
    assert "创建一个新的目录叫奢侈品" in evals
    assert "不重建目录或看板" in evals
    assert "用户只回复比较选项「2」不等于确认指标口径" in evals


def test_locate_data_bounds_relation_scope_without_overpromising() -> None:
    skill = (
        Path(__file__).resolve().parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/locate-data/SKILL.md"
    ).read_text(encoding="utf-8")
    # Only plan relations after choosing semantics; first verify a suitable single table.
    assert "口径确认后指标与维度分散时" in skill
    assert "验证已有候选是否单表可用" in skill
    assert "跨数据集计算指标（A 表指标 ÷ B 表指标）做不到" in skill
    assert "不支持跨表" in skill
    assert "不写入配置、不解读数值" in skill


def test_interpret_dashboard_is_read_only_and_refuses_mechanism_guessing() -> None:
    skill = (
        Path(__file__).resolve().parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/interpret-dashboard/SKILL.md"
    ).read_text(encoding="utf-8")
    # 基线失败：模型自行推演平台算法，把合法的「last_days + 日环比」判成配置错误，
    # 并据此反推出未返回的明细数字。以下三条是对该失败的直接封堵。
    assert "不推演平台如何计算" in skill
    assert "都是合法组合" in skill
    assert "只报工具返回的数字" in skill
    assert "解读任务零写入、零发布" in skill
    assert "截断不是总量" in skill


def test_claude_md_and_skill_carry_p0_10_rules() -> None:
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "## 职责边界" in claude_md
    assert "已知缺口合并一问" in claude_md
    skill_root = root / ".claude/skills/configure-dashboard-widget"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "确认配置" in skill and "不存在就询问" in skill
    locate = (root / ".claude/skills/locate-data/SKILL.md").read_text(encoding="utf-8")
    assert "isComplete:true" in locate
    assert "复用已确认的 datasetRef + fieldRef" in locate
    tools = (skill_root / "references/tools.md").read_text(encoding="utf-8")
    assert "不要携带读回 `effectiveSpec` 里的 `name`/`type`" in tools
    fields_time = (skill_root / "references/fields-and-time.md").read_text(
        encoding="utf-8"
    )
    assert "## 同环比可用性" in fields_time
    assert "月环比 `monthChain` 不可用" in fields_time
    errors = (skill_root / "references/errors.md").read_text(encoding="utf-8")
    assert "is not allowed" in errors


def test_davinci_skill_documents_collaborative_space_p0_contract() -> None:
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    claude_md = (root / "CLAUDE.md").read_text(encoding="utf-8")
    skill_root = root / ".claude/skills/configure-dashboard-widget"
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    tools = (skill_root / "references/tools.md").read_text(encoding="utf-8")
    errors = (skill_root / "references/errors.md").read_text(encoding="utf-8")
    space_root = root / ".claude/skills/manage-space"
    space_skill = (space_root / "SKILL.md").read_text(encoding="utf-8")
    space_tools = (space_root / "references/tools.md").read_text(encoding="utf-8")
    # 空间 Skill 只约束空间分支，但不能否认同一工具的个人范围。
    guidance = f"{claude_md}\n{skill}\n{tools}\n{errors}\n{space_skill}\n{space_tools}"

    assert (
        "`ui.open_space_page` 固定导航到 `/share/collaborative-space`，"
        "不接受 `spaceId`、path 或 URL。"
    ) in guidance
    assert (
        "`ui.open_personal_workspace` 固定导航到 `/share/workbench-new`，"
        "用于离开协同空间且不接受 path、URL 或资源参数。"
    ) in guidance
    assert "dashboard.open_data_alert_config {widgetId}" in guidance
    assert "configure-subscription-rule" in skill
    assert (
        "空间角色能力只是上限；实际 action catalog/execute 仍按当前可信 "
        "Page State 复核实时门禁。"
    ) in guidance
    assert (
        "实时门禁至少包括 `busy`、`publishStatus`、`embed/snapshot`、Widget "
        "presence/eligibility 与 `revision`；owner/admin 不保证必然可发布，"
        "member 打开数据推送/预警也要通过各自门禁，alert 仅限 eligible "
        "Widget。"
    ) in guidance
    assert "空间 owner/admin 可以配置、持久化和发布" not in guidance
    assert (
        "`ui.open_space_page` 只导航，不创建、删除、移交空间，也不做成员"
        "增删或角色管理。"
    ) in guidance
    assert ("消息 Widget 不可查询，返回 `MESSAGE_WIDGET_NOT_QUERYABLE`") in guidance
    assert (
        "Agent 刷新只处理普通 Widget，消息项进入 `unavailableWidgetIds`"
    ) in guidance

    p0_codes = {
        "MESSAGE_WIDGET_NOT_QUERYABLE",
        "DASHBOARD_AMBIGUOUS",
        "CHART_TYPE_NOT_AVAILABLE",
        "TOO_MANY_TARGETS",
        "CREATE_FAILED",
        "CREATE_ROLLBACK_FAILED",
        "PUBLISH_FAILED",
    }
    assert all(f"`{code}`" in errors for code in p0_codes)
    wave_3_only_codes = {
        "MUTATION_BUSY",
        "MUTATION_RECOVERY_REQUIRED",
        "PENDING_WIDGET_CONFIGURATION",
    }
    assert all(code not in guidance for code in wave_3_only_codes)


def test_davinci_claude_md_guides_bounded_space_core_workflow() -> None:
    space_root = (
        Path(__file__).resolve().parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/manage-space"
    )
    # 空间流程规则整体迁往 manage-space；CLAUDE.md 只保留一行路由。
    claude_md = "\n".join(
        [
            (space_root / "SKILL.md").read_text(encoding="utf-8"),
            (space_root / "references/tools.md").read_text(encoding="utf-8"),
        ]
    )

    expected_tools = {
        "space.list",
        "space.get_context",
        "space.open",
        "space.create",
        "space.update_info",
        "space.invitation.respond",
        "space.member.get_context",
        "space.member.apply_changes",
        "space.menu.get_context",
        "space.menu.apply_changes",
        "space.dashboard.create_and_open",
    }
    assert all(tool in claude_md for tool in expected_tools)


def test_davinci_prompt_budget_stays_within_gates() -> None:
    """CLAUDE.md 每次请求都付费，Skill 只在被路由到时付费。

    闸门按落地时的实际大小加约 10% 余量设定，目的是让「再加一段」成为一次
    有意识的决定，而不是悄悄回到拆分前 8.3KB 的宪法。
    """
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    gates = {
        # CLAUDE.md 与 manage-space 4600 -> 5000（2026-09-01，用户拍板）：
        # session 7b2e4f32 导航前烧掉 53.8k 字符思考，其中最大的一段是在化解
        # 指令自身的矛盾——manage-space 说 `space.*`「固定常驻」，模型在仪表盘
        # 页却一个都看不到，且启动决策树缺了「还不在空间域」这一支。把真话、
        # 缺失分支和「工具不在目录就先导航」写进去需要这 400 字节。
        "CLAUDE.md": 5000,
        ".claude/skills/configure-dashboard-widget/SKILL.md": 6144,
        ".claude/skills/configure-subscription-rule/SKILL.md": 5200,
        ".claude/skills/interpret-dashboard/SKILL.md": 4300,
        ".claude/skills/locate-data/SKILL.md": 5600,
        ".claude/skills/manage-space/SKILL.md": 5000,
    }
    oversized = {
        name: ((root / name).stat().st_size, limit)
        for name, limit in gates.items()
        if (root / name).stat().st_size > limit
    }
    assert not oversized, f"超出提示词预算: {oversized}"

    # locate-data 是 configure-dashboard-widget 与 configure-subscription-rule
    # 共同依赖的公共节点，常见组合的常驻指令量要留在合理范围内。
    combo = (
        (root / "CLAUDE.md").stat().st_size
        + (root / ".claude/skills/configure-dashboard-widget/SKILL.md").stat().st_size
        + (root / ".claude/skills/locate-data/SKILL.md").stat().st_size
    )
    # 15000 -> 15900（2026-09-01，P0-14 拍板允许小幅突破）：新增的每一句都
    # 对应 session 8f14f309 里一类真实翻车——显式 Skill 装载、必填筛选并入
    # 澄清、业务语言提问、新建 dryRun 不探数、filter source 标注。
    # 15900 -> 16400（2026-09-01 同批）：随 CLAUDE.md 闸门 4600->5000 顺延，
    # 否则单文件放宽了、组合仍卡死，等于没放。
    assert combo <= 16400, f"建图+找数组合过大: {combo}"


def test_runtime_denial_codes_are_documented() -> None:
    """模型真会撞上的拒绝码要在 errors.md 里查得到下一步。

    这几个码此前只存在于运行时文案里，文档一个字没有；errors.md 没有字节
    门禁，补齐是零成本的。`TOOL_NOT_READY` 运行时已不再发出（Task 8），换成
    实际会遇到的 `HANDLER_NOT_READY`/`TOOL_NOT_AVAILABLE`/`STALE_CONTEXT`。
    """
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    errors = (
        root / ".claude/skills/configure-dashboard-widget/references/errors.md"
    ).read_text(encoding="utf-8")
    for code in (
        "EMPTY_READBACK_NEEDS_USER",
        "WRITE_BUDGET_EXCEEDED",
        "REPEATED_CALL_BLOCKED",
        "HANDLER_NOT_READY",
        "TOOL_NOT_AVAILABLE",
        "STALE_CONTEXT",
    ):
        assert f"`{code}`" in errors, code
    assert "`TOOL_NOT_READY`" not in errors

    locate = root / ".claude/skills/locate-data"
    locate_errors = (locate / "references/errors.md").read_text(encoding="utf-8")
    assert "`UNSUPPORTED_CAPABILITY`" in locate_errors

    # 字段取值确有其路（dryRun 探针的 probe.sampleRows），指到它，别让模型硬猜。
    locate_tools = (locate / "references/tools.md").read_text(encoding="utf-8")
    assert "sampleRows" in locate_tools


@pytest.mark.parametrize("name", ["tools.md", "fields-and-time.md"])
def test_configure_examples_validate_against_current_tool_contract(name):
    """Executable documentation uses the same schema as real page-tool requests."""
    import json
    import re

    from jsonschema import Draft202012Validator

    from app.agui.contracts import CONTRACT_PATH, load_contract_registry

    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    root = (
        Path(__file__).parents[1]
        / "workspaces/davinci-dashboard/.claude/skills/configure-dashboard-widget/references"
    )
    match = re.search(r"```json\n(.*?)\n```", (root / name).read_text(), re.DOTALL)
    assert match is not None
    schema = registry.get("dashboard.apply_widget_spec").input_schema
    errors = list(Draft202012Validator(schema).iter_errors(json.loads(match.group(1))))
    assert errors == [], [error.message for error in errors]


def test_locate_data_uses_knowledge_receipt_evidence() -> None:
    """data-MCP 知识层（2026-09-14 上线）把社区用量、主时间字段和歧义词选项直接放进回执。

    staging 实测：skill 未改时模型已能读用量证据并在同名表之间提问，但拿到社区主时间
    字段后仍开放式地问"按哪个时间字段"。这里只加最小的三条：用量证据是选表依据、社区
    时间习惯作为默认写进提问、回执里的澄清选项直接用；字段形状放 references，不占预算。
    """
    root = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard"
    locate = (root / ".claude/skills/locate-data/SKILL.md").read_text(encoding="utf-8")
    tools = (root / ".claude/skills/locate-data/references/tools.md").read_text(encoding="utf-8")

    assert "community_usage" in locate
    assert "community_time_habit" in locate
    assert "作为默认写进提问" in locate
    assert "clarifications" in locate and "termClarifications" in locate
    # 早问原则不变：默认值是提给用户确认的，不是替用户决定。
    assert "让用户确认" in locate
    # 字段形状在参考文件里，主文件只说怎么用。
    assert "## 知识层回执" in tools
    assert "distilled_usage" in tools
