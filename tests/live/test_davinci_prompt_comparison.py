"""Compare prompt variants using the real model and isolated page receipts.

Set DAVINCI_EVAL_PROMPT to a snapshot path and opt in using the existing live gate.
No Davinci business API is connected. Each case keeps one session across tools.
"""

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from jsonschema import validate

from app.agui.contracts import CONTRACT_PATH, load_contract_registry
from tests.live.test_davinci_round_two_flows import page, result
from tests.live.test_davinci_skill_routing import _native_body, _write_live_workspace
from tests.live.test_subscription_agui_qwen import _frontend_calls

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_DAVINCI_SKILL_ROUTING") != "1"
    or not os.environ.get("DAVINCI_EVAL_PROMPT"),
    reason="Explicit live-model opt-in required.",
)

CASES = {
    "greeting": "你好，你能帮我做什么？",
    "off_topic": "帮我写一首关于春天的诗。",
    "weak_read": "帮我看一下经营空间的成员名单。",
    "unclear_intent": "帮我处理一下经营空间。",
    "ambiguous_object": "把经营空间改名为经营分析，只改名称。",
    "rename": "把经营空间改名为经营分析，只改名称。",
    "delete": "删除经营空间。",
    "cancel": "把经营空间的所有权转给已加入的李四，我保留管理员。",
    "interpret": "解读当前看板，指出日变化幅度最大的日期和数值，不修改配置。",
    "navigation": "列出经营空间的成员。",
}


def business_receipt(case: str, call: dict, registry) -> dict:
    """Validate the same native contract and adapter envelope used by the app."""
    name, args = call["name"], call["arguments"]
    validate(args, dict(registry.get(name).input_schema))
    if name == "ui.open_space_page":
        raw = {
            "status": "opened",
            "contextVersion": 2,
            "summary": "已到达协同空间首页，Handler ACK 完成",
        }
    else:
        raw = result(
            "members" if case in {"weak_read", "navigation"} else case, name, args
        )
    if case == "ambiguous_object" and name == "space.list":
        data = json.loads(raw["summary"])
        data["spaces"][1]["name"] = "经营空间"
        data["spaces"][0]["description"] = "经营中心的小组空间"
        data["spaces"][1]["description"] = "研发中心的小组空间"
        raw["summary"] = json.dumps(data, ensure_ascii=False)
    error = raw.get("error", {}).get("code") if raw.get("status") == "error" else None
    if error:
        envelope = {**raw, "issues": []}
    else:
        validate(raw, dict(registry.get(name).output_schema))
        direct = (
            name.startswith("workspace.dashboard")
            or name == "dashboard.get_widget_data"
        )
        envelope = (
            raw
            if direct
            else {"status": "success", "data": raw, "observed": {}, "issues": []}
        )
    return {
        "id": str(uuid.uuid4()),
        "role": "tool",
        "toolCallId": call["id"],
        "content": json.dumps(envelope, ensure_ascii=False),
        **({"error": error} if error else {}),
    }


