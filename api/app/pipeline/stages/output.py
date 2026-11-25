"""Output stage: packs the final result the chat window shows: answer, citations, the model that
answered (and any fallback), token counts (actual or estimated), estimated cost with the same tokens
priced on every model, and local compute time. If guardrails blocked the question, the answer is the
block reason and no model was called."""

from ...rag.citations import MARKER
from ..context import RunContext
from ..graph import Node
from .input import detect_language

LANGUAGE_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi"}


def language_match(answer: str, language: str, roman: bool = False) -> bool:
    """Is the answer written in the script of the language it should be in? (English, and Telugu or Hindi typed in
    English letters = no Indic script.)"""
    found = detect_language(MARKER.sub("", answer))
    if roman:
        return found is None
    return found == language if language in ("te", "hi") else found is None


def run(ctx: RunContext, node: Node) -> dict:
    gen = ctx.generation
    # Small models sometimes answer a Telugu or Hindi question in English: say so on the answer.
    matches = language_match(ctx.answer, ctx.language, ctx.roman) if gen and not ctx.blocked else None
    if matches is False:
        ctx.warnings.append(f"The answer is not written in {LANGUAGE_NAMES[ctx.language]}{' (English letters)' if ctx.roman else ''}, the language of the "
                            f"question. Small local models often reply in English; try a larger model.")
    return {
        "answer": ctx.block_reason if ctx.blocked else ctx.answer,
        "blocked": ctx.blocked,
        "language": ctx.language,
        "roman": ctx.roman,           # True: Telugu/Hindi typed in English letters, answered the same way
        "language_match": matches,    # False: the model answered in another language (None: no model answer)
        "route": ctx.route or None,
        "sources": [n.id for n in ctx.sources],
        "tools": [n.id for n in ctx.tools],
        "citations": ctx.citations,
        "tool_calls": ctx.tool_calls,  # each tool call: node_id, tool, args, ok, output or error, ms
        "claims": ctx.claims,         # one verdict per sentence: supported / unsupported / uncited
        "supported": ctx.supported,   # True only when the shown answer cites real chunks
        "grounded": ctx.grounded,     # False for direct answers that did not use any source
        "model": gen["label"] if gen else None,
        "model_requested": ctx.model_key or None,
        "local_only": ctx.local_only,  # privacy: True means no cloud LLM API was allowed for this answer
        "fallback": gen["fallback"] if gen else None,
        "usage": ({"input_tokens": gen["input_tokens"], "output_tokens": gen["output_tokens"],
                   "estimated": gen["tokens_estimated"]} if gen else None),
        "response_ms": gen["response_ms"] if gen else None,
        "cost": ctx.cost,             # {"usd", "inr", "priced", "api_fee", "local_compute_ms"} or None
        "comparison": ctx.comparison,
        "model_note": ctx.model_note,
        "warnings": ctx.warnings,
    }
