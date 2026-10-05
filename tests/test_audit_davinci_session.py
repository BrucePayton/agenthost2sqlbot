from scripts.audit_davinci_session import (
    SAME_TOOL_LIMIT,
    _error_codes,
    _public_name,
    analyze_events,
)


def ev(
    turn: str,
    seq: int,
    event_type: str,
    payload: dict,
    text: str = "",
    created_at: str | None = None,
) -> dict:
    return {
        "turn_id": turn,
        "sequence": seq,
        "event_type": event_type,
        "payload": payload,
        "created_at": created_at or f"2026-08-17T00:00:{seq:02d}",
        "input_text": text,
    }


def test_public_name_accepts_cold_server_prefix() -> None:
    assert (
        _public_name("mcp__davinci_ui_more__dashboard__rename_widget")
        == "dashboard.rename_widget"
    )
    assert (
        _public_name("mcp__davinci_ui__dashboard__get_structure")
        == "dashboard.get_structure"
    )


def test_subscription_metrics_exclude_user_idle_and_keep_deferred_continuations():
    """Two user requests are not three SDK turns or an hour of model processing."""
    events = [
        ev("t1", 1, "message.user", {"text": "帮我配置"}, created_at="2026-09-09T07:00:00Z"),
        ev("t1", 2, "tool.started", {"tool_use_id": "s", "name": "mcp__davinci_ui__space__message_rule__start_draft",
                                     "input_preview": '{"mode":"blank","scene":"data-alert"}'},
           created_at="2026-09-09T07:00:05Z"),
        ev("t2", 1, "message.user", {"tool_results": [{"tool_call_id": "s", "content": "{}"}]},
           created_at="2026-09-09T07:00:06Z"),
        ev("t2", 2, "tool.completed", {"tool_use_id": "s", "duration_ms": 1000,
                                       "output_preview": '{"status":"success","data":{"status":"active"}}'},
           created_at="2026-09-09T07:00:06Z"),
        ev("t2", 3, "usage.updated", {"model_api_turns": 1, "sdk_duration_api_ms": 3000},
           created_at="2026-09-09T07:00:10Z"),
        ev("t3", 1, "message.user", {"text": "改成十点"}, created_at="2026-09-09T08:00:00Z"),
        ev("t3", 2, "turn.completed", {}, created_at="2026-09-09T08:00:10Z"),
    ]
    report = analyze_events(events, duration_budget_seconds=60)
    assert report.turns == 3 and report.user_requests == 2
    assert report.duration_seconds == 3610
    assert report.active_duration_seconds == 20
    assert not report.over_duration_budget
    assert report.subscription_draft_starts == 1
    assert report.first_editable_draft_seconds == 6
    assert report.tool_duration_ms == 1000
    assert report.model_api_duration_ms == 3000


def test_subscription_save_receipt_does_not_require_querying_dashboard_rows():
    """Subscription persistence is verified by its receipt, never an expensive preview."""
    events = [
        ev("t1", 1, "message.user", {"text": "保存"}),
        ev("t1", 2, "tool.started", {"tool_use_id": "s", "name": "mcp__davinci_ui__space__message_rule__save_draft", "input_preview": "{}"}),
        ev("t2", 3, "message.user", {"tool_results": [{"tool_call_id": "s", "content":
           '{"status":"success","data":{"persisted":true,"finalStatus":"disabled","ruleRef":"r"}}'}]}),
    ]
    assert analyze_events(events).writes_without_readback == []


def test_orphan_tool_use_and_repeats_are_flagged() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "创建柱状图"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "a",
                "name": "mcp__davinci_ui__dashboard__get_structure",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "tool.started",
            {
                "tool_use_id": "b",
                "name": "mcp__davinci_ui__dashboard__get_errors",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            4,
            "frontend_tool.deferred",
            {"tool_use_id": "b", "name": "dashboard.get_errors"},
        ),
        ev("t2", 1, "message.user", {"text": "", "tool_results": []}),
        ev(
            "t2",
            2,
            "tool.started",
            {
                "tool_use_id": "c",
                "name": "mcp__davinci_ui__dashboard__get_structure",
                "input_preview": "{}",
            },
        ),
        ev(
            "t2",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "c", "name": "dashboard.get_structure"},
        ),
        ev("t3", 1, "message.user", {"text": "", "tool_results": []}),
        ev(
            "t3",
            2,
            "tool.started",
            {
                "tool_use_id": "d",
                "name": "mcp__davinci_ui__dashboard__get_structure",
                "input_preview": "{}",
            },
        ),
        ev(
            "t3",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "d", "name": "dashboard.get_structure"},
        ),
        ev("t4", 1, "message.user", {"text": "", "tool_results": []}),
        ev(
            "t4",
            2,
            "tool.started",
            {
                "tool_use_id": "e",
                "name": "mcp__davinci_ui__dashboard__get_structure",
                "input_preview": "{}",
            },
        ),
        ev(
            "t4",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "e", "name": "dashboard.get_structure"},
        ),
    ]

    report = analyze_events(events)

    assert report.orphan_tool_use_ids == ["a"]
    assert report.repeated_calls == [("dashboard.get_structure", "{}", 4)]
    assert report.passed is False


