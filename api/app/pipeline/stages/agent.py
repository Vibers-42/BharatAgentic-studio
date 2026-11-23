"""Agent stage: collects the sources and tools drawn into the agent and writes the answer.
Phase 2 has no model yet, so it returns a clearly labelled placeholder; Phase 3 adds retrieval
with citations and Phase 4 the real model call through generate()."""

from ..context import RunContext
from ..graph import SOURCE_TYPES, TOOL_TYPES, Graph, Node


def run(ctx: RunContext, node: Node, graph: Graph) -> dict:
    connected = graph.connected_to(node.id)
    ctx.sources = [n for n in connected if n.type in SOURCE_TYPES]
    ctx.tools = [n for n in connected if n.type in TOOL_TYPES]
    model = node.config.get("model", "gemini")

    ctx.answer = (
        f"[Placeholder, no model called] Route: {ctx.route}. Language: {ctx.language}. "
        f"Connected sources: {', '.join(n.id for n in ctx.sources) or 'none'}. "
        f"Connected tools: {', '.join(n.id for n in ctx.tools) or 'none'}. "
        "Real answers with citations arrive in Phase 3."
    )
    return {
        "model": model,
        "simulated": True,
        "sources": [{"id": n.id, "type": n.type} for n in ctx.sources],
        "tools": [{"id": n.id, "type": n.type} for n in ctx.tools],
    }
