"""Agent stage: retrieves chunks from the sources drawn into the agent (and only those), asks the chosen
model (through generate(), with retry and local fallback) to answer from them with [n] citations, and
streams the answer as token events while it is written. Every model answer gets its token counts and
estimated cost, and is logged in the usage table. Each cited sentence is then checked against the chunk
it cites (numbers and meaning) and dropped if unsupported. If nothing relevant is found, or nothing
supported is left, the agent says it does not know."""

import time
from collections.abc import Iterator

from ... import usage
from ...config import settings
from ...models import generate, pricing
from ...models.generate import LANGUAGE_NOTES, AllProvidersFailed, is_local
from ...rag import citations, claims, prompt
from ...rag.retrieve import retrieve
from ..context import RunContext
from ..graph import SOURCE_TYPES, TOOL_TYPES, Graph, Node


def _log(ctx: RunContext, **row) -> None:
    """Write the usage row; a logging failure is reported on the answer but never loses the answer."""
    if ctx.user_id is None:
        return
    try:
        usage.log(user_id=ctx.user_id, sources=[n.id for n in ctx.sources], language=ctx.language, **row)
    except Exception as exc:
        ctx.warnings.append(f"Usage was not logged: {exc}")


def _account(ctx: RunContext, gen: generate.Generation) -> None:
    """Token counts, cost of this answer, the same tokens priced on every model, and the usage row."""
    local = is_local(gen.model)
    ctx.generation = gen.to_dict()
    ctx.cost = pricing.cost(gen.model_id, gen.input_tokens, gen.output_tokens, local)
    ctx.cost["local_compute_ms"] = gen.response_ms if local else 0
    ctx.comparison = pricing.comparison([(k, m, is_local(k)) for k, m in generate.model_ids().items()],
                                        gen.input_tokens, gen.output_tokens)
    note = LANGUAGE_NOTES.get(gen.model)
    ctx.model_note = note if note and ctx.language in ("te", "hi") else None
    _log(ctx, model=gen.label, model_requested=ctx.model_key, input_tokens=gen.input_tokens,
         output_tokens=gen.output_tokens, tokens_estimated=gen.tokens_estimated, cost_usd=ctx.cost["usd"],
         cost_inr=ctx.cost["inr"], response_ms=gen.response_ms, local_compute_ms=ctx.cost["local_compute_ms"],
         fallback_from=gen.fallback["from"] if gen.fallback else None)


def _stream_answer(ctx: RunContext, node: Node, system: str, user: str) -> Iterator[dict]:
    """Forward the model's pieces as token events, plus token_reset / fallback events, and account for
    the answer. "ms" is when the api received the piece (ms after the first request was sent), so a
    client can compare its own arrival times with the model's pace and tell streaming from buffering."""
    start = time.perf_counter()
    text = ""
    try:
        for event in generate.stream(ctx.model_key, system, user, ctx.local_only):
            if "text" in event:
                text += event["text"]
                yield {"type": "token", "stage": "agent", "node_id": node.id, "text": event["text"],
                       "ms": round((time.perf_counter() - start) * 1000)}
            elif "reset" in event:
                text = ""  # the model failed after writing; its partial text must not be shown
                yield {"type": "token_reset", "stage": "agent", "node_id": node.id, **event["reset"]}
            elif "fallback" in event:
                yield {"type": "fallback", "stage": "agent", "node_id": node.id, **event["fallback"]}
            else:
                gen = event["done"]
    except AllProvidersFailed as exc:
        _log(ctx, model=f"{ctx.model_key}:{generate.model_ids()[ctx.model_key]}", model_requested=ctx.model_key,
             input_tokens=0, output_tokens=0, tokens_estimated=False, cost_usd=0, cost_inr=0,
             response_ms=round((time.perf_counter() - start) * 1000), local_compute_ms=0, status="error",
             error_code=exc.code, fallback_from=ctx.model_key if len(exc.attempts) > 1 else None)
        raise
    ctx.answer = gen.text
    _account(ctx, gen)