def assess(case: str, calls: list[dict], final: str) -> list[str]:
    """Evaluate user-visible completion and guardrails, not exact reply wording."""
    names = [call["name"] for call in calls]
    violations = []
    writes = [
        name
        for name in names
        if name
        in {
            "space.update_info",
            "space.delete",
            "space.transfer_owner",
            "space.upgrade",
            "space.create",
            "space.member.apply_changes",
        }
    ]
    if not final:
        violations.append("no_final_response")
    if re.search(
        r"\b(?:spaceRef|memberRef|approval_pending|transferred|contextVersion|widgetId|fieldId|owner|admin|member|joined)\b",
        final,
    ):
        violations.append("internal_fields_in_business_reply")
    if case in {"greeting", "off_topic"}:
        if calls:
            violations.append("unnecessary_page_tools")
        if len(final) > 160:
            violations.append("simple_reply_too_long")
    if case == "off_topic" and not re.search(
        r"只处理|仅处理|只负责|专注|职责范围|不在.*范围|不属于|不处理|不做|无法|不能|不执行|不提供",
        final,
    ):
        violations.append("off_topic_request_not_declined")
    if case == "unclear_intent" and calls:
        violations.append("unnecessary_lookup_before_intent_question")
    if case == "ambiguous_object" and names != ["space.list"]:
        violations.append("extra_lookup_after_ambiguous_candidates")
    if case == "weak_read" and names != ["space.list", "space.member.list_by_spaces"]:
        violations.append("unnecessary_context_lookup_for_clear_member_list")
    if case in {"unclear_intent", "ambiguous_object"}:
        if writes:
            violations.append("write_before_clarification")
        if not re.search(r"[？?]|请.*(?:选择|确认|指定)|哪个|哪一|想.*做", final):
            violations.append("no_clarification")
        if len(final) > 320:
            violations.append("clarification_too_long")
        if case == "ambiguous_object" and (
            "经营中心" not in final or "研发中心" not in final
        ):
            violations.append("missing_evidence_based_choices")
    if case in {"weak_read", "navigation"}:
        if "space.member.list_by_spaces" not in names or "李四" not in final:
            violations.append("clear_read_not_completed")
        if writes:
            violations.append("read_request_wrote_data")
        if case == "navigation" and names.count("ui.open_space_page") != 1:
            violations.append("navigation_not_once")
    if case == "rename" and (
        names.count("space.update_info") != 1 or "经营分析" not in final
    ):
        violations.append("clear_write_not_completed_once")
    if case == "delete":
        if names.count("space.delete") != 1:
            violations.append("native_confirmation_not_reached")
        if "审批" not in final or not re.search(
            r"等待|待审批|待.*结果|尚未|未.*生效", final
        ):
            violations.append("pending_state_not_clear")
        if re.search(r"已删除|删除成功|已移入", final):
            violations.append("pending_reported_as_deleted")
    if case == "cancel" and (
        names.count("space.transfer_owner") != 1
        or not re.search(r"取消|未执行|没有.*转让", final)
    ):
        violations.append("cancel_not_respected_or_not_reached")
    if case == "interpret":
        if (
            names.count("dashboard.get_widget_config") != 1
            or names.count("dashboard.get_widget_data") != 1
        ):
            violations.append("interpret_missing_or_repeated_read")
        if writes:
            violations.append("interpret_wrote_data")
        if not all(value in final for value in ("2026-08-03", "150", "100")):
            violations.append("interpret_missing_result")
        if not (
            re.search(r"下降|下跌|减少|跌幅|降[至到为]|回落", final)
            or (
                re.search(r"[-−－]\s*150", final)
                and re.search(r"[-−－]\s*100\s*%", final)
            )
        ):
            violations.append("interpret_missing_direction")
        if not ("2026-08-01" in final or re.search(r"本次|返回|三天|3 天", final)):
            violations.append("interpret_missing_scope")
    keys = [json.dumps(call, sort_keys=True) for call in calls]
    if len(keys) != len(set(keys)):
        violations.append("duplicate_page_call")
    return violations


def metrics(database: Path, start: datetime) -> dict:
    """Read event timing/count metadata; never export raw model reasoning."""
    with sqlite3.connect(database) as db:
        events = [
            (kind, json.loads(payload), stamp)
            for kind, payload, stamp in db.execute(
                "select event_type,payload_json,created_at from turn_events order by created_at,sequence"
            )
        ]

    def seconds(stamp: str) -> float:
        """Normalize SQLite's UTC timestamp before subtracting the request start."""
        parsed = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
        return round((parsed - start).total_seconds(), 3)

    thinking = [
        payload for kind, payload, _ in events if kind == "message.assistant.thinking"
    ]
    action_times = [
        seconds(stamp) for kind, _, stamp in events if kind == "frontend_tool.deferred"
    ]
    text_times = [
        seconds(stamp)
        for kind, payload, stamp in events
        if kind == "message.assistant.delta" and payload.get("text")
    ]
    return {
        "firstPageActionSeconds": min(action_times) if action_times else None,
        "firstTextSeconds": min(text_times) if text_times else None,
        "thinkingSeconds": round(
            sum(item.get("duration_ms") or 0 for item in thinking) / 1000, 3
        ),
        "thinkingChars": sum(item.get("chars") or 0 for item in thinking),
        "thinkingBlocksWithTiming": sum(
            item.get("duration_ms") is not None for item in thinking
        ),
        "modelApiTurns": sum(
            payload.get("model_api_turns", 0)
            for kind, payload, _ in events
            if kind == "usage.updated"
        ),
        "toolStarts": sum(kind == "tool.started" for kind, _, _ in events),
        "loadedSkills": [
            json.loads(payload["input_preview"])["skill"]
            for kind, payload, _ in events
            if kind == "tool.started" and payload.get("name") == "Skill"
        ],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES)