def test_write_without_readback_and_forbidden_tools_are_flagged() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "绑定"}),
        ev(
            "t1",
            2,
            "tool.started",
            {"tool_use_id": "x", "name": "Bash", "input_preview": "ls"},
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {"tool_use_id": "x", "is_error": True},
        ),
        ev(
            "t1",
            4,
            "tool.started",
            {
                "tool_use_id": "w",
                "name": "mcp__davinci_ui__dashboard__set_widget_dataset",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            5,
            "frontend_tool.deferred",
            {"tool_use_id": "w", "name": "dashboard.set_widget_dataset"},
        ),
        ev(
            "t2",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "w",
                        "content": (
                            '{"status":"success","data":'
                            '{"persisted":true,"resourceRevision":1}}'
                        ),
                        "is_error": False,
                    }
                ],
            },
        ),
        ev("t2", 2, "message.assistant.completed", {"text": "已完成绑定"}),
    ]

    report = analyze_events(events)

    assert report.writes_without_readback == ["w"]
    assert report.forbidden_tool_calls == ["Bash"]
    assert report.passed is False


def test_partial_readback_counts_as_readback() -> None:
    write_result = '{"status":"success","data":{"persisted":true,"resourceRevision":1}}'
    partial_readback = (
        '{"status":"partial","data":{"resourceId":"88","widgets":[]},'
        '"observed":{"resourceRevision":1},"issues":[],"pagination":{"truncated":true}}'
    )
    events = [
        ev("t1", 1, "message.user", {"text": "改成近30天"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "w",
                "name": "mcp__davinci_ui__dashboard__apply_widget_spec",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "w", "name": "dashboard.apply_widget_spec"},
        ),
        ev(
            "t2",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "w",
                        "content": write_result,
                        "is_error": False,
                    }
                ],
            },
        ),
        ev(
            "t2",
            2,
            "tool.started",
            {
                "tool_use_id": "r",
                "name": "mcp__davinci_ui__dashboard__get_widget_data",
                "input_preview": "{}",
            },
        ),
        ev(
            "t2",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "r", "name": "dashboard.get_widget_data"},
        ),
        ev(
            "t3",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "r",
                        "content": partial_readback,
                        "is_error": False,
                    }
                ],
            },
        ),
        ev("t3", 2, "message.assistant.completed", {"text": "已完成"}),
    ]

    report = analyze_events(events)

    assert report.writes_without_readback == []


def test_clean_session_passes() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "创建"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "w",
                "name": "mcp__davinci_ui__dashboard__apply_widget_spec",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "w", "name": "dashboard.apply_widget_spec"},
        ),
        ev(
            "t2",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "w",
                        "content": (
                            '{"status":"success","data":'
                            '{"persisted":true,"resourceRevision":2}}'
                        ),
                        "is_error": False,
                    }
                ],
            },
        ),
        ev(
            "t2",
            2,
            "tool.started",
            {
                "tool_use_id": "r",
                "name": "mcp__davinci_ui__dashboard__get_widget_data",
                "input_preview": "{}",
            },
        ),
        ev(
            "t2",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "r", "name": "dashboard.get_widget_data"},
        ),
        ev(
            "t3",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "r",
                        "content": (
                            '{"status":"success","data":'
                            '{"widgets":[{"state":"ready"}]}}'
                        ),
                        "is_error": False,
                    }
                ],
            },
        ),
        ev("t3", 2, "message.assistant.completed", {"text": "完成"}),
    ]

    report = analyze_events(events)

    assert report.passed is True
    assert report.frontend_calls == 2
    assert report.turns == 3


