"""Tool endpoint for the builder: which MCP servers the api may use and the read-only tools each offers
(listed live from the mcp-runner with the official MCP SDK), plus the calculator. Nothing is executed here."""

from fastapi import APIRouter

from ..tools import mcp_client

router = APIRouter(prefix="/tools")


@router.get("")
def list_tools():
    servers = []
    for name in mcp_client.servers():
        try:
            tools = mcp_client.list_tools(name)
            servers.append({"name": name, "ok": True, "error": None,
                            "tools": [{"name": t["name"], "description": t["description"], "read_only": t["read_only"]}
                                      for t in tools if t["name"] != "ping"]})
        except mcp_client.MCPError as exc:
            servers.append({"name": name, "ok": False, "error": str(exc), "tools": []})
    return {"calculator": {"operations": "+ - * / // % ** and round, abs, min, max, sqrt; 18% of 4500"},
            "mcp_servers": servers}
