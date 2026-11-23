"""MCP server built with the official MCP Python SDK, served over HTTP at :8100/mcp.
Phase 1 exposes only a `ping` tool; the read-only orders tool is added in Phase 7."""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Binding to 0.0.0.0 turns off the SDK's default Host-header check, so we set it ourselves:
# only the api container (host "mcp-runner") and local health checks may call this server.
security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["mcp-runner:*", "localhost:*", "127.0.0.1:*"],
    allowed_origins=[],
)

mcp = FastMCP("bharat-tools", host="0.0.0.0", port=8100, transport_security=security)


@mcp.tool()
def ping() -> str:
    """Return 'pong' so the api can check the tool server is reachable."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
