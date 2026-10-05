import json
from pathlib import Path
from typing import Any


def dashboard_context(version: int = 3):
    from app.agui.models import HostContext

    return HostContext(
        pageType="dashboard",
        resourceId="1024",
        contextVersion=version,
        supportedCapabilities=["dashboard.capture_current_view"],
        supportedCommands=["navigateTo"],
    )


def dataset_context(version: int = 4):
    from app.agui.models import HostContext

    return HostContext(
        pageType="dataset",
        resourceId=None,
        contextVersion=version,
        supportedCapabilities=[],
        supportedCommands=["navigateTo"],
    )


def dashboard_tools():
    from app.agui.models import CAPTURE_TOOL, NAVIGATE_TOOL

    return [CAPTURE_TOOL.model_copy(deep=True), NAVIGATE_TOOL.model_copy(deep=True)]


def snapshot_payload(*, context_version: int = 3) -> dict[str, Any]:
    return {
        "schemaVersion": "mock-dashboard-snapshot-v1",
        "page": {
            "pageType": "dashboard",
            "dashboardId": "1024",
            "title": "南区经营仪表盘",
            "contextVersion": context_version,
            "capturedAt": "2026-08-06T16:00:00+08:00",
        },
        "filters": [
            {"field": "区域", "operator": "eq", "value": "南区"},
            {"field": "时间", "operator": "relative", "value": "最近7天"},
        ],
        "metrics": {
            "itemCount": 4734,
            "weeklyChangePct": -10.88,
            "bidAmount": 40388380,
        },
        "widgets": [],
    }


def snapshot_tool_message(tool_call_id: str = "tool-1"):
    from ag_ui.core import ToolMessage

    return ToolMessage(
        id=f"tool-result-{tool_call_id}",
        content=json.dumps(snapshot_payload(), ensure_ascii=False),
        toolCallId=tool_call_id,
    )


def navigation_tool_message(
    tool_call_id: str = "tool-nav",
    *,
    destination: str = "datasets",
    path: str = "/datasets",
    context_version: int = 4,
):
    from ag_ui.core import ToolMessage

    return ToolMessage(
        id=f"tool-result-{tool_call_id}",
        content=json.dumps(
            {
                "schemaVersion": "davinci-ui-ack-v1",
                "status": "executed",
                "destination": destination,
                "path": path,
                "contextVersion": context_version,
            }
        ),
        toolCallId=tool_call_id,
    )


def make_runtime_request(
    tmp_path: Path,
    *,
    platform_session_id: str = "thread-1",
    workspace_snapshot: dict[str, Any] | None = None,
):
    from app.runtime.contracts import RuntimeRequest

    cwd = tmp_path / "workspace"
    claude_config_dir = tmp_path / "claude-config"
    memory_dir = tmp_path / "memory"
    cwd.mkdir()
    claude_config_dir.mkdir()
    memory_dir.mkdir()
    return RuntimeRequest(
        platform_session_id=platform_session_id,
        claude_session_id=None,
        cwd=cwd,
        claude_config_dir=claude_config_dir,
        memory_scope_key="user:workspace",
        memory_dir=memory_dir,
        text="解读当前仪表盘",
        attachments=(),
        file_references=(),
        workspace_snapshot=workspace_snapshot
        or {
            "model": "qwen3.8-max",
            "allowed_tools": ["Read"],
            "skills": [],
            "mcp_servers": {},
        },
    )
