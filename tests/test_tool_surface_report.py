import json
import sqlite3

import pytest

from scripts.tool_surface_report import per_tool_usage, profile_tools


def test_profile_tools_mirror_frontend_rule() -> None:
    rows = [
        {"action": "page.get_context", "bundle": "core-navigation", "public": True, "executor": "frontend"},
        {"action": "dashboard.get_structure", "bundle": "dashboard-observe", "public": True, "executor": "frontend"},
        {"action": "space.list", "bundle": "space-core", "public": True, "executor": "frontend"},
        {"action": "space.message_rule.get_context", "bundle": "space-message-rule", "public": True, "executor": "frontend"},
        {"action": "dataset.marketplace.get_context", "bundle": "dataset-marketplace", "public": True, "executor": "frontend"},
        {"action": "dataset.editor.get_context", "bundle": "dataset-editor", "public": True, "executor": "frontend"},
        {"action": "dataset.get_fields", "bundle": "dataset-marketplace", "public": False, "executor": "host"},
    ]
    profiles = profile_tools(rows)
    assert profiles["workspace"] == ["page.get_context", "space.message_rule.get_context"]
    assert profiles["dashboard"] == ["page.get_context", "dashboard.get_structure", "space.message_rule.get_context"]
    assert "space.list" in profiles["space"] and "space.list" not in profiles["dashboard"]
    assert profiles["dataset-marketplace"] == ["page.get_context", "space.message_rule.get_context", "dataset.marketplace.get_context"]
    assert profiles["dataset-editor"] == ["page.get_context", "space.message_rule.get_context", "dataset.marketplace.get_context", "dataset.editor.get_context"]


def test_per_tool_usage_pairs_deferred_with_completed_and_counts_envelope_errors(tmp_path) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("create table turn_events (event_type text, payload_json text, created_at text, session_id text)")
    conn.execute("insert into turn_events values ('frontend_tool.deferred', ?, '2026-09-02 01:00:00', 's1')",
                 (json.dumps({"tool_use_id": "t1", "name": "space.list", "arguments": {}}),))
    conn.execute("insert into turn_events values ('tool.completed', ?, '2026-09-02 01:00:03', 's1')",
                 (json.dumps({"tool_use_id": "t1", "name": "tool", "is_error": False,
                              "output_preview": json.dumps({"status": "error", "error": {"code": "TOOL_TIMEOUT"}})}),))
    conn.commit()
    conn.close()
    rows = per_tool_usage(str(db), since="2026-09-01")
    assert rows == [{"tool": "davinci_ui:space.list", "calls": 1, "errors": 1, "unknown": 0,
                     "statuses": {"error": 1}, "codes": {"TOOL_TIMEOUT": 1}}]


def test_per_tool_usage_buckets_truncated_previews_as_unknown(tmp_path) -> None:
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("create table turn_events (event_type text, payload_json text, created_at text, session_id text)")
    conn.execute("insert into turn_events values ('tool.started', ?, '2026-09-02 01:00:00', 's1')",
                 (json.dumps({"tool_use_id": "t2", "name": "mcp__davinci_data__catalog_search_fields", "input_preview": "{}"}),))
    conn.execute("insert into turn_events values ('tool.completed', ?, '2026-09-02 01:00:03', 's1')",
                 (json.dumps({"tool_use_id": "t2", "name": "mcp__davinci_data__catalog_search_fields", "is_error": False,
                              "output_preview": '{"status":"ok","fields":[' + 'x' * 8000 + '\n...[truncated]'}),))
    conn.commit()
    conn.close()
    rows = per_tool_usage(str(db), since="2026-09-01")
    assert rows[0]["tool"] == "davinci_data:catalog_search_fields"
    assert rows[0]["errors"] == 0 and rows[0]["unknown"] == 1 and rows[0]["statuses"] == {}


def test_per_tool_usage_refuses_a_missing_database(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        per_tool_usage(str(tmp_path / "missing.db"), since="2026-09-01")
    assert not (tmp_path / "missing.db").exists()
