from app.agui.tool_ledger import (
    ThreadLedger,
    ToolLedgerStore,
    ToolOperation,
    arguments_hash,
    parse_frontend_result,
    query_semantics_write_targets,
)


def op(
    tool_use_id: str,
    name: str,
    args: dict,
    kind: str = "frontend",
    rev: int | None = 1,
    dry_run: bool = False,
) -> ToolOperation:
    targets = query_semantics_write_targets(name, args)
    return ToolOperation(
        tool_use_id=tool_use_id,
        tool_name=name,
        arguments_hash=arguments_hash(args),
        kind=kind,
        revision_before=rev,
        dry_run=dry_run,
        is_query_write=targets is not None and not dry_run,
        target_widget_ids=targets[0] if targets else (),
        dashboard_scope=bool(targets and targets[1]),
    )


def test_identical_calls_count_same_name_args_and_revision() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("创建柱状图")
    ledger.record_call(op("t1", "dashboard.get_structure", {}))
    ledger.resolve("t1", success=True, revision_after=1, write_receipt=False)
    ledger.record_call(op("t2", "dashboard.get_structure", {}))
    ledger.resolve("t2", success=True, revision_after=1, write_receipt=False)
    ledger.record_call(op("t3", "dashboard.get_structure", {}, rev=2))
    ledger.resolve("t3", success=True, revision_after=2, write_receipt=False)
    assert ledger.identical_calls("dashboard.get_structure", arguments_hash({}), 1) == 2
    assert ledger.identical_calls("dashboard.get_structure", arguments_hash({}), 2) == 1
    assert (
        ledger.identical_calls(
            "dashboard.get_structure", arguments_hash({"a": 1}), 1
        )
        == 0
    )


def test_candidate_guard_counts_actual_empty_results_and_resets_on_progress() -> None:
    ledger = ThreadLedger()

    def record(index, count=None, *, scope="fields-a", context="page-a", success=True):
        import json
        call = op(str(index), "space.message_rule.search_options", {"query": str(index)})
        call.candidate_search_scope = scope
        call.context_key = context
        ledger.record_call(call)
        ledger.resolve(str(index), success=success, revision_after=1, write_receipt=False)
        content = json.dumps({"data": {"results": [None] * count}}) if count is not None else "broken"
        ledger.record_candidate_result(str(index), content)

    record(1, 0)
    record(2, 0)
    record(3, 0, success=False)
    record(4)
    assert not ledger.candidate_search_exhausted("fields-a", "page-a")
    record(5, 0)
    assert ledger.candidate_search_exhausted("fields-a", "page-a")
    assert not ledger.candidate_search_exhausted("fields-b", "page-a")
    assert not ledger.candidate_search_exhausted("fields-a", "page-b")
    record(6, 2)
    assert not ledger.candidate_search_exhausted("fields-a", "page-a")
    for i in range(7, 10):
        record(i, 0)
    assert ledger.candidate_search_exhausted("fields-a", "page-a")
    ledger.record_call(op("write", "space.message_rule.apply_draft", {}))
    ledger.resolve("write", success=True, revision_after=2, write_receipt=True)
    assert not ledger.candidate_search_exhausted("fields-a", "page-a")
    for i in range(10, 13):
        record(i, 0)
    assert ledger.candidate_search_exhausted("fields-a", "page-a")
    ledger.start_user_turn("请换一个数据集")
    assert not ledger.candidate_search_exhausted("fields-a", "page-a")


def test_denied_calls_do_not_count_as_identical() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    denied = op("t1", "dashboard.get_structure", {})
    denied.execution_result = "denied"
    ledger.record_call(denied)
    assert ledger.identical_calls("dashboard.get_structure", arguments_hash({}), 1) == 0


def test_identical_calls_only_counts_successful_attempts() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    h = arguments_hash({})
    for index, outcome in enumerate(("success", "error", "denied", "pending")):
        call = op(f"t{index}", "dashboard.get_structure", {})
        if outcome != "pending":
            call.execution_result = outcome
        ledger.record_call(call)
    assert ledger.identical_calls("dashboard.get_structure", h, 1) == 1


def test_failed_attempts_do_not_block_a_retry() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    for index in range(3):
        ledger.record_call(op(f"t{index}", "dashboard.get_widget_data", {}))
        ledger.resolve(
            f"t{index}", success=False, revision_after=None, write_receipt=False
        )
    assert (
        ledger.identical_calls("dashboard.get_widget_data", arguments_hash({}), 1)
        == 0
    )


