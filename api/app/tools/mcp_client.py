"""Talks to MCP tool servers with the official MCP Python SDK (streamable HTTP). Only servers named in
MCP_SERVERS can be reached; a graph cannot point the api at an arbitrary URL. The pipeline is synchronous,
so each call runs the SDK's async client in a short-lived event loop."""

import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ..config import settings

TIMEOUT_SECONDS = 15


class MCPError(RuntimeError):
    pass


def servers() -> dict[str, str]:
    """Allowed server names -> URLs. "orders-db" is the read-only server in the mcp-runner container."""
    return {"orders-db": settings.mcp_url}


def server_url(name: str) -> str:
    if name not in servers():
        raise MCPError(f"Unknown MCP server '{name}'. Allowed: {', '.join(servers())}.")
    return servers()[name]


async def _list(url: str) -> list[dict]:
    async with (httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as http,
                streamable_http_client(url, http_client=http) as (read, write, _)):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
    return [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema,
             "read_only": bool(t.annotations and t.annotations.readOnlyHint)} for t in tools]


async def _call(url: str, tool: str, args: dict) -> str:
    async with (httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as http,
                streamable_http_client(url, http_client=http) as (read, write, _)):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    if result.isError:
        raise MCPError("; ".join(c.text for c in result.content if hasattr(c, "text")) or "tool error")
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return "\n".join(c.text for c in result.content if hasattr(c, "text"))


def _run(coro):
    try:
        return asyncio.run(asyncio.wait_for(coro, TIMEOUT_SECONDS + 5))
    except MCPError:
        raise
    except Exception as exc:  # connection refused, timeout, protocol errors (often wrapped in a group)
        raise MCPError(f"MCP server unavailable: {type(exc).__name__}: {exc}") from exc


def list_tools(server: str) -> list[dict]:
    return _run(_list(server_url(server)))


def call_tool(server: str, tool: str, args: dict) -> str:
    return _run(_call(server_url(server), tool, args))
