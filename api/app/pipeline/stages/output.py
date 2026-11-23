"""Output stage: packs the final result the chat window shows: answer, citations, model and token counts.
If guardrails blocked the question, the answer is the block reason. Cost is added in Phase 4."""

from ..context import RunContext
from ..graph import Node


def run(ctx: RunContext, node: Node) -> dict:
    return {
        "answer": ctx.block_reason if ctx.blocked else ctx.answer,
        "blocked": ctx.blocked,
        "language": ctx.language,
        "route": ctx.route or None,
        "sources": [n.id for n in ctx.sources],
        "tools": [n.id for n in ctx.tools],
        "citations": ctx.citations,
        "supported": ctx.supported,   # True only when every shown fact is backed by a cited chunk
        "grounded": ctx.grounded,     # False for direct answers that did not use any source
        "model": ctx.model or None,
        "usage": ctx.usage,
    }