def test_write_then_readback_receipts() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    ledger.record_call(op("w1", "dashboard.apply_widget_spec", {"widgetId": "1"}))
    ledger.resolve("w1", success=True, revision_after=2, write_receipt=True)
    assert ledger.has_unverified_write() is True
    # 配置读不是数据证据：它证明不了这次写之后还查得到数据。
    ledger.record_call(
        op("r1", "dashboard.get_widget_config", {"widgetId": "1"}, rev=2)
    )
    ledger.resolve("r1", success=True, revision_after=2, write_receipt=False)
    ledger.mark_readback("r1")
    assert ledger.has_unverified_write() is True
    ledger.apply_readback_verification({"1": "ready"}, observed_revision=2)
    assert ledger.has_unverified_write() is False


def test_readback_before_write_does_not_satisfy() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    ledger.record_call(op("r1", "dashboard.get_widget_config", {}))
    ledger.mark_readback("r1")
    ledger.record_call(op("w1", "dashboard.set_widget_dataset", {}))
    ledger.resolve("w1", success=True, revision_after=2, write_receipt=True)
    assert ledger.has_unverified_write() is True


def test_only_query_semantics_writes_require_readback() -> None:
    ledger = ThreadLedger()
    ledger.record_call(op("layout-1", "dashboard.set_widget_layout", {}))
    ledger.resolve(
        "layout-1", success=True, revision_after=2, write_receipt=True
    )
    assert ledger.has_unverified_write() is False

    ledger.record_call(op("spec-1", "dashboard.apply_widget_spec", {}))
    ledger.resolve("spec-1", success=True, revision_after=3, write_receipt=True)
    assert ledger.has_unverified_write() is True


def test_start_user_turn_resets_operations_but_keeps_tool_names() -> None:
    ledger = ThreadLedger()
    ledger.last_tool_names = ("page.get_context",)
    ledger.record_call(op("t1", "page.get_context", {}))
    ledger.start_user_turn("新问题")
    assert ledger.operations == []
    assert ledger.last_tool_names == ("page.get_context",)
    assert ledger.diff_tool_names(
        ("page.get_context", "dashboard.get_structure")
    ) == (("dashboard.get_structure",), ())


def test_parse_frontend_result_extracts_revision_and_persisted() -> None:
    ok = '{"status":"success","data":{"persisted":true,"resourceRevision":3},"observed":{},"issues":[]}'
    assert parse_frontend_result(ok) == (True, 3, True)
    err = '{"status":"error","error":{"code":"TOOL_NOT_AVAILABLE"},"observed":{"resourceRevision":1},"issues":[]}'
    assert parse_frontend_result(err) == (False, 1, False)
    assert parse_frontend_result("not json") == (False, None, False)


def test_parse_frontend_result_treats_partial_readback_as_success() -> None:
    partial = (
        '{"status":"partial","data":{"resourceId":"88","widgets":[]},'
        '"observed":{"resourceRevision":6},"issues":[],"pagination":{"truncated":true}}'
    )
    assert parse_frontend_result(partial) == (True, 6, False)
    partial_write = '{"status":"partial","data":{"persisted":true,"resourceRevision":7},"observed":{},"issues":[]}'
    assert parse_frontend_result(partial_write) == (True, 7, False)


def test_store_isolates_threads() -> None:
    store = ToolLedgerStore()
    a = store.get("thread-a")
    b = store.get("thread-b")
    a.record_call(op("t1", "page.get_context", {}))
    assert b.operations == []
    store.drop("thread-a")
    assert store.get("thread-a").operations == []


def test_publish_objective_survives_intermediate_turns_until_it_is_used() -> None:
    from app.agui.tool_ledger import ThreadLedger

    ledger = ThreadLedger()
    ledger.start_user_turn("发布当前仪表盘", resource_id="88")
    assert ledger.publish_objective == "88"

    ledger.start_user_turn("方案2", resource_id="88")
    assert ledger.publish_objective == "88", "确认修复方案的轮次不应清掉发布目标"

    ledger.clear_publish_objective()
    ledger.start_user_turn("再改个标题", resource_id="88")
    assert ledger.publish_objective is None


def test_publish_objective_clears_on_dashboard_switch_and_negation_and_timeout() -> None:
    from app.agui.tool_ledger import ThreadLedger

    switched = ThreadLedger()
    switched.start_user_turn("发布", resource_id="88")
    switched.start_user_turn("切换到门店数据", resource_id="207")
    assert switched.publish_objective is None

    negated = ThreadLedger()
    negated.start_user_turn("发布", resource_id="88")
    negated.start_user_turn("先不要发布，我看一眼", resource_id="88")
    assert negated.publish_objective is None

    expired = ThreadLedger()
    expired.start_user_turn("发布", resource_id="88")
    for index in range(5):
        expired.start_user_turn(f"第 {index} 个无关请求", resource_id="88")
    assert expired.publish_objective is None


