"""Offline checks for the live model's fixture; native tests own real behavior."""

import asyncio
import json

import pytest
from jsonschema.exceptions import ValidationError

from tests.live.test_subscription_agui_qwen import (
    SubscriptionLiveReport,
    SubscriptionPageSimulator,
    _evaluation_model,
    _native_body,
    _recorded_page_turn,
    _write_isolated_workspace,
)


def call(simulator: SubscriptionPageSimulator, action: str, **arguments) -> dict:
    """Exercise the existing live simulator through its canonical schema gate."""
    result = json.loads(simulator.result(f"space.message_rule.{action}", arguments))
    assert result["status"] == "success"
    return result["data"]


def configured_fixture(*, allow_save: bool = False) -> SubscriptionPageSimulator:
    """Create the declared group-report fixture, never a real subscription rule."""
    simulator = SubscriptionPageSimulator(allow_save=allow_save)
    call(simulator, "get_context")
    call(simulator, "search_options", kind="dataset", query="奢侈品回收数据")
    fields = call(simulator, "search_options", kind="field", datasetRef="ref-dataset-luxury")
    employee = next(item for item in fields["results"] if item["ref"] == "ref-field-employee")
    assert employee["isEmployeeAccount"] is True
    call(simulator, "search_options", kind="enum_value", fieldRef="ref-field-status")
    call(simulator, "start_draft", mode="blank", scene="dashboard-push")
    call(simulator, "apply_draft", expectedRevision=1, operations=[
        {"operation": "set_schedule", "frequency": "daily", "times": ["09:00"]},
        {"operation": "set_send_rule", "sendRule": "scheduled_dataset"},
        {"operation": "upsert_dataset_query", "datasetRef": "ref-dataset-luxury",
         "metricRefs": ["ref-field-amount"],
         "dimensionRefs": ["ref-field-city", "ref-field-manager", "ref-field-employee"],
         "filters": [{"fieldRef": "ref-field-status", "operator": "eq",
                      "valueRefs": ["ref-value-completed"]}]},
    ])
    call(simulator, "apply_draft", expectedRevision=2, operations=[
        {"operation": "set_push_mode", "mode": "group",
         "queryRef": simulator.query_ref, "groupByFieldRef": "ref-field-manager"},
        {"operation": "set_recipients", "fieldRecipients": [
            {"queryRef": simulator.query_ref, "fieldRef": "ref-field-employee"}]},
        {"operation": "set_content", "title": "奢侈品回收日报",
         "components": [{"type": "richtext", "markdown": "请查收回收日报"},
                        {"type": "data-table", "queryRef": simulator.query_ref,
                         "outputRefs": ["output-ref-field-city", "output-ref-field-amount"]}]},
        {"operation": "set_finalize", "ruleName": "奢侈品回收日报",
         "desiredStatus": "disabled"},
    ])
    return simulator


def test_configuration_terminal_is_reviewed_editable_draft_without_preview_or_save():
    """All fixture requirements must survive patches before review can complete."""
    simulator = configured_fixture()
    review = call(simulator, "review_draft", expectedRevision=3, includeDataCheck=False)
    assert review["complete"] is True
    assert review["dataChecks"] == []
    assert simulator.reviewed_revision == 3
    assert simulator.receipt is None
    draft = call(simulator, "get_context")["activeDraft"]
    assert draft["editable"] is True
    assert draft["lifecycle"] == "active"
    assert draft["queries"][0]["outputs"]
    assert isinstance(draft["summary"], str)
    assert len(draft["configuration"]["operations"]) == 7
    with pytest.raises(AssertionError, match="editable draft"):
        call(simulator, "save_draft", expectedRevision=3, desiredStatus="disabled")


def test_blank_or_incorrect_fixture_does_not_receive_always_complete_review():
    """Do not let a model pass this gate just by calling review_draft."""
    simulator = SubscriptionPageSimulator()
    call(simulator, "start_draft", mode="blank", scene="dashboard-push")
    review = call(simulator, "review_draft")
    assert review["complete"] is False
    assert len(review["errors"]) == 6
    simulator = configured_fixture()
    call(simulator, "apply_draft", expectedRevision=3, operations=[
        {"operation": "set_schedule", "frequency": "daily", "times": ["18:00"]},
    ])
    review = call(simulator, "review_draft", expectedRevision=4)
    assert review["complete"] is False
    assert review["errors"][0]["section"] == "trigger"


