from dataclasses import replace
from pathlib import Path

import pytest

from app.runtime.claude import ClaudeAgentRuntime
from tests.test_runtime_events import runtime_request


@pytest.fixture
def davinci_runtime(settings_factory, tmp_path: Path):
    class Credentials:
        expired = False

        def authorization_for_owner(self, owner_key: str) -> str:
            return f"session-{owner_key}"

        def ob_id_for_owner(self, owner_key: str) -> str:
            if self.expired:
                raise LookupError("expired")
            return {"u-test": "00123", "u-bob": "00456"}[owner_key]

    credentials = Credentials()
    runtime = ClaudeAgentRuntime(
        settings_factory(tool_repeat_limit=2),
        environ={"PATH": "/usr/bin", "DAVINCI_DATA_MCP_URL": "https://mcp.test/mcp"},
        mcp_credential_provider=credentials,
    )
    request = runtime_request(tmp_path)
    request.workspace_snapshot["allowed_tools"] = ["Read", "mcp__davinci_data__*", "mcp__other__*"]
    request.workspace_snapshot["mcp_servers"] = {
        "davinci_data": {
            "type": "http",
            "url_env": "DAVINCI_DATA_MCP_URL",
            "authorization_source": "davinci_session",
        }
    }
    return runtime, request, credentials


@pytest.mark.parametrize("tool_name", [
    "catalog_search_datasets", "catalog_get_dataset_schema", "catalog_search_fields",
    "catalog_list_dataset_usages", "access_check_resources", "access_get_apply_plan",
    "analytics_resolve_data_requirements", "catalog_search_semantic_assets",
    "search_data_assets", "get_data_asset", "aggregate_data_assets",
])
@pytest.mark.parametrize("supplied_identity", [None, "another-user"])
async def test_davinci_tools_receive_trusted_ob_id_and_keep_bearer(
    davinci_runtime, tool_name: str, supplied_identity: str | None,
) -> None:
    runtime, request, _ = davinci_runtime
    options = runtime.build_options(request)
    arguments = {"query": "成交金额"}
    if supplied_identity is not None:
        arguments["obId"] = supplied_identity
    gate = options.hooks["PreToolUse"][0].hooks[0]

    decision = await gate({
        "tool_name": f"mcp__davinci_data__{tool_name}", "tool_input": arguments,
    }, "call-1", None)

    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert decision["hookSpecificOutput"]["updatedInput"] == {
        "query": "成交金额", "obId": "00123",
    }
    assert arguments.get("obId") == supplied_identity
    assert options.mcp_servers["davinci_data"] == {
        "type": "http", "url": "https://mcp.test/mcp",
        "headers": {"Authorization": "Bearer session-u-test"},
    }
    assert "调用时省略 obId" in options.system_prompt["append"]


async def test_ob_id_is_resolved_for_each_session_owner(davinci_runtime) -> None:
    runtime, request, _ = davinci_runtime
    for owner, ob_id in [("u-test", "00123"), ("u-bob", "00456")]:
        current_request = replace(request, memory_scope_key=f"{owner}/w-test")
        options = runtime.build_options(current_request)
        gate = options.hooks["PreToolUse"][0].hooks[0]
        decision = await gate({
            "tool_name": "mcp__davinci_data__search_data_assets", "tool_input": {},
        }, owner, None)
        assert decision["hookSpecificOutput"]["updatedInput"]["obId"] == ob_id
        assert options.mcp_servers["davinci_data"]["headers"] == {
            "Authorization": f"Bearer session-{owner}",
        }


async def test_expired_identity_cannot_use_model_supplied_ob_id(davinci_runtime) -> None:
    runtime, request, credentials = davinci_runtime
    gate = runtime.build_options(request).hooks["PreToolUse"][0].hooks[0]
    credentials.expired = True
    decision = await gate({
        "tool_name": "mcp__davinci_data__search_data_assets",
        "tool_input": {"obId": "00123"},
    }, "expired-call", None)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "MCP_IDENTITY_UNAVAILABLE" in decision["hookSpecificOutput"]["permissionDecisionReason"]
    assert runtime.tool_ledger.get(request.platform_session_id).get("expired-call") is None


async def test_changing_ob_id_does_not_bypass_repeat_limit(davinci_runtime) -> None:
    runtime, request, _ = davinci_runtime
    options = runtime.build_options(request)
    gate = options.hooks["PreToolUse"][0].hooks[0]
    record_result = options.hooks["PostToolUse"][0].hooks[0]
    for index in range(3):
        decision = await gate({
            "tool_name": "mcp__davinci_data__search_data_assets",
            "tool_input": {"query": "成交金额", "obId": f"forged-{index}"},
        }, f"call-{index}", None)
        expected = "allow" if index < 2 else "deny"
        assert decision["hookSpecificOutput"]["permissionDecision"] == expected
        if expected == "allow":
            await record_result({
                "tool_name": "mcp__davinci_data__search_data_assets",
            }, f"call-{index}", None)
    assert "REPEATED_CALL_BLOCKED" in decision["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool_name", ["Read", "mcp__other__search"])
async def test_non_davinci_tools_do_not_receive_identity(davinci_runtime, tool_name: str) -> None:
    runtime, request, _ = davinci_runtime
    gate = runtime.build_options(request).hooks["PreToolUse"][0].hooks[0]
    decision = await gate({"tool_name": tool_name, "tool_input": {}}, "other-call", None)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "updatedInput" not in decision["hookSpecificOutput"]


async def test_other_authentication_modes_are_unchanged(davinci_runtime) -> None:
    runtime, request, credentials = davinci_runtime
    request.workspace_snapshot["mcp_servers"]["davinci_data"].pop("authorization_source")
    credentials.expired = True
    options = runtime.build_options(request)
    assert "调用时省略 obId" not in options.system_prompt["append"]
    gate = options.hooks["PreToolUse"][0].hooks[0]
    decision = await gate({
        "tool_name": "mcp__davinci_data__search_data_assets", "tool_input": {},
    }, "other-auth-call", None)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "updatedInput" not in decision["hookSpecificOutput"]
