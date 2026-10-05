import pytest


@pytest.mark.asyncio
async def test_deferred_store_binds_thread_run_arguments_and_one_continuation() -> None:
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolError,
        DeferredFrontendToolStore,
    )

    store = DeferredFrontendToolStore(max_entries=4)
    call = DeferredFrontendToolCall.create(
        thread_id="thread-1",
        origin_run_id="run-1",
        tool_call_id="tool-1",
        public_name="dashboard.get_structure",
        arguments={"includeLayout": True},
    )

    assert await store.record(call) == call
    assert await store.record(call) == call
    consumed = await store.consume(
        thread_id="thread-1",
        continuation_run_id="run-2",
        tool_call_id="tool-1",
        content='{"ok":true}',
        error=None,
    )
    replay = await store.consume(
        thread_id="thread-1",
        continuation_run_id="run-2",
        tool_call_id="tool-1",
        content='{"ok":true}',
        error=None,
    )

    assert consumed.status == "accepted"
    assert replay.status == "replayed"
    assert consumed.call.argument_hash == call.argument_hash

    with pytest.raises(DeferredFrontendToolError) as conflict:
        await store.consume(
            thread_id="thread-1",
            continuation_run_id="run-3",
            tool_call_id="tool-1",
            content='{"ok":false}',
            error="failed",
        )
    assert conflict.value.code == "TOOL_RESULT_CONFLICT"


@pytest.mark.asyncio
async def test_deferred_store_rejects_wrong_thread_unknown_and_record_conflict() -> None:
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolError,
        DeferredFrontendToolStore,
    )

    store = DeferredFrontendToolStore(max_entries=2)
    call = DeferredFrontendToolCall.create(
        thread_id="thread-1",
        origin_run_id="run-1",
        tool_call_id="tool-1",
        public_name="page.get_context",
        arguments={},
    )
    await store.record(call)

    with pytest.raises(DeferredFrontendToolError) as wrong_thread:
        await store.consume(
            thread_id="thread-2",
            continuation_run_id="run-2",
            tool_call_id="tool-1",
            content="{}",
            error=None,
        )
    assert wrong_thread.value.code == "SESSION_MISMATCH"

    with pytest.raises(DeferredFrontendToolError) as unknown:
        await store.consume(
            thread_id="thread-1",
            continuation_run_id="run-2",
            tool_call_id="unknown",
            content="{}",
            error=None,
        )
    assert unknown.value.code == "TOOL_NOT_FOUND"

    conflicting = DeferredFrontendToolCall.create(
        thread_id="thread-1",
        origin_run_id="run-1",
        tool_call_id="tool-1",
        public_name="page.get_context",
        arguments={"unexpected": True},
    )
    with pytest.raises(DeferredFrontendToolError) as record_conflict:
        await store.record(conflicting)
    assert record_conflict.value.code == "TOOL_RESULT_CONFLICT"


@pytest.mark.asyncio
async def test_deferred_store_evicts_oldest_entry_at_bound() -> None:
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolError,
        DeferredFrontendToolStore,
    )

    store = DeferredFrontendToolStore(max_entries=2)
    for index in range(3):
        await store.record(
            DeferredFrontendToolCall.create(
                thread_id="thread-1",
                origin_run_id=f"run-{index}",
                tool_call_id=f"tool-{index}",
                public_name="page.get_context",
                arguments={"index": index},
            )
        )

    with pytest.raises(DeferredFrontendToolError) as evicted:
        await store.consume(
            thread_id="thread-1",
            continuation_run_id="run-next",
            tool_call_id="tool-0",
            content="{}",
            error=None,
        )
    assert evicted.value.code == "TOOL_NOT_FOUND"


@pytest.mark.asyncio
async def test_mixed_batch_flag_round_trip() -> None:
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolStore,
    )

    store = DeferredFrontendToolStore()
    call = DeferredFrontendToolCall.create(
        thread_id="t",
        origin_run_id="r",
        tool_call_id="c1",
        public_name="dashboard.get_widget_config",
        arguments={"widgetId": "12945"},
    )
    await store.record(call)
    assert await store.mixed_batch_ids("t", ["c1"]) == set()

    await store.mark_mixed_batch("t", ["c1", "missing"])
    assert await store.mixed_batch_ids("t", ["c1", "other"]) == {"c1"}
    assert await store.mixed_batch_ids("other-thread", ["c1"]) == set()