def test_explicit_save_requires_native_confirmation_and_validated_receipt():
    """A save call alone is not success, and no real service is written here."""
    simulator = configured_fixture(allow_save=True)
    call(simulator, "review_draft", expectedRevision=3)
    with pytest.raises(AssertionError):
        call(simulator, "save_draft", expectedRevision=3, desiredStatus="disabled")
    assert simulator.receipt is None
    simulator.confirm_native(3)
    receipt = call(simulator, "save_draft", expectedRevision=3, desiredStatus="disabled")
    assert receipt == simulator.receipt
    assert receipt["persisted"] is True
    assert receipt["finalStatus"] == "disabled"
    assert receipt["ownershipScope"] == "space"
    assert receipt["ruleRef"]
    with pytest.raises(AssertionError, match="no editable draft"):
        call(simulator, "save_draft", expectedRevision=3, desiredStatus="disabled")


def test_revision_change_invalidates_review_and_confirmation():
    """A patch after native confirmation cannot reuse that confirmation."""
    simulator = configured_fixture(allow_save=True)
    call(simulator, "review_draft", expectedRevision=3)
    simulator.confirm_native(3)
    call(simulator, "apply_draft", expectedRevision=3, operations=[
        {"operation": "set_finalize", "ruleName": "用户修改的回收日报", "desiredStatus": "disabled"},
    ])
    assert simulator.confirmed_revision is None
    assert simulator.reviewed_revision is None
    with pytest.raises(AssertionError, match="stale revision"):
        call(simulator, "save_draft", expectedRevision=3, desiredStatus="disabled")


def test_schema_and_reference_validation_reject_fake_tool_contracts():
    """Old options/summary objects and guessed handles must fail this gate."""
    simulator = SubscriptionPageSimulator()
    with pytest.raises(ValidationError):
        call(simulator, "start_draft", mode="blank", scope="personal")
    assert simulator.revision == 0
    with pytest.raises(AssertionError, match="unissued parent"):
        call(simulator, "search_options", kind="field", datasetRef="made-up")
    call(simulator, "start_draft", mode="blank", scene="dashboard-push")
    with pytest.raises(AssertionError, match="unissued option"):
        call(simulator, "apply_draft", expectedRevision=1, operations=[
            {"operation": "set_recipients", "memberRefs": ["made-up"]},
        ])
    assert simulator.revision == 1
    simulator._result = lambda name, arguments: {"kind": "dataset", "options": [], "summary": {}}
    with pytest.raises(ValidationError):
        call(simulator, "search_options", kind="dataset")


def test_configuration_fixture_does_not_silently_execute_preview():
    """The default model scenario has no authorization or need to query rows."""
    simulator = configured_fixture()
    with pytest.raises(AssertionError, match="did not request data preview"):
        call(simulator, "review_draft", expectedRevision=3, includeDataCheck=True)


def test_dynamic_employee_must_also_be_present_in_query_outputs():
    """A configured receiver alone must not make an impossible model trace pass."""
    simulator = configured_fixture()
    call(simulator, "apply_draft", expectedRevision=3, operations=[
        {"operation": "upsert_dataset_query", "queryRef": simulator.query_ref,
         "dimensionRefs": ["ref-field-city", "ref-field-manager"]},
    ])
    result = call(simulator, "review_draft", expectedRevision=4)
    assert result["complete"] is False
    assert {error["section"] for error in result["errors"]} >= {"datasets", "receivers"}


def test_live_model_uses_workspace_configuration_or_explicit_override(tmp_path, monkeypatch):
    """Changing the workspace model must not leave the live gate pinned to Qwen."""
    import yaml
    from pathlib import Path

    monkeypatch.delenv("DAVINCI_SUBSCRIPTION_EVAL_MODEL", raising=False)
    source = Path(__file__).resolve().parents[1] / "workspaces/davinci-dashboard/workspace.yaml"
    expected = yaml.safe_load(source.read_text(encoding="utf-8"))["model"]
    assert _evaluation_model() == expected
    monkeypatch.setenv("DAVINCI_SUBSCRIPTION_EVAL_MODEL", " explicit-test-model ")
    assert _evaluation_model() == "explicit-test-model"
    _write_isolated_workspace(tmp_path)
    isolated = yaml.safe_load((tmp_path / "actual/workspace.yaml").read_text(encoding="utf-8"))
    assert isolated["model"] == "explicit-test-model"
    assert isolated["mcp_servers"] == {}
    assert _native_body("session", "run")["forwardedProps"]["model"] == "explicit-test-model"