def test_dry_run_apply_does_not_require_readback() -> None:
    from app.agui.tool_ledger import write_requires_readback

    assert (
        write_requires_readback(
            "dashboard.apply_widget_spec", {"widgetId": "1", "dryRun": True}
        )
        is False
    )
    assert (
        write_requires_readback("dashboard.apply_widget_spec", {"widgetId": "1"})
        is True
    )


def test_a_dry_run_probe_owes_no_readback() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("为什么没数据")
    probe = op("t1", "dashboard.apply_widget_spec", {"dryRun": True})
    probe.dry_run = True
    ledger.record_call(probe)
    ledger.resolve("t1", success=True, revision_after=1, write_receipt=True)

    assert ledger.operations[-1].readback_required is False
    assert ledger.has_unverified_write() is False

    # A real write on the same Widget still owes one.
    ledger.record_call(op("t2", "dashboard.apply_widget_spec", {}))
    ledger.resolve("t2", success=True, revision_after=2, write_receipt=True)
    assert ledger.has_unverified_write() is True


def test_query_semantics_targets_cover_all_write_shapes() -> None:
    from app.agui.tool_ledger import query_semantics_write_targets as targets

    assert targets("dashboard.apply_widget_spec", {"widgetId": "12945", "spec": {}}) == (
        ("12945",),
        False,
    )
    # create 路径入参没有任何 id：目标留空，等回执回来再补绑。
    assert targets("dashboard.apply_widget_spec", {"create": {"chartType": 1}, "spec": {}}) == (
        (),
        False,
    )
    assert targets("dashboard.apply_widget_spec", {"widgetId": "1", "dryRun": True}) is None
    assert targets("dashboard.set_widget_dataset", {"widgetId": "7"}) == (("7",), False)
    assert targets("dashboard.apply_global_filter_edits", {"operations": []}) == ((), True)
    assert targets("dashboard.set_widget_layout", {"layouts": []}) is None

    comparison = {
        "operations": [
            {
                "widgetId": "9",
                "edits": [{"capabilityId": "metric.comparison.monthChain"}],
            }
        ]
    }
    assert targets("dashboard.apply_widget_edits", comparison) == (("9",), False)

    style_only = {
        "operations": [
            {
                "widgetId": "9",
                "edits": [{"capabilityId": "appearance.background.color"}],
            }
        ]
    }
    assert targets("dashboard.apply_widget_edits", style_only) is None


def test_stray_dry_run_on_other_tools_is_not_exempt() -> None:
    from app.agui.tool_ledger import (
        query_semantics_write_targets,
        write_requires_readback,
    )

    # 契约里只有 apply_widget_spec 定义 dryRun；平台不校验前端工具输入，
    # 所以别的写工具带上 dryRun 键不得抬起任何门禁。
    assert query_semantics_write_targets(
        "dashboard.set_widget_dataset", {"widgetId": "7", "dryRun": True}
    ) == (("7",), False)
    assert (
        write_requires_readback(
            "dashboard.set_widget_dataset", {"widgetId": "7", "dryRun": True}
        )
        is True
    )
    assert (
        write_requires_readback(
            "dashboard.apply_widget_spec", {"widgetId": "7", "dryRun": True}
        )
        is False
    )


def _write(ledger, tool_use_id, tool, args, receipt_revision=2):
    ledger.record_call(op(tool_use_id, tool, args))
    ledger.resolve(
        tool_use_id,
        success=True,
        revision_after=receipt_revision,
        write_receipt=True,
    )


def test_readback_must_cover_the_written_widget() -> None:
    """写 A 读 B 不再清义务；覆盖 A 才清。"""
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    _write(ledger, "w1", "dashboard.apply_widget_spec", {"widgetId": "A", "spec": {}})
    ledger.apply_readback_verification({"B": "ready"}, observed_revision=2)
    assert ledger.has_unverified_write() is True
    ledger.apply_readback_verification({"A": "ready", "B": "ready"}, observed_revision=2)
    assert ledger.has_unverified_write() is False


