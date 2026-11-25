"""Runs a validated graph for one question: input, guardrails, router, agent, output, in order.
Yields an event before and after every stage so the frontend can light up that node, then a result.

Event shapes:
  {"type": "stage", "stage": "router", "node_id": "n3", "status": "running"}
  {"type": "stage", "stage": "router", "node_id": "n3", "status": "done", "ms": 1, "detail": {...}}
  status can also be "blocked" (guardrails stopped it) or "skipped" (a later stage after a block)
  {"type": "token", "stage": "agent", "node_id": "n4", "text": "...", "ms": 412}  (while the model is writing;
  ms = when the api received this piece from the model)
  {"type": "error", "stage": "agent", "node_id": "n4", "message": "...", "code": "quota_rate", "retry_after": 30}
  code is "all_providers_failed" (with "attempts"), "quota_daily" / "quota_rate", "safety", "model_error",
  or "stage_error"
  {"type": "fallback", "stage": "agent", "from": "gemini", "to": "local", "code": "...", "reason": "..."}
  {"type": "tool_call", "stage": "agent", "node_id": "t1", "tool": "calculator", "args": {...}}
  {"type": "tool_result", "stage": "agent", "node_id": "t1", "tool": "...", "ok": true, "output": "...", "ms": 3}
  {"type": "token_reset", "stage": "agent", ...}  (a model failed mid-answer: discard the streamed text)
  {"type": "result", ...output stage fields...}
"""

import inspect
import time
from collections.abc import Iterator

from .context import RunContext
from .graph import MAIN_LINE, Graph
from .stages import agent, guardrails, input, output, router


def run_pipeline(graph: Graph, question: str, language: str | None = None, model: str | None = None,
                 user_id: int | None = None, scope: str = "inline", local_only: bool = False,
                 source_map: dict | None = None, history: list | None = None, owner: dict | None = None,
                 previous_citations: list | None = None, can_approve: bool = False) -> Iterator[dict]:
    """The graph must already have passed validate_graph(). user_id None means usage is not logged.
    scope says whose document chunks the agent may search ("graph:<id>" for a saved graph)."""
    nodes = {t: graph.of_type(t)[0] for t in MAIN_LINE}
    ctx = RunContext(question=question, requested_language=language, requested_model=model, user_id=user_id,
                     scope=scope, local_only=local_only, source_map=source_map, history=history or [],
                     owner=owner or {}, previous_citations=previous_citations or [], can_approve=can_approve)

    steps = {
        "input": lambda: input.run(ctx, nodes["input"], nodes["agent"]),
        "guardrails": lambda: guardrails.run(ctx, nodes["guardrails"]),
        "router": lambda: router.run(ctx, nodes["router"], graph, nodes["agent"]),
        "agent": lambda: agent.run(ctx, nodes["agent"], graph),
        "output": lambda: output.run(ctx, nodes["output"]),
    }

    for stage in MAIN_LINE:
        node_id = nodes[stage].id
        # Router and agent never see a blocked question; output still runs to show the reason.
        if ctx.blocked and stage in ("router", "agent"):
            yield {"type": "stage", "stage": stage, "node_id": node_id, "status": "skipped"}
            continue

        yield {"type": "stage", "stage": stage, "node_id": node_id, "status": "running"}
        start = time.perf_counter()
        try:
            detail = steps[stage]()
            if inspect.isgenerator(detail):
                # A streaming stage (the agent) yields token events and returns its detail at the end.
                detail = yield from detail
        except Exception as exc:  # report which stage broke instead of a bare 500
            error = {"type": "error", "stage": stage, "node_id": node_id, "message": str(exc),
                     "code": getattr(exc, "code", "stage_error")}  # e.g. quota_daily, quota_rate
            if getattr(exc, "retry_after", None) is not None:
                error["retry_after"] = exc.retry_after
            if getattr(exc, "attempts", None):
                error["attempts"] = exc.attempts  # every model tried, and why each failed
            yield error
            return
        ms = round((time.perf_counter() - start) * 1000)
        status = "blocked" if stage == "guardrails" and ctx.blocked else "done"
        yield {"type": "stage", "stage": stage, "node_id": node_id, "status": status, "ms": ms,
               "detail": detail}

    # The output stage's detail is the final answer package.
    yield {"type": "result", **detail}