def test_live_report_keeps_failure_evidence_without_exception_or_runtime_payloads(tmp_path):
    """Test-owned reports must retain outcomes but never copy raw exception text."""
    with pytest.raises(ValueError):
        with SubscriptionLiveReport(tmp_path, "safe-report", "test-model") as report:
            report.capture_runtime_tools([
                {"event_type": "tool.started", "payload": {"name": "Skill", "secret": "DO_NOT_COPY"}},
                {"event_type": "tool.started", "payload": {"name": "Read"}},
                {"event_type": "runtime.diagnostic", "payload": {
                    "code": "SUBSCRIPTION_RUNTIME_FAILURE_TIMING", "secret": "DO_NOT_COPY",
                    "runtime_timing": {"total_ms": 60000, "incomplete": True,
                        "query_to_first_text_ms": None}}},
            ])
            report.data["terminal"] = "clarification-needed"
            report.data["clarificationRounds"] = 1
            raise ValueError("DO_NOT_COPY raw stderr/settings")
    recorded = report.path.read_text(encoding="utf-8")
    evidence = json.loads(recorded)
    assert "DO_NOT_COPY" not in recorded
    assert evidence["outcome"] == "failed"
    assert evidence["failureType"] == "ValueError"
    assert evidence["skillLoadCount"] == 1
    assert evidence["clarificationRounds"] == 1
    assert evidence["totalElapsedSeconds"] >= 0
    assert evidence["userUtterances"]
    assert evidence["modelTurns"] is None
    assert evidence["runtimeTimings"] == []
    assert evidence["failedRuntimeTimings"] == [{"total_ms": 60000, "incomplete": True,
        "query_to_first_text_ms": None}]


def test_live_turn_report_records_requests_calls_and_elapsed_without_model_access(tmp_path):
    """Exercise the recorder against a fake HTTP response, not an external model."""
    events = [
        {"type": "TOOL_CALL_START", "toolCallId": "call-1", "toolCallName": "space.message_rule.get_context"},
        {"type": "TOOL_CALL_ARGS", "toolCallId": "call-1", "delta": "{}"},
        {"type": "TEXT_MESSAGE_CONTENT", "delta": "我先检查当前草稿。"},
    ]

    class FakeResponse:
        status_code = 200
        text = "\n".join("data: " + json.dumps(event) for event in events)

    class FakeClient:
        async def post(self, *args, **kwargs):
            """Return only local fixture events."""
            return FakeResponse()

    with SubscriptionLiveReport(tmp_path, "record-turn", "test-model") as report:
        calls, assistant = asyncio.run(_recorded_page_turn(
            FakeClient(), _native_body("session", "run", "test-model"), report,
        ))
        assert calls[0]["arguments"] == {}
        assert assistant == "我先检查当前草稿。"
    evidence = json.loads(report.path.read_text(encoding="utf-8"))
    assert evidence["outcome"] == "passed"
    assert evidence["rounds"][0]["userMessages"]
    assert evidence["rounds"][0]["tools"][0]["simulatedResult"] is None
    assert evidence["rounds"][0]["elapsedSeconds"] >= 0
    assert evidence["toolCalls"] == 1
    assert evidence["dataPreviewRequests"] == evidence["ruleListRequests"] == 0


def test_live_stream_error_is_not_misclassified_as_clarification(tmp_path):
    """HTTP 200 with RUN_ERROR must fail without persisting its sensitive message."""
    class FakeResponse:
        status_code = 200
        text = 'data: {"type":"RUN_ERROR","message":"DO_NOT_COPY raw stderr"}'

    class FakeClient:
        async def post(self, *args, **kwargs):
            """Return a local streamed error fixture."""
            return FakeResponse()

    with pytest.raises(pytest.fail.Exception):
        with SubscriptionLiveReport(tmp_path, "stream-error", "test-model") as report:
            asyncio.run(_recorded_page_turn(
                FakeClient(), _native_body("session", "run", "test-model"), report,
            ))
    recorded = report.path.read_text(encoding="utf-8")
    evidence = json.loads(recorded)
    assert evidence["outcome"] == "failed"
    assert evidence["terminal"] == "host-run-error"
    assert evidence["clarificationRounds"] == 0
    assert "DO_NOT_COPY" not in recorded