def test_missing_or_error_state_does_not_verify() -> None:
    """读一个 missing 的 id 不能算验证过，否则这道门形同虚设。"""
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    _write(ledger, "w1", "dashboard.apply_widget_spec", {"widgetId": "A", "spec": {}})
    ledger.apply_readback_verification({"A": "missing"}, observed_revision=9)
    assert ledger.has_unverified_write() is True
    ledger.apply_readback_verification({"A": "error"}, observed_revision=9)
    assert ledger.has_unverified_write() is True
    # unavailable 覆盖消息组件：它没有查询路径，数据级验证本就不可能。
    ledger.apply_readback_verification({"A": "unavailable"}, observed_revision=9)
    assert ledger.has_unverified_write() is False


def test_spec_readback_requires_receipt_revision_or_newer() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    _write(
        ledger,
        "w1",
        "dashboard.apply_widget_spec",
        {"widgetId": "A", "spec": {}},
        receipt_revision=5,
    )
    ledger.apply_readback_verification({"A": "ready"}, observed_revision=4)
    assert ledger.has_unverified_write() is True
    ledger.apply_readback_verification({"A": "ready"}, observed_revision=5)
    assert ledger.has_unverified_write() is False

    # observed 缺席（旧前端没传 provider revision）→ 降级为覆盖判定。
    _write(
        ledger,
        "w2",
        "dashboard.apply_widget_spec",
        {"widgetId": "A", "spec": {}},
        receipt_revision=6,
    )
    ledger.apply_readback_verification({"A": "ready"}, observed_revision=None)
    assert ledger.has_unverified_write() is False


def test_create_receipt_binds_the_new_widget_id() -> None:
    """create 入参没有 id：resolve 后由回执 data.widgetId 补绑。"""
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    ledger.record_call(
        op("w1", "dashboard.apply_widget_spec", {"create": {"chartType": 1}, "spec": {}})
    )
    ledger.resolve("w1", success=True, revision_after=3, write_receipt=True)
    # 补绑前读回任何东西都不该清掉这条义务。
    ledger.apply_readback_verification({"10815": "ready"}, observed_revision=3)
    assert ledger.has_unverified_write() is True
    ledger.bind_write_receipt("w1", "10815")
    ledger.apply_readback_verification({"10815": "ready"}, observed_revision=3)
    assert ledger.has_unverified_write() is False


def test_global_filter_edit_clears_on_any_successful_readback() -> None:
    """dashboard 级义务没有 widget 绑定的事实来源，任何成功读回即清。"""
    ledger = ThreadLedger()
    ledger.start_user_turn("x")
    _write(ledger, "w1", "dashboard.apply_global_filter_edits", {"operations": []})
    assert ledger.has_unverified_write() is True
    ledger.apply_readback_verification({"whatever": "ready"}, observed_revision=None)
    assert ledger.has_unverified_write() is False


def test_parse_receipt_widget_id_and_verifications() -> None:
    from app.agui.tool_ledger import (
        parse_receipt_widget_id,
        readback_widget_verifications,
    )

    receipt = '{"status":"success","data":{"widgetId":"10815","persisted":true}}'
    assert parse_receipt_widget_id(receipt) == "10815"
    assert parse_receipt_widget_id('{"status":"success","data":{}}') == ""
    assert parse_receipt_widget_id("not json") == ""

    data = (
        '{"status":"success","data":{"widgets":['
        '{"widgetId":"1","state":"ready","rows":[{"a":1}]},'
        '{"widgetId":"2","rows":[]},'
        '{"widgetId":"3","state":"missing"}]}}'
    )
    assert readback_widget_verifications(data) == {
        "1": "ready",
        "2": "empty",
        "3": "missing",
    }


def test_identical_calls_reset_after_a_successful_write() -> None:
    ledger = ThreadLedger()
    ledger.start_user_turn("建目录", resource_id="")
    first = op("t1", "space.menu.get_context", {}, rev=None)
    ledger.record_call(first)
    ledger.resolve("t1", success=True, revision_after=None, write_receipt=False)
    assert ledger.identical_calls("space.menu.get_context", arguments_hash({}), None) == 1

    write = op("t2", "space.menu.apply_changes", {"operation": "create_group", "name": "奢侈品"}, rev=None)
    ledger.record_call(write)
    ledger.resolve("t2", success=True, revision_after=None, write_receipt=True)

    assert ledger.write_epoch == 1
    # 写成功后再读同一个上下文不再算"相同页面版本下的重复"
    assert ledger.identical_calls("space.menu.get_context", arguments_hash({}), None) == 0


def _apply_widget_spec_write() -> ToolOperation:
    return op(
        "t1",
        "dashboard.apply_widget_spec",
        {
            "create": {"chartType": 2001},
            "spec": {
                "dataset": {"datasetUid": "662", "datasetType": "warehouseTopic"},
                "metrics": [{"fieldId": "27068"}],
                "dimensions": [],
                "filters": [],
            },
        },
        rev=3,
    )


