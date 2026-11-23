"""Output stage: packs the final result the chat window shows. If guardrails blocked the question,
the answer is the block reason. Citations, model badge and cost are added in Phases 3 and 4."""

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
    }