def test_json_argument_order_is_canonicalized_for_repeat_detection() -> None:
    events = [ev("t1", 1, "message.user", {"text": "读取"})]
    for index, preview in enumerate(
        ('{"widgetId":"1","include":true}', '{"include":true,"widgetId":"1"}'),
        start=2,
    ):
        tool_id = f"r{index}"
        events.extend(
            [
                ev(
                    "t1",
                    index * 2,
                    "tool.started",
                    {
                        "tool_use_id": tool_id,
                        "name": "mcp__davinci_ui__dashboard__get_widget_config",
                        "input_preview": preview,
                    },
                ),
                ev(
                    "t1",
                    index * 2 + 1,
                    "frontend_tool.deferred",
                    {
                        "tool_use_id": tool_id,
                        "name": "dashboard.get_widget_config",
                    },
                ),
            ]
        )
    tool_id = "r4"
    events.extend(
        [
            ev(
                "t1",
                8,
                "tool.started",
                {
                    "tool_use_id": tool_id,
                    "name": "mcp__davinci_ui__dashboard__get_widget_config",
                    "input_preview": '{"widgetId":"1","include":true}',
                },
            ),
            ev(
                "t1",
                9,
                "frontend_tool.deferred",
                {
                    "tool_use_id": tool_id,
                    "name": "dashboard.get_widget_config",
                },
            ),
        ]
    )

    report = analyze_events(events)

    assert report.repeated_calls == [
        (
            "dashboard.get_widget_config",
            '{"include":true,"widgetId":"1"}',
            3,
        )
    ]


def test_denied_frontend_tool_serialized_call_is_flagged() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "创建"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "a",
                "name": "mcp__davinci_ui__dashboard__get_structure",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "tool.started",
            {
                "tool_use_id": "b",
                "name": "mcp__davinci_ui__dashboard__get_errors",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            4,
            "tool.completed",
            {
                "tool_use_id": "b",
                "is_error": True,
                "output_preview": (
                    "FRONTEND_TOOL_SERIALIZED: 这条回复里已经有一个正在执行的 "
                    "Davinci 页面工具（dashboard.get_structure），"
                    "同一条回复只能包含一个页面工具调用。"
                    "本次 dashboard.get_errors 未执行。请立即结束这条回复；"
                    "等 dashboard.get_structure 的结果返回后，在下一条回复里再调用 "
                    "dashboard.get_errors。"
                ),
            },
        ),
        ev(
            "t1",
            5,
            "frontend_tool.deferred",
            {"tool_use_id": "a", "name": "dashboard.get_structure"},
        ),
    ]

    report = analyze_events(events)

    assert report.denied_frontend_calls == ["dashboard.get_errors"]
    assert report.passed is False


def test_denied_publish_call_is_flagged() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "刷新一下"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "publish",
                "name": "mcp__davinci_ui__dashboard__publish",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {
                "tool_use_id": "publish",
                "is_error": True,
                "output_preview": (
                    "PUBLISH_REQUIRES_USER_REQUEST: "
                    "本轮用户没有要求发布仪表盘。"
                ),
            },
        ),
    ]

    report = analyze_events(events)

    assert report.denied_frontend_calls == ["dashboard.publish"]
    assert report.passed is False


def test_denied_page_tool_in_flight_call_is_flagged() -> None:
    # 页面工具还没回结果就再调下一个（含 davinci_data）会被 PreToolUse 拒掉，
    # 审计要能认出这条拒绝，否则"串行"违规在报告里是空白。
    events = [
        ev("t1", 1, "message.user", {"text": "看看组件"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "inflight",
                "name": "mcp__davinci_ui__dashboard__get_widget_config",
                "input_preview": '{"widgetId":"1"}',
            },
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {
                "tool_use_id": "inflight",
                "is_error": True,
                "output_preview": (
                    "PAGE_TOOL_IN_FLIGHT: 这条回复里的页面工具"
                    "（dashboard.get_structure）还没有返回结果。"
                    "请立即结束这条回复；结果返回后再在下一条回复里调用 "
                    "dashboard.get_widget_config。"
                ),
            },
        ),
    ]

    report = analyze_events(events)

    assert report.denied_frontend_calls == ["dashboard.get_widget_config"]
    assert report.passed is False


def test_denied_repeated_call_is_flagged() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "查看组件"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "repeat",
                "name": "mcp__davinci_ui__dashboard__get_widget_config",
                "input_preview": '{"widgetId":"1"}',
            },
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {
                "tool_use_id": "repeat",
                "is_error": True,
                "output_preview": (
                    "REPEATED_CALL_BLOCKED: dashboard.get_widget_config "
                    "已用相同参数调用过 3 次。"
                ),
            },
        ),
    ]

    report = analyze_events(events)

    assert report.denied_frontend_calls == ["dashboard.get_widget_config"]
    assert report.passed is False