def run(ctx: RunContext, node: Node, graph: Graph) -> Iterator[dict]:
    """A generator: yields token events, and returns the stage detail when finished."""
    connected = graph.connected_to(node.id)
    ctx.sources = [n for n in connected if n.type in SOURCE_TYPES]
    ctx.tools = [n for n in connected if n.type in TOOL_TYPES]
    # The chat's model dropdown wins over the agent node's setting. Local-only can come from the request,
    # the agent node, or the server (LOCAL_ONLY in .env); any of them is enough.
    ctx.model_key = ctx.requested_model or node.config.get("model", "gemini")
    ctx.local_only = ctx.local_only or node.config.get("local_only") is True or settings.local_only
    detail = {
        "model_requested": ctx.model_key,
        "local_only": ctx.local_only,
        "sources": [{"id": n.id, "type": n.type} for n in ctx.sources],
        "tools": [{"id": n.id, "type": n.type} for n in ctx.tools],
    }
    if ctx.route == "tool":
        detail["note"] = "Tools are added in Phase 7; answering from the sources instead."

    def finish(**extra) -> dict:
        return {**detail, **extra, "generation": ctx.generation, "cost": ctx.cost}

    # Direct answer: nothing to retrieve, no citations, and the reply is marked as not from sources.
    if ctx.route == "direct" or not ctx.sources:
        yield from _stream_answer(ctx, node, prompt.direct_system(ctx.language), ctx.question)
        ctx.grounded, ctx.supported, ctx.citations = False, False, []
        return finish(mode="direct", model_called=True)

    # Search only the connected sources; an unconnected source is never passed to retrieve().
    relevant, candidates = retrieve(ctx.question, [n.id for n in ctx.sources], ctx.scope)
    detail["retrieval"] = {
        "scope": ctx.scope,
        "searched": [n.id for n in ctx.sources],
        "min_similarity": settings.min_similarity,
        "candidates": [{"source_id": c.source_id, "file": c.file_name, "chunk": c.chunk_index,
                        "similarity": round(c.similarity, 3)} for c in candidates],
        "relevant": len(relevant),
    }
    ctx.grounded = True
    if not relevant:
        ctx.answer, ctx.supported, ctx.citations = prompt.DONT_KNOW[ctx.language], False, []
        return finish(mode="rag", model_called=False,
                      reason="No chunk from the connected sources is relevant enough to answer.")

    yield from _stream_answer(ctx, node, prompt.rag_system(ctx.language), prompt.rag_prompt(ctx.question, relevant))
    checked = citations.check(ctx.answer, relevant)
    ctx.citations = checked["citations"]
    if citations.is_dont_know(ctx.answer) or not ctx.citations:
        # Either the model said it does not know, or it answered without citing a real chunk.
        ctx.answer, ctx.supported, ctx.citations = prompt.DONT_KNOW[ctx.language], False, []
        reason = ("The model found no answer in the sources." if citations.is_dont_know(checked["answer"])
                  else "The model's answer cited no real source chunk, so it was not shown.")
        return finish(mode="rag", model_called=True, reason=reason, invalid_markers=checked["invalid_markers"])
    # Each cited sentence must be backed by the chunk it cites (numbers and meaning); others are removed.
    try:
        verified = claims.verify(checked["answer"], relevant)
    except Exception as exc:  # e.g. Ollama down: keep the cited answer, but say it was not checked
        ctx.warnings.append(f"Claims were not checked against the sources: {exc}")
        verified = {"answer": checked["answer"], "claims": [], "removed": 0, "checked": False}
    ctx.claims = verified["claims"]
    if not verified["answer"]:
        ctx.answer, ctx.supported, ctx.citations = prompt.DONT_KNOW[ctx.language], False, []
        return finish(mode="rag", model_called=True, claims=ctx.claims,
                      reason="No sentence of the model's answer was backed by the source it cited.")
    rechecked = citations.check(verified["answer"], relevant)  # keep only citations still used
    ctx.answer, ctx.citations, ctx.supported = rechecked["answer"], rechecked["citations"], bool(rechecked["citations"])
    return finish(mode="rag", model_called=True, invalid_markers=checked["invalid_markers"], claims=ctx.claims,
                  claims_removed=verified["removed"])
