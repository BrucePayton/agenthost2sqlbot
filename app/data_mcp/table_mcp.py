"""Host-side connection to the company table MCP; credentials never enter the Runner."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import Settings
from app.errors import AppError

TABLE_TOOLS = frozenset({'table.search', 'table.describe', 'table.query'})


class TableMcpClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def require_subject(self, subject: str) -> None:
        s = self.settings
        if not (s.data_mcp_command and s.data_mcp_token_cache_dir and s.data_mcp_subject):
            raise AppError('table_mcp_not_configured', 'MCP 取数尚未配置，请配置服务端连接和登录账号。', 503)
        if subject != s.data_mcp_subject:
            raise AppError('table_mcp_forbidden', '当前用户未绑定 MCP 登录账号。', 403)
        directory = s.data_mcp_token_cache_dir
        if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
            raise AppError('table_mcp_cache_invalid', 'MCP 登录缓存目录不可用。', 503)

    async def _request(self, subject: str, name: str | None, arguments: dict | None = None):
        self.require_subject(subject)
        s = self.settings
        params = StdioServerParameters(command=s.data_mcp_command, args=s.data_mcp_args, env={
            'MCP_GATEWAY_URL': str(s.data_mcp_gateway_url).rstrip('/'),
            'MCP_ENV': 'prod',
            'MCP_TOKEN_CACHE_DIR': str(s.data_mcp_token_cache_dir),
        })
        try:
            async with asyncio.timeout(s.data_mcp_timeout_seconds):
                # Proxy stderr can contain authentication details; do not expose it to the model.
                with open(os.devnull, 'w') as errlog:  # noqa: ASYNC230 - OS null device; SDK requires a file descriptor.
                    async with stdio_client(params, errlog=errlog) as streams:
                        async with ClientSession(*streams) as session:
                            await session.initialize()
                            if name is None:
                                result = await session.list_tools()
                                tools = [t.model_dump(mode='json', exclude_none=True) for t in result.tools if t.name in TABLE_TOOLS]
                                if {t['name'] for t in tools} != TABLE_TOOLS:
                                    raise AppError('table_mcp_tools_missing', 'MCP 未返回全部三个取数工具。', 502)
                                return tools
                            result = await session.call_tool(name, arguments or {})
                            body = result.structuredContent
                            if body is None:
                                body = [item.model_dump(mode='json', exclude_none=True) for item in result.content]
                            return {'content': [{'type': 'text', 'text': json.dumps(body, ensure_ascii=False)}],
                                    'is_error': bool(result.isError)}
        except AppError:
            raise
        except Exception as exc:
            raise AppError('table_mcp_unavailable', 'MCP 连接失败或超时，请检查公司网络及 MCP 登录状态；未改用 SQLBot。', 503) from exc

    async def list_tools(self, subject: str) -> list[dict[str, Any]]:
        return await self._request(subject, None)

    async def call_tool(self, subject: str, name: str, arguments: dict) -> dict:
        if name not in TABLE_TOOLS:
            raise AppError('table_mcp_tool_forbidden', '不允许调用此 MCP 工具。', 403)
        return await self._request(subject, name, arguments)