def test_receipt_probe_counts_as_readback() -> None:
    import json

    from app.agui.tool_ledger import parse_receipt_probe_states, parse_receipt_widget_id

    ledger = ThreadLedger()
    ledger.start_user_turn("建指标卡", resource_id="d1")
    write = _apply_widget_spec_write()
    ledger.record_call(write)
    receipt = json.dumps({"status": "success", "data": {"resourceId": "d1", "resourceRevision": 4,
                          "persisted": True, "state": "updated", "widgetId": "999", "created": True,
                          "appliedSpec": {}, "probe": {"rowCount": 42}}})
    ledger.resolve("t1", success=True, revision_after=4, write_receipt=True)
    ledger.bind_write_receipt("t1", parse_receipt_widget_id(receipt))
    ledger.apply_readback_verification(parse_receipt_probe_states(receipt), 4)
    assert ledger.has_unverified_write() is False


def test_receipt_probe_with_unavailable_reason_is_not_readback() -> None:
    """探针跑不起来时前端把回执降为 partial 并带 unavailableReason；不能据此解除读回义务。"""
    import json

    from app.agui.tool_ledger import parse_receipt_probe_states, parse_receipt_widget_id

    ledger = ThreadLedger()
    ledger.start_user_turn("建指标卡", resource_id="d1")
    write = _apply_widget_spec_write()
    ledger.record_call(write)
    receipt = json.dumps({"status": "partial", "data": {"resourceId": "d1", "resourceRevision": 4,
                          "persisted": True, "state": "updated", "widgetId": "999", "created": True,
                          "appliedSpec": {},
                          "probe": {"rowCount": 0, "unavailableReason": "QUERY_TIMEOUT"}}})
    ledger.resolve("t1", success=True, revision_after=4, write_receipt=True)
    ledger.bind_write_receipt("t1", parse_receipt_widget_id(receipt))
    assert parse_receipt_probe_states(receipt) == {}
    ledger.apply_readback_verification(parse_receipt_probe_states(receipt), 4)
    assert ledger.has_unverified_write() is True


def test_receipt_probe_requires_persisted_true() -> None:
    import json

    from app.agui.tool_ledger import parse_receipt_probe_states

    receipt = json.dumps(
        {"status": "success", "data": {"persisted": False, "widgetId": "999", "probe": {"rowCount": 0}}}
    )
    assert parse_receipt_probe_states(receipt) == {}


def test_receipt_probe_requires_a_successful_status() -> None:
    import json

    from app.agui.tool_ledger import parse_receipt_probe_states

    receipt = json.dumps(
        {"status": "error", "data": {"persisted": True, "widgetId": "999", "probe": {"rowCount": 0}}}
    )
    assert parse_receipt_probe_states(receipt) == {}


def test_receipt_probe_row_count_must_be_a_non_negative_int() -> None:
    import json

    from app.agui.tool_ledger import parse_receipt_probe_states

    negative = json.dumps(
        {"status": "success", "data": {"persisted": True, "widgetId": "999", "probe": {"rowCount": -1}}}
    )
    assert parse_receipt_probe_states(negative) == {}
    non_integer = json.dumps(
        {"status": "success", "data": {"persisted": True, "widgetId": "999", "probe": {"rowCount": "42"}}}
    )
    assert parse_receipt_probe_states(non_integer) == {}


def test_subscription_identical_failures_stop_but_context_recovery_and_new_turn_are_allowed():
    from app.agui.tool_ledger import ThreadLedger, ToolOperation

    ledger = ThreadLedger()
    name = 'space.message_rule.apply_draft'
    for index in range(2):
        ledger.record_call(ToolOperation(str(index), name, 'same', 'frontend', None,
                                        execution_result='error', context_key='page'))
    assert ledger.repeated_failed_subscription_call(name, 'same', 'page')
    assert not ledger.repeated_failed_subscription_call(name, 'fixed-arguments', 'page')
    assert not ledger.repeated_failed_subscription_call(name, 'same', 'other-page')
    ledger.record_call(ToolOperation('context', 'space.message_rule.get_context', '{}', 'frontend', None,
                                    execution_result='success', context_key='page'))
    assert not ledger.repeated_failed_subscription_call(name, 'same', 'page')
    ledger.start_user_turn('New requirement')
    assert not ledger.repeated_failed_subscription_call(name, 'same', 'page')
