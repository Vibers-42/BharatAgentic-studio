"""MCP server built with the official MCP Python SDK, served over HTTP at :8100/mcp, in its own container on
a network with no route to the database. It exposes one data source ("orders-db"): fictional clinic orders from
orders.json, loaded once at start. The lookup tools are annotated readOnlyHint=True. cancel_order is the one tool
that changes data (readOnlyHint=False, destructiveHint=True): the api only offers it to an agent whose MCP block
allows actions, and even then it runs only after the person who asked clicks Approve (tools/approvals.py).
Changes live in memory and reset when the container restarts, so the demo data never drifts for good."""

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

# Binding to 0.0.0.0 turns off the SDK's default Host-header check, so we set it ourselves:
# only the api container (host "mcp-runner") and local health checks may call this server.
security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["mcp-runner:*", "localhost:*", "127.0.0.1:*"],
    allowed_origins=[],
)

mcp = FastMCP("orders-db", host="0.0.0.0", port=8100, transport_security=security)

DATA = json.loads((Path(__file__).parent / "orders.json").read_text(encoding="utf-8"))
ORDERS = {o["order_id"]: o for o in DATA["orders"]}
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CHANGES_DATA = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


@mcp.tool(annotations=READ_ONLY)
def ping() -> str:
    """Return 'pong' so the api can check the tool server is reachable."""
    return "pong"


@mcp.tool(annotations=READ_ONLY)
def get_order(order_id: str) -> dict:
    """Look up one order by its id (for example ORD-1002): items, amount, payment, status and refund."""
    order = ORDERS.get(order_id.strip().upper())
    if order is None:
        return {"found": False, "order_id": order_id, "known_ids": sorted(ORDERS)}
    return {"found": True, **order}


@mcp.tool(annotations=READ_ONLY)
def find_orders(status_contains: str = "", patient: str = "", limit: int = 10) -> dict:
    """List orders whose status contains a word (for example 'cancelled' or 'returned') and/or of one patient."""
    limit = max(1, min(int(limit), 20))
    rows = [o for o in DATA["orders"]
            if status_contains.lower() in o["status"].lower() and patient.lower() in o["patient"].lower()]
    return {"count": len(rows), "orders": rows[:limit]}


@mcp.tool(annotations=READ_ONLY)
def order_totals() -> dict:
    """Totals over all orders: number of orders, amount billed, and refunds approved or paid."""
    refunded = [o for o in DATA["orders"] if o.get("refund")]
    return {"orders": len(DATA["orders"]), "amount_billed": sum(o["amount"] for o in DATA["orders"]),
            "refunds": len(refunded), "refund_amount": sum(o["refund"] for o in refunded)}


@mcp.tool(annotations=CHANGES_DATA)
def cancel_order(order_id: str, reason: str = "") -> dict:
    """Cancel one order by its id (for example ORD-1003). Changes data: only runs after the user approves."""
    order = ORDERS.get(order_id.strip().upper())
    if order is None:
        return {"changed": False, "order_id": order_id, "error": "No such order.", "known_ids": sorted(ORDERS)}
    if order["status"].startswith("cancelled"):
        return {"changed": False, "order_id": order["order_id"], "status": order["status"],
                "note": "The order was already cancelled; nothing changed."}
    previous = order["status"]
    order["status"] = "cancelled on request"
    if reason:
        order["cancel_reason"] = reason[:200]
    return {"changed": True, "order_id": order["order_id"], "previous_status": previous, "status": order["status"],
            "note": "Any refund follows the clinic's refund policy."}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
