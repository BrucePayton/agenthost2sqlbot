"""Catalog evidence remains scoped and bounded without choosing a business plan."""
from copy import deepcopy
import json

import pytest

from app.runtime.subscription_discovery import catalog_scope_key, record_catalog_evidence, subscription_field_evidence

SCHEMA = "mcp__davinci_data__catalog_get_dataset_schema"
SEARCH = "mcp__davinci_data__catalog_search_datasets"
FIELDS = "mcp__davinci_data__catalog_search_fields"


def task_with_fields():
    return {"scope_key": {"instance": "page", "space": "space-a"},
            "confirmed_selections": {"dataset": {"ref": "widget:orders"}}}


def field(ref="widget:orders/volume", **extra):
    return {"fieldRef": ref, "name": "成交量", "roles": ["metric"], **extra}


def test_partial_relation_warning_does_not_discard_complete_fields():
    task = task_with_fields()
    assert record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders"}, {
        "status": "partial", "fieldCount": 1, "truncated": False,
        "issues": ["relation_summary_unavailable"], "fields": [field(description="成交商品件数")]})
    before = deepcopy(task)
    evidence = subscription_field_evidence(task)
    dataset, = evidence["datasets"]
    assert dataset["complete"] is True
    assert dataset["fields"][0]["description"] == "成交商品件数"
    assert dataset["issues"] == ["relation_summary_unavailable"]
    assert "requirements" not in evidence and task == before


@pytest.mark.parametrize("restriction", [{"query": "成交量"}, {"roles": ["metric"]}, {"fieldRefs": ["widget:orders/volume"]}])
def test_filtered_schema_never_proves_full_inventory(restriction):
    task = task_with_fields()
    record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders", **restriction}, {
        "fieldCount": 1, "truncated": False, "fields": [field()]})
    dataset, = subscription_field_evidence(task)["datasets"]
    assert dataset["complete"] is False and dataset["coverage"]["filtered"] is True


def test_same_label_in_different_sources_retains_both_identities_without_selection():
    task = task_with_fields()
    for source in ("orders", "other"):
        record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:" + source}, {
            "fieldCount": 1, "truncated": False, "fields": [field(f"widget:{source}/volume")]})
    assert [row["datasetRef"] for row in subscription_field_evidence(task)["datasets"]] == ["widget:orders", "widget:other"]
    assert task["confirmed_selections"]["dataset"]["ref"] == "widget:orders"


def test_scope_change_invalidates_prior_evidence_and_rejects_late_receipt():
    task = task_with_fields()
    old_scope = deepcopy(task["scope_key"])
    record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders"}, {"fieldCount": 1, "truncated": False, "fields": [field()]})
    task["scope_key"]["space"] = "space-b"
    assert subscription_field_evidence(task) == {}
    assert not record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders"}, {"fields": []}, scope_key=old_scope)
    assert task["confirmed_selections"]["dataset"]["ref"] == "widget:orders"
    assert catalog_scope_key({**old_scope, "revision": 8}) == old_scope


def test_mcp_wrappers_keep_source_refs_pagination_and_reject_errors():
    task = task_with_fields()
    payload = {"status": "ok", "candidates": [{"datasetRef": "widget:orders", "name": "订单明细",
        "permission": "authorized", "matchedFields": [field()]}], "nextCursor": "more", "coverage": {"exhausted": False}}
    assert record_catalog_evidence(task, SEARCH, {"query": "订单"}, {"content": [{"type": "text", "text": json.dumps(payload)}]})
    choice = task["data_evidence"]["candidates"]
    assert choice["results"][0]["ref"] == "widget:orders" and choice["truncated"]
    assert choice["coverage"] == {"exhausted": False}
    before = deepcopy(task)
    for error in ({"isError": True, "structuredContent": payload}, {"content": [{"type": "text", "text": '{"status":"error"}'}]}):
        assert not record_catalog_evidence(task, SEARCH, {}, error)
        assert task == before


def test_self_built_dataset_identity_does_not_become_underlying_table():
    task = task_with_fields()
    record_catalog_evidence(task, FIELDS, {"datasetRefs": ["widget:orders"]}, {"fields": [
        field(sourceRef="table:warehouse"), field("widget:other/volume")]})
    dataset, = subscription_field_evidence(task)["datasets"]
    assert dataset["datasetRef"] == "widget:orders"
    assert len(dataset["fields"]) == 1 and dataset["fields"][0]["sourceRef"] == "table:warehouse"


def test_v2_encoded_catalog_field_keeps_exact_reference():
    task = task_with_fields()
    ref = "v2/widget:order%2Fsource/order%20amount"
    record_catalog_evidence(task, FIELDS, {"datasetRefs": ["widget:order/source"]}, {"fields": [field(ref)]})
    dataset, = subscription_field_evidence(task)["datasets"]
    assert dataset["datasetRef"] == "widget:order/source" and dataset["fields"][0]["fieldRef"] == ref


def test_metadata_version_change_replaces_previous_complete_inventory():
    task = task_with_fields()
    record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders"}, {
        "fieldCount": 1, "truncated": False, "metadataVersion": 1, "fields": [field()]})
    record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders", "query": "区经"}, {
        "fieldCount": 3, "truncated": True, "metadataVersion": 2,
        "fields": [field("widget:orders/owner", name="区经", isEmployeeAccount=True, employeeAccountType="ob")]})
    dataset, = subscription_field_evidence(task)["datasets"]
    assert dataset["complete"] is False
    assert [row["fieldRef"] for row in dataset["fields"]] == ["widget:orders/owner"]
    assert dataset["fields"][0]["employeeAccountType"] == "ob"


def test_current_native_outputs_remain_distinct_from_catalog_inventory():
    task = task_with_fields()
    record_catalog_evidence(task, SCHEMA, {"datasetRef": "widget:orders"}, {"fieldCount": 1, "truncated": False, "fields": [field()]})
    task.update(native_task_id="draft", revision=2, native_readback={"taskId": "draft", "revision": 2,
        "queries": [{"queryRef": "native-query", "datasetRef": "native-source",
            "fields": [{"ref": "native-volume", "label": "成交量"}],
            "outputs": [{"outputRef": "native-rate", "fieldRef": "native-volume", "isComparison": True}]}]})
    before = deepcopy(task)
    evidence = subscription_field_evidence(task)
    assert evidence["configuredQueries"] == task["native_readback"]["queries"]
    assert evidence["datasets"][0]["fields"][0]["fieldRef"] == "widget:orders/volume"
    assert task == before
    task["native_readback"]["revision"] = 1
    assert subscription_field_evidence(task)["configuredQueries"] == []
