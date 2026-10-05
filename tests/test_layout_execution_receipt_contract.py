"""Keep existing native layout receipt fields when regenerating both contracts."""

from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from app.agui.contracts import CONTRACT_PATH, load_contract_registry

BASE_SUMMARY = {"rootCount": 4, "resizedCount": 1, "beforeHeight": 20, "afterHeight": 16,
                "sizingSkippedWidgetIds": [], "orderPreserved": True}


def summary_schema():
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    schema = registry.get("dashboard.set_widget_layout").output_schema
    return schema["properties"]["data"]["properties"]["summary"]


def error_schema():
    registry = load_contract_registry(CONTRACT_PATH.with_name("davinci-agent-v2.json"))
    return registry.get("dashboard.set_widget_layout").output_schema["properties"]["error"]


@pytest.mark.parametrize("execution", [
    {"executionMode": "remote", "solverVersion": "constraint-v1", "computeCalls": 1},
    {"executionMode": "remote", "solverVersion": "constraint-v1", "computeCalls": 2},
    {"executionMode": "linear_fallback", "solverVersion": "constraint-v1",
     "remoteStatus": "service_unavailable", "fallbackReason": "remote_unavailable"},
    {"executionMode": "linear_fallback", "solverVersion": "constraint-v1",
     "remoteStatus": "content_verification_failed", "fallbackReason": "candidate_validation_failed"},
])
def test_existing_compact_layout_execution_receipt_paths_remain_valid(execution):
    # These fields are produced together by DashboardLayoutController's compact
    # receipt. They were already supported by Davinci's checked-in contract.
    receipt = {**BASE_SUMMARY,
               "planningElapsedMs": 31, "visualBenchmarkStatus": "not_run", **execution}
    Draft202012Validator(summary_schema()).validate(receipt)


@pytest.mark.parametrize("bad", [
    {"executionMode": "invented"}, {"fallbackReason": "arbitrary"},
    {"remoteStatus": {"hidden": "payload"}}, {"newUnverifiedProperty": True},
])
def test_restoring_existing_fields_does_not_relax_layout_receipt_validation(bad):
    schema = deepcopy(summary_schema())
    assert schema["additionalProperties"] is False
    Draft202012Validator(schema).validate(BASE_SUMMARY)
    receipt = {**BASE_SUMMARY, **bad}
    assert list(Draft202012Validator(schema).iter_errors(receipt))


def test_layout_persistence_error_exposes_bounded_correlation_evidence():
    error = {
        "code": "PERSISTENCE_OUTCOME_UNKNOWN",
        "message": "Persistence could not be confirmed.",
        "retryable": False,
        "layer": "page",
        "phase": "persistence",
        "committed": True,
        "correlationId": "group-request-123",
    }
    Draft202012Validator(error_schema()).validate(error)
    assert list(Draft202012Validator(error_schema()).iter_errors({
        **error, "phase": "private-stage",
    }))
    assert list(Draft202012Validator(error_schema()).iter_errors({
        **error, "correlationId": "x" * 129,
    }))
    assert list(Draft202012Validator(error_schema()).iter_errors({
        **error, "committed": "true",
    }))