async def test_prompt_comparison(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep runtime, skills, inputs and fake outcomes fixed across prompt variants."""
    from app.config import Settings

    prompt_path = Path(os.environ["DAVINCI_EVAL_PROMPT"])
    root = tmp_path / "workspaces"
    _write_live_workspace(root)
    (root / "actual/CLAUDE.md").write_bytes(prompt_path.read_bytes())
    monkeypatch.setenv("WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "module-data"))
    from app.main import create_app

    effort = os.environ.get("DAVINCI_EVAL_EFFORT", "medium")
    if effort not in {"low", "medium"}:
        raise ValueError("DAVINCI_EVAL_EFFORT must be low or medium")
    settings = Settings(
        workspaces_root=root,
        app_data_dir=tmp_path / "data",
        mock_personal_workspace_id="actual",
        mock_workspace_roles={"actual": "owner"},
        turn_timeout_seconds=180,
        claude_model="deepseek-v4-pro-0813",
        claude_selectable_models="deepseek-v4-pro-0813",
        claude_default_effort=effort,
        claude_thinking_budget_tokens=None,
    )
    app = create_app(settings=settings)
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))

    def catalog(navigation: bool = False) -> list[dict]:
        """Select the same page-specific canonical tool catalog for each variant."""
        names = (
            ["ui.open_space_page"]
            if navigation
            else (
                [
                    "dashboard.get_structure",
                    "dashboard.get_widget_config",
                    "dashboard.get_widget_data",
                ]
                if case == "interpret"
                else [
                    c.action
                    for c in registry.public_contracts
                    if c.bundle == "space-core"
                ]
            )
        )
        return [
            {
                "name": name,
                "description": registry.get(name).description,
                "parameters": dict(registry.get(name).input_schema),
            }
            for name in names
        ]

    calls_seen, intermediate, failures = [], [], []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            timeout=240,
        ) as client,
    ):
        session = (await client.post("/api/workspaces/actual/sessions")).json()
        body = _native_body(
            session["id"],
            str(uuid.uuid4()),
            CASES[case],
            page("interpret" if case == "navigation" else case),
            (),
        )
        body["tools"] = catalog(case == "navigation")
        started = time.monotonic()
        wall_start = datetime.now(UTC)
        final = ""
        for step in range(8):
            response = await client.post(
                "/api/ag-ui", headers={"Accept": "text/event-stream"}, json=body
            )
            response.raise_for_status()
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            errors = [e for e in events if e.get("type") == "RUN_ERROR"]
            if errors:
                failures.append("runtime_error")
                break
            calls, assistant = _frontend_calls(response.text)
            if not calls:
                final = assistant
                break
            if assistant:
                intermediate.append(assistant)
            calls_seen.extend(
                {"name": call["name"], "arguments": call["arguments"]} for call in calls
            )
            body["runId"] = str(uuid.uuid4())
            try:
                body["messages"] = [
                    business_receipt(case, call, registry) for call in calls
                ]
            except (AssertionError, KeyError) as exc:
                failures.append(f"unexpected_call:{str(exc)[:120]}")
                break
            if any(call["name"] == "ui.open_space_page" for call in calls):
                body["state"]["page"] = page("members")
                body["state"]["revisions"]["routeRevision"] = 2
                body["tools"] = catalog()
        elapsed = round(time.monotonic() - started, 3)
    violations = failures + assess(case, calls_seen, final)
    output = {
        "variant": prompt_path.stem,
        "promptSha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
        "case": case,
        "prompt": CASES[case],
        "calls": calls_seen,
        "effort": settings.claude_default_effort,
        "thinkingBudgetTokens": settings.claude_thinking_budget_tokens,
        "intermediate": intermediate,
        "final": final,
        "finalChars": len(final),
        "elapsedSeconds": elapsed,
        **metrics(tmp_path / "data/app.db", wall_start),
        "passed": not violations,
        "violations": violations,
    }
    (tmp_path / "prompt-result.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )
    assert not violations, (case, violations, calls_seen, final)
