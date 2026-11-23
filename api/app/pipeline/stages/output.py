"""Output stage: packs the final result the chat window shows: answer, citations, the model that
answered (and any fallback), token counts (actual or estimated), estimated cost with the same tokens
priced on every model, and local compute time. If guardrails blocked the question, the answer is the
block reason and no model was called."""

from ..context import RunContext
from ..graph import Node


def run(ctx: RunContext, node: Node) -> dict:
    gen = ctx.generation
    return {
        "answer": ctx.block_reason if ctx.blocked else ctx.answer,
        "blocked": ctx.blocked,
        "language": ctx.language,
        "route": ctx.route or None,
        "sources": [n.id for n in ctx.sources],
        "tools": [n.id for n in ctx.tools],
        "citations": ctx.citations,
        "supported": ctx.supported,   # True only when the shown answer cites real chunks
        "grounded": ctx.grounded,     # False for direct answers that did not use any source
        "model": gen["label"] if gen else None,
        "model_requested": ctx.model_key or None,
        "fallback": gen["fallback"] if gen else None,
        "usage": ({"input_tokens": gen["input_tokens"], "output_tokens": gen["output_tokens"],
                   "estimated": gen["tokens_estimated"]} if gen else None),
        "response_ms": gen["response_ms"] if gen else None,
        "cost": ctx.cost,             # {"usd", "inr", "priced", "api_fee", "local_compute_ms"} or None
        "comparison": ctx.comparison,
        "model_note": ctx.model_note,
        "warnings": ctx.warnings,
    }