@pytest.mark.asyncio
async def test_consume_keeps_the_result_for_a_later_repeat_denial() -> None:
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolStore,
        deferred_argument_hash,
    )

    store = DeferredFrontendToolStore()
    call = DeferredFrontendToolCall.create(
        thread_id="t",
        origin_run_id="r",
        tool_call_id="c1",
        public_name="dashboard.get_widget_config",
        arguments={"widgetId": "12945"},
    )
    await store.record(call)
    await store.consume(
        thread_id="t",
        continuation_run_id="r2",
        tool_call_id="c1",
        content='{"status":"success","data":{"effectiveSpec":' + "x" * 5000 + "}}",
        error=None,
    )

    content = await store.latest_result_content(
        "t",
        "dashboard.get_widget_config",
        deferred_argument_hash({"widgetId": "12945"}),
    )
    assert content is not None
    assert content.startswith('{"status":"success"')
    assert len(content) <= 4100


@pytest.mark.asyncio
async def test_large_persistence_result_is_kept_as_compact_valid_json() -> None:
    """Recovery keeps persistence truth while omitting oversized receipt details."""
    import json

    from app.agui.deferred_tools import (
        MAX_KEPT_RESULT_CHARS,
        DeferredFrontendToolCall,
        DeferredFrontendToolStore,
        deferred_argument_hash,
    )

    store = DeferredFrontendToolStore()
    call = DeferredFrontendToolCall.create(
        thread_id="t",
        origin_run_id="r",
        tool_call_id="layout",
        public_name="dashboard.set_widget_layout",
        arguments={"items": [{"widgetId": "1", "x": 0}]},
    )
    await store.record(call)
    result = {
        "status": "success",
        "data": {
            "resourceId": "dashboard-1",
            "resourceRevision": 19,
            "persisted": True,
            "layoutChangeCount": 200,
            "returnedLayoutChangeCount": 200,
            "layoutChangesTruncated": False,
            "layoutChanges": [
                {"widgetId": str(index), "details": "x" * 200}
                for index in range(200)
            ],
        },
        "observed": {"resourceRevision": 19},
        "issues": [],
    }
    await store.consume(
        thread_id="t",
        continuation_run_id="r2",
        tool_call_id="layout",
        content=json.dumps(result),
        error=None,
    )

    content = await store.latest_result_content(
        "t",
        "dashboard.set_widget_layout",
        deferred_argument_hash(call.arguments),
    )
    assert content is not None
    assert len(content) <= MAX_KEPT_RESULT_CHARS
    recovered = json.loads(content)
    assert recovered["status"] == "success"
    assert recovered["data"]["persisted"] is True
    assert recovered["data"]["resourceRevision"] == 19
    assert recovered["data"]["layoutChangeCount"] == 200
    assert recovered["data"]["returnedLayoutChangeCount"] == 0
    assert recovered["data"]["layoutChangesTruncated"] is True
    assert recovered["data"]["readbackAction"] == "dashboard.get_structure"
    assert "layoutChanges" not in recovered["data"]

    # A different call, and a different thread, keep their own answers.
    assert (
        await store.latest_result_content(
            "t", "dashboard.get_structure", deferred_argument_hash({})
        )
        is None
    )
    assert (
        await store.latest_result_content(
            "other",
            "dashboard.get_widget_config",
            deferred_argument_hash({"widgetId": "12945"}),
        )
        is None
    )


