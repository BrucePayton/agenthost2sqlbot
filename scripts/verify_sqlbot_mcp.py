"""Verify SQLBot's legacy SSE MCP endpoint without printing credentials."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client


def _payload(result: Any) -> Any:
    for item in result.content:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        return value.get("data") if isinstance(value, dict) and "data" in value else value
    raise RuntimeError("SQLBot MCP returned no JSON payload")


async def verify(url: str, username: str, password: str) -> None:
    async with sse_client(url) as streams, ClientSession(*streams) as session:
        initialized = await session.initialize()
        tools = await session.list_tools()
        started = _payload(
            await session.call_tool(
                "mcp_start",
                {"username": username, "password": password},
            )
        )
        token = started["access_token"]
        workspaces = _payload(
            await session.call_tool("mcp_ws_list", {"token": token})
        )
        datasources = _payload(
            await session.call_tool("mcp_datasource_list", {"token": token})
        )
        print(
            json.dumps(
                {
                    "server": initialized.serverInfo.name,
                    "protocol": initialized.protocolVersion,
                    "tools": [tool.name for tool in tools.tools],
                    "chat_created": isinstance(started.get("chat_id"), int),
                    "workspace_count": len(workspaces),
                    "workspace_names": [item.get("name") for item in workspaces],
                    "datasource_count": len(datasources),
                    "datasource_names": [item.get("name") for item in datasources],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url", default=os.environ.get("SQLBOT_MCP_URL", "http://127.0.0.1:8001/mcp")
    )
    parser.add_argument("--username", default=os.environ.get("SQLBOT_USERNAME", "admin"))
    args = parser.parse_args()
    password = os.environ.get("SQLBOT_PASSWORD")
    if not password:
        raise SystemExit("SQLBOT_PASSWORD is required")
    asyncio.run(verify(args.url, args.username, password))


if __name__ == "__main__":
    main()
