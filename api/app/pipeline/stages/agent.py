"""Agent stage: retrieves chunks from the sources drawn into the agent (and only those), asks Gemini
to answer from them with [n] citations, and streams the answer as token events while it is written.
If nothing relevant is found, or the answer cites no real chunk, the agent says it does not know."""

import time
from collections.abc import Iterator

from ...config import settings
from ...models import gemini
from ...rag import citations, prompt
from ...rag.retrieve import retrieve
from ..context import RunContext
from ..graph import SOURCE_TYPES, TOOL_TYPES, Graph, Node


def _stream_answer(ctx: RunContext, node: Node, system: str, user: str) -> Iterator[dict]:
    """Forward each Gemini piece as a token event; keep the full text and token counts on ctx.
    "ms" is when the api received the piece (ms after the request was sent), so a client can compare
    its own arrival times with the model's pace and tell real streaming from buffering."""
    text = ""
    start = time.perf_counter()
    for piece in gemini.stream(system, user):
        if "text" in piece:
            text += piece["text"]
            yield {"type": "token", "stage": "agent", "node_id": node.id, "text": piece["text"],
                   "ms": round((time.perf_counter() - start) * 1000)}
        else:
            ctx.usage = piece["usage"]
    ctx.answer = text.strip()


def run(ctx: RunContext, node: Node, graph: Graph) -> Iterator[dict]:
    """A generator: yields token events, and returns the stage detail when finished."""
    connected = graph.connected_to(node.id)
    ctx.sources = [n for n in connected if n.type in SOURCE_TYPES]
    ctx.tools = [n for n in connected if n.type in TOOL_TYPES]
    ctx.model = f"gemini:{settings.gemini_model}"
    detail = {
        "model": ctx.model,
        "model_requested": node.config.get("model", "gemini"),  # other models arrive in Phase 4
        "sources": [{"id": n.id, "type": n.type} for n in ctx.sources],
        "tools": [{"id": n.id, "type": n.type} for n in ctx.tools],
    }
    if ctx.route == "tool":
        detail["note"] = "Tools are added in Phase 7; answering from the sources instead."

    # Direct answer: nothing to retrieve, no citations, and the reply is marked as not from sources.
    if ctx.route == "direct" or not ctx.sources:
        yield from _stream_answer(ctx, node, prompt.direct_system(ctx.language), ctx.question)
        ctx.grounded, ctx.supported, ctx.citations = False, False, []
        return {**detail, "mode": "direct", "usage": ctx.usage}

    # Search only the connected sources; an unconnected source is never passed to retrieve().
    relevant, candidates = retrieve(ctx.question, [n.id for n in ctx.sources])
    detail["retrieval"] = {
        "searched": [n.id for n in ctx.sources],
        "min_similarity": settings.min_similarity,
        "candidates": [{"source_id": c.source_id, "file": c.file_name, "chunk": c.chunk_index,
                        "similarity": round(c.similarity, 3)} for c in candidates],
        "relevant": len(relevant),
    }
    ctx.grounded = True
    if not relevant:
        ctx.answer, ctx.supported, ctx.citations = prompt.DONT_KNOW[ctx.language], False, []
        return {**detail, "mode": "rag", "model_called": False,
                "reason": "No chunk from the connected sources is relevant enough to answer."}

    yield from _stream_answer(ctx, node, prompt.rag_system(ctx.language), prompt.rag_prompt(ctx.question, relevant))
    checked = citations.check(ctx.answer, relevant)
    ctx.citations = checked["citations"]
    if citations.is_dont_know(ctx.answer) or not ctx.citations:
        # Either the model said it does not know, or it answered without citing a real chunk.
        ctx.answer, ctx.supported, ctx.citations = prompt.DONT_KNOW[ctx.language], False, []
        reason = ("The model found no answer in the sources." if citations.is_dont_know(checked["answer"])
                  else "The model's answer cited no real source chunk, so it was not shown.")
        return {**detail, "mode": "rag", "model_called": True, "usage": ctx.usage, "reason": reason,
                "invalid_markers": checked["invalid_markers"]}
    ctx.answer, ctx.supported = checked["answer"], True
    return {**detail, "mode": "rag", "model_called": True, "usage": ctx.usage,
            "invalid_markers": checked["invalid_markers"]}