@pytest.mark.asyncio
async def test_large_recovery_keeps_real_issues_and_structure_orientation() -> None:
    """Compaction retains bounded causes and useful structure page facts."""
    import json

    from app.agui.deferred_tools import _truncate_result

    layout = {
        "status": "error",
        "data": {"resourceRevision": 7, "persisted": False},
        "issues": [
            {
                "code": "COLLISION",
                "message": "Widgets overlap",
                "retryable": False,
                "widgetIds": [str(index) for index in range(31)],
            }
        ],
        "payload": "x" * 5000,
    }
    recovered = json.loads(_truncate_result(json.dumps(layout)))
    assert recovered["issues"][0]["code"] == "COLLISION"
    assert recovered["issues"][0]["message"] == "Widgets overlap"
    assert recovered["issues"][0]["returnedWidgetIdCount"] == 0
    assert recovered["issues"][0]["widgetIdsTruncated"] is True
    assert recovered["data"] == {"resourceRevision": 7, "persisted": False}
    assert recovered["readbackAction"] == "dashboard.get_structure"

    structure = {
        "status": "success",
        "summary": "100 of 200 widgets",
        "resourceRevision": 7,
        "totalCount": 200,
        "rootCount": 150,
        "childCount": 50,
        "returnedCount": 100,
        "hasMore": True,
        "nextCursor": "page-2",
        "widgets": [{"title": "x" * 100} for _ in range(100)],
    }
    recovered = json.loads(_truncate_result(json.dumps(structure)))
    assert recovered["summary"] == "100 of 200 widgets"
    assert recovered["totalCount"] == 200
    assert recovered["rootCount"] == 150
    assert recovered["childCount"] == 50
    assert recovered["returnedCount"] == 0
    assert recovered["hasMore"] is True
    assert "nextCursor" not in recovered
    assert recovered["readbackAction"] == "dashboard.get_structure"
    assert recovered["readbackRequired"] is True
    assert recovered["restartRead"] is True

    oversized_error = {
        "status": "error",
        "data": {
            "resourceId": "dashboard-1",
            "resourceRevision": 11,
            "persisted": False,
        },
        "error": {
            "code": "PERSIST_FAILED",
            "message": "x" * 10000,
            "retryable": True,
            "layer": "page",
        },
    }
    recovered = json.loads(_truncate_result(json.dumps(oversized_error)))
    assert recovered["status"] == "error"
    assert recovered["data"] == {
        "resourceId": "dashboard-1",
        "resourceRevision": 11,
        "persisted": False,
    }
    assert recovered["error"] == {
        "code": "PERSIST_FAILED",
        "message": "x" * 1000,
        "retryable": True,
        "layer": "page",
    }
    assert len(json.dumps(recovered)) <= 4000


def test_large_recovery_preserves_existing_issue_truncation_truth() -> None:
    """Compaction never turns an already partial issue receipt into a complete one."""
    import json

    from app.agui.deferred_tools import _truncate_result

    receipt = {
        "status": "error",
        "issues": [
            {
                "code": "COLLISION",
                "message": "overlap",
                "retryable": False,
                "widgetIds": [],
                "widgetIdCount": 250,
                "returnedWidgetIdCount": 0,
                "widgetIdsTruncated": True,
            }
        ],
        "issueCount": 1,
        "returnedIssueCount": 1,
        "issuesTruncated": True,
        "payload": "x" * 5000,
    }
    recovered = json.loads(_truncate_result(json.dumps(receipt)))
    assert recovered["issuesTruncated"] is True
    assert recovered["issues"][0]["widgetIdsTruncated"] is True
    assert recovered["issues"][0]["widgetIdCount"] == 250
    assert recovered["issues"][0]["returnedWidgetIdCount"] == 0

@pytest.mark.asyncio
async def test_validation_does_not_bind_a_result_before_persistence():
    """A persistence failure after validation leaves the original result free to retry."""
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolStore,
    )
    store = DeferredFrontendToolStore()
    call = DeferredFrontendToolCall.create(thread_id="t", origin_run_id="origin", tool_call_id="c", public_name="page.get_context", arguments={})
    await store.record(call)
    await store.consume(thread_id="t", continuation_run_id="failed-persistence", tool_call_id="c", content="{}", error=None, validate_only=True)
    accepted = await store.consume(thread_id="t", continuation_run_id="accepted-run", tool_call_id="c", content="{}", error=None)
    assert accepted.status == "accepted"


@pytest.mark.asyncio
async def test_cache_eviction_loads_original_call_and_binding():
    """A missing cache entry cannot turn a historical write into a new execution."""
    from app.agui.deferred_tools import (
        DeferredFrontendToolCall,
        DeferredFrontendToolError,
        DeferredFrontendToolStore,
    )
    store = DeferredFrontendToolStore(max_entries=1)
    old = DeferredFrontendToolCall.create(thread_id="t", origin_run_id="origin", tool_call_id="old", public_name="space.create", arguments={})
    async def load_history(thread):
        """Stand in for the durable repository projection."""
        assert thread == "t"
        return [{"call": old, "continuation_run_id": "accepted", "tool_result": {"content": "{}", "is_error": False}}]
    store.bind_history_loader(load_history)
    restored = await store.get("t", "old")
    assert restored == old
    replay = await store.consume(thread_id="t", continuation_run_id="accepted", tool_call_id="old", content="{}", error=None)
    assert replay.status == "replayed"
    with pytest.raises(DeferredFrontendToolError):
        await store.consume(thread_id="t", continuation_run_id="different", tool_call_id="old", content="{}", error=None)