def test_readback_after_new_user_text_still_closes_pending_write() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "写入"}),
        ev(
            "t1",
            2,
            "tool.started",
            {
                "tool_use_id": "w",
                "name": "mcp__davinci_ui__dashboard__apply_widget_spec",
                "input_preview": "{}",
            },
        ),
        ev(
            "t1",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "w", "name": "dashboard.apply_widget_spec"},
        ),
        ev(
            "t2",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "w",
                        "content": (
                            '{"status":"success","data":{"persisted":true}}'
                        ),
                    }
                ],
            },
        ),
        ev("t3", 1, "message.user", {"text": "核对一下"}),
        ev(
            "t3",
            2,
            "tool.started",
            {
                "tool_use_id": "r",
                "name": "mcp__davinci_ui__dashboard__get_widget_config",
                "input_preview": "{}",
            },
        ),
        ev(
            "t3",
            3,
            "frontend_tool.deferred",
            {"tool_use_id": "r", "name": "dashboard.get_widget_config"},
        ),
        ev(
            "t4",
            1,
            "message.user",
            {
                "text": "",
                "tool_results": [
                    {
                        "tool_call_id": "r",
                        "content": '{"status":"success","data":{}}',
                    }
                ],
            },
        ),
    ]

    report = analyze_events(events)

    assert report.writes_without_readback == []


PERSISTENCE_ERROR = (
    '{"error":{"code":"PERSISTENCE_OUTCOME_UNKNOWN",'
    '"details":{"failureCode":"EXECUTION_FAILED:CLIENT"},"layer":"backend",'
    '"message":"空间创建结果未知，请先使用 space.list 核实后再决定后续操作",'
    '"phase":"persistence","requiredAction":"space.list","retryable":false},'
    '"issues":[],"status":"error"}'
)
WAIT_TIMEOUT_RESULT = (
    '{"data":{"resourceId":"2143","widgets":[{"rows":[],"state":"loading"}]},'
    '"issues":[{"code":"WAIT_TIMEOUT","message":"Timed out while waiting.",'
    '"retryable":true,"targetRef":"13141"},'
    '{"code":"DATA_LOADING","message":"Widget data is still loading.",'
    '"retryable":true,"targetRef":"13141"}],"status":"partial"}'
)


def started(tool_id: str, tool: str, preview: str = "{}") -> dict:
    return {"tool_use_id": tool_id, "name": tool, "input_preview": preview}


def test_error_codes_survive_a_truncated_preview() -> None:
    # output_preview 到 8000 字就被砍断，json.loads 会炸，抽码必须还能工作。
    truncated = PERSISTENCE_ERROR[:80] + "\n...[truncated]"

    assert _error_codes(truncated) == ["PERSISTENCE_OUTCOME_UNKNOWN"]
    assert _error_codes("CATALOG_SEARCH_EXHAUSTED: 本轮检索已达上限。") == [
        "CATALOG_SEARCH_EXHAUSTED"
    ]
    # error.code 是笼统的 EXECUTION_FAILED，真正的原因在 error.message 里。
    assert _error_codes(
        '{"error":{"code":"EXECUTION_FAILED","layer":"page",'
        '"message":"INVALID_ARGUMENT"},"status":"error"}'
    ) == ["EXECUTION_FAILED", "INVALID_ARGUMENT"]


def test_business_tool_error_is_flagged_with_its_code() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "建个空间"}),
        ev("t1", 2, "tool.started", started("c", "mcp__davinci_ui__space__create")),
        ev(
            "t1",
            3,
            "tool.completed",
            {"tool_use_id": "c", "is_error": True, "output_preview": PERSISTENCE_ERROR},
        ),
    ]

    report = analyze_events(events)

    assert report.business_tool_errors == [
        ("space.create", "PERSISTENCE_OUTCOME_UNKNOWN")
    ]
    assert report.contract_error_codes == {"PERSISTENCE_OUTCOME_UNKNOWN": 1}
    assert report.passed is False


def test_pre_tool_denial_stays_out_of_business_errors() -> None:
    # 护栏拒绝和业务报错是两码事，不能因为都带 is_error 就混在一起统计。
    events = [
        ev("t1", 1, "message.user", {"text": "刷新"}),
        ev(
            "t1", 2, "tool.started", started("p", "mcp__davinci_ui__dashboard__publish")
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {
                "tool_use_id": "p",
                "is_error": True,
                "output_preview": (
                    "PUBLISH_REQUIRES_USER_REQUEST: 本轮用户没有要求发布仪表盘。"
                ),
            },
        ),
    ]

    report = analyze_events(events)

    assert report.denied_frontend_calls == ["dashboard.publish"]
    assert report.business_tool_errors == []


