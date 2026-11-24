"""Writes one usage row for every question that reached a model, the same way for every provider:
who asked, which model was requested and which answered, tokens, estimated cost, speed, sources.
Failed answers are logged too (status "error"), so the dashboard shows them."""

from .db import get_conn


def log(*, user_id: int, model: str, model_requested: str, input_tokens: int, output_tokens: int,
        tokens_estimated: bool, cost_usd: float | None, cost_inr: float | None, response_ms: int,
        local_compute_ms: int, sources: list[str], language: str, status: str = "ok",
        error_code: str | None = None, fallback_from: str | None = None, org_id: int | None = None,
        project_id: int | None = None, graph_id: int | None = None, conversation_id: int | None = None) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """INSERT INTO usage (user_id, model, model_requested, input_tokens, output_tokens, tokens_estimated,
                                  cost_usd, cost_inr, response_ms, local_compute_ms, sources, language,
                                  status, error_code, fallback_from, org_id, project_id, graph_id, conversation_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (user_id, model, model_requested, input_tokens, output_tokens, tokens_estimated, cost_usd, cost_inr,
             response_ms, local_compute_ms, sources, language, status, error_code, fallback_from, org_id, project_id,
             graph_id, conversation_id),
        ).fetchone()
    return row[0]
