"""Runs the tool calls the agent planned, with the rules that keep tools safe:
- only a tool node drawn into the agent can run (the planner only sees those, and this checks again);
- an MCP tool must exist on its allowlisted server and be marked read-only, unless the node explicitly
  sets allow_write (no write tools exist on the bundled server);
- each call has its own result or error, so one failing tool does not stop the answer."""

import time
from dataclasses import dataclass

from ..pipeline.graph import Node
from . import calculator, mcp_client


@dataclass
class ToolCall:
    node_id: str
    tool: str          # "calculator", or the MCP tool's name
    args: dict


def _arg_types(schema: dict) -> dict:
    """A compact view of a tool's JSON schema for the planner prompt: {"order_id": "string"}. Keeping the
    prompt short matters on the local model, which reads every token of it before planning."""
    return {name: prop.get("type", "string") for name, prop in (schema.get("properties") or {}).items()}


def describe(nodes: list[Node]) -> list[dict]:
    """What each connected tool can do, for the planner prompt. MCP servers are asked for their tool list;
    an unreachable server is reported instead of failing the question."""
    out = []
    for node in nodes:
        if node.type == "tool_calculator":
            out.append({"node_id": node.id, "tool": "calculator", "args": {"expression": "string"},
                        "description": "Exact arithmetic on numbers, e.g. '1200 * 80%' or '(300 + 3500) / 2'."})
        elif node.type == "tool_mcp":
            server = node.config.get("server", "")
            try:
                tools = mcp_client.list_tools(server)
            except mcp_client.MCPError as exc:
                out.append({"node_id": node.id, "tool": None, "error": str(exc)})
                continue
            for t in tools:
                if t["read_only"] or node.config.get("allow_write") is True:
                    if t["name"] == "ping":
                        continue
                    out.append({"node_id": node.id, "tool": t["name"], "server": server,
                                "args": _arg_types(t["input_schema"]), "description": t["description"].split("\n")[0]})
    return out


def run(call: ToolCall, connected: dict[str, Node]) -> dict:
    """Execute one call. Returns {"node_id", "tool", "args", "ok", "output"|"error", "ms"}."""
    start = time.perf_counter()
    result = {"node_id": call.node_id, "tool": call.tool, "args": call.args}
    try:
        node = connected.get(call.node_id)
        if node is None:
            raise PermissionError(f"Tool node '{call.node_id}' is not connected to the agent, so it cannot run.")
        if node.type == "tool_calculator":
            if call.tool != "calculator":
                raise PermissionError(f"Node '{node.id}' is a calculator; it has no tool '{call.tool}'.")
            out = calculator.evaluate(str(call.args.get("expression", "")))
            result["output"] = f"{out['expression']} = {out['result']}"
        elif node.type == "tool_mcp":
            server = node.config.get("server", "")
            listed = {t["name"]: t for t in mcp_client.list_tools(server)}
            tool = listed.get(call.tool)
            if tool is None:
                raise PermissionError(f"Server '{server}' has no tool '{call.tool}'.")
            if not tool["read_only"] and node.config.get("allow_write") is not True:
                raise PermissionError(f"Tool '{call.tool}' is not read-only; this node only allows read-only tools.")
            result["output"] = mcp_client.call_tool(server, call.tool, call.args)
        else:
            raise PermissionError(f"Node '{node.id}' is not a tool.")
        result["ok"] = True
    except (calculator.CalculatorError, mcp_client.MCPError, PermissionError, TypeError, ValueError) as exc:
        result["ok"], result["error"] = False, str(exc)
    result["ms"] = round((time.perf_counter() - start) * 1000)
    return result