def test_contract_error_code_in_issues_is_counted_without_is_error() -> None:
    # WAIT_TIMEOUT 挂在 issues[] 上、is_error 是 false，只看 is_error 会整个漏掉。
    events = [
        ev("t1", 1, "message.user", {"text": "看看数"}),
        ev(
            "t1",
            2,
            "tool.started",
            started("d", "mcp__davinci_ui__dashboard__get_widget_data"),
        ),
        ev(
            "t1",
            3,
            "tool.completed",
            {
                "tool_use_id": "d",
                "is_error": False,
                "output_preview": WAIT_TIMEOUT_RESULT,
            },
        ),
    ]

    report = analyze_events(events)

    assert report.business_tool_errors == []
    assert report.contract_error_codes == {"WAIT_TIMEOUT": 1}
    assert report.passed is False


def test_duration_budget_is_enforced_and_overridable() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "开始"}, created_at="2026-08-17T00:00:00"),
        ev(
            "t1",
            2,
            "message.assistant.completed",
            {"text": "完成"},
            created_at="2026-08-17T00:20:00",
        ),
    ]

    report = analyze_events(events)

    assert report.duration_seconds == 1200.0
    assert report.over_duration_budget is True
    assert report.passed is False

    relaxed = analyze_events(events, duration_budget_seconds=1800.0)

    assert relaxed.over_duration_budget is False
    assert relaxed.passed is True


def test_model_invocation_ratio_is_enforced() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "算一下"}),
        ev(
            "t1",
            2,
            "tool.started",
            started("r", "mcp__davinci_ui__dashboard__get_structure"),
        ),
        ev("t1", 3, "frontend_tool.deferred", {"tool_use_id": "r"}),
        ev("t1", 4, "usage.updated", {"model_api_turns": 5}),
    ]

    report = analyze_events(events)

    assert report.model_invocations == 5
    assert report.tool_calls == 1
    assert report.over_model_budget is True
    assert report.passed is False

    relaxed = analyze_events(events, model_invocation_ratio=6.0)

    assert relaxed.over_model_budget is False
    assert relaxed.passed is True


def test_model_budget_is_skipped_when_no_tool_was_called() -> None:
    # 纯问答没有工具调用，不该因为"模型次数比工具次数多"被判死。
    events = [
        ev("t1", 1, "message.user", {"text": "解释一下口径"}),
        ev("t1", 2, "usage.updated", {"model_api_turns": 3}),
    ]

    report = analyze_events(events)

    assert report.over_model_budget is False
    assert report.passed is True


def test_stalled_turn_and_worst_turn_are_reported() -> None:
    events = [
        ev("t1", 1, "message.user", {"text": "想想"}, created_at="2026-08-17T00:00:00"),
        ev(
            "t1",
            2,
            "usage.updated",
            {"model_api_turns": 11},
            created_at="2026-08-17T00:05:32",
        ),
        ev("t2", 1, "message.user", {"text": "继续"}, created_at="2026-08-17T00:05:40"),
        ev(
            "t2",
            2,
            "usage.updated",
            {"model_api_turns": 1},
            created_at="2026-08-17T00:05:50",
        ),
    ]

    report = analyze_events(events, duration_budget_seconds=3600.0)

    assert report.stalled_turns == [("t1", 11)]
    assert report.worst_turn == ("t1", 332.0, 11, 0)
    assert report.passed is False

    relaxed = analyze_events(
        events, duration_budget_seconds=3600.0, turn_model_invocation_limit=20
    )

    assert relaxed.stalled_turns == []
    assert relaxed.passed is True


def test_same_tool_with_varying_input_is_flagged() -> None:
    # 逐字节相同的入参才触发 repeated_calls，换个参数刷同一个工具得靠 repeated_tools。
    events = [ev("t1", 1, "message.user", {"text": "核实空间"})]
    for index in range(SAME_TOOL_LIMIT):
        tool_id = f"s{index}"
        events.extend(
            [
                ev(
                    "t1",
                    index * 2 + 2,
                    "tool.started",
                    started(
                        tool_id,
                        "mcp__davinci_ui__space__list",
                        f'{{"keyword":"k{index}"}}',
                    ),
                ),
                ev(
                    "t1",
                    index * 2 + 3,
                    "frontend_tool.deferred",
                    {"tool_use_id": tool_id},
                ),
            ]
        )

    report = analyze_events(events)

    assert report.repeated_calls == []
    assert report.repeated_tools == [("space.list", SAME_TOOL_LIMIT)]
    assert report.passed is False
