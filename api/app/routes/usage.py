"""Usage endpoints for the dashboard: totals, by user, by model, by day, and the latest rows.
Every endpoint takes ?user=<name> to show one person only, ?model=<key> (gemini, groq, local, local_small)
to show one model only, and ?days=N for the last N days only (all optional, combined with AND). Costs are estimates from prices.json;
rows whose model has no published price are counted in "unpriced_requests" instead of as zero."""

from fastapi import APIRouter, Query

from ..config import settings
from ..db import get_conn

router = APIRouter(prefix="/usage")

AGGREGATES = """
    count(*) AS requests,
    count(*) FILTER (WHERE u.status = 'error') AS errors,
    count(*) FILTER (WHERE u.fallback_from IS NOT NULL) AS fallbacks,
    coalesce(sum(u.input_tokens), 0) AS input_tokens,
    coalesce(sum(u.output_tokens), 0) AS output_tokens,
    count(*) FILTER (WHERE u.tokens_estimated) AS estimated_requests,
    coalesce(sum(u.cost_usd), 0) AS cost_usd,
    coalesce(sum(u.cost_inr), 0) AS cost_inr,
    count(*) FILTER (WHERE u.cost_usd IS NULL AND u.status = 'ok') AS unpriced_requests,
    coalesce(round(avg(u.response_ms)), 0) AS avg_response_ms,
    coalesce(sum(u.local_compute_ms), 0) AS local_compute_ms
"""
COLUMNS = ["requests", "errors", "fallbacks", "input_tokens", "output_tokens", "estimated_requests",
           "cost_usd", "cost_inr", "unpriced_requests", "avg_response_ms", "local_compute_ms"]


def _filters(user: str | None, model: str | None, days: int | None) -> tuple[list[str], list]:
    where, args = ["true"], []
    if user:
        where.append("us.name = %s")
        args.append(user)
    if model:  # rows store "<key>:<model id>", e.g. "groq:openai/gpt-oss-120b"
        where.append("split_part(u.model, ':', 1) = %s")
        args.append(model)
    if days:
        where.append("u.created_at >= now() - make_interval(days => %s)")
        args.append(days)
    return where, args


def _query(group_sql: str | None, group_name: str | None, user: str | None, model: str | None = None,
           days: int | None = None) -> list[dict]:
    where, args = _filters(user, model, days)
    select_group = f"{group_sql} AS grp," if group_sql else ""
    group_by = f"GROUP BY {group_sql} ORDER BY {group_sql}" if group_sql else ""
    sql = (f"SELECT {select_group} {AGGREGATES} FROM usage u JOIN users us ON us.id = u.user_id "
           f"WHERE {' AND '.join(where)} {group_by}")
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        values = r[1:] if group_sql else r
        item = {c: (float(v) if c.startswith("cost") else int(v)) for c, v in zip(COLUMNS, values)}
        if group_sql:
            g = r[0]
            item = {group_name: g.isoformat() if hasattr(g, "isoformat") else g, **item}
        out.append(item)
    return out


MODEL = Query(None, pattern="^[a-z_]{1,32}$")
DAYS = Query(None, ge=1, le=366)


@router.get("/totals")
def totals(user: str | None = None, model: str | None = MODEL, days: int | None = DAYS):
    return {**_query(None, None, user, model, days)[0], "usd_to_inr": settings.usd_to_inr,
            "note": "Estimated from prices.json, not a bill."}


@router.get("/by-user")
def by_user(model: str | None = MODEL, days: int | None = DAYS):
    return _query("us.name", "user", None, model, days)


@router.get("/by-model")
def by_model(user: str | None = None, days: int | None = DAYS):
    return _query("u.model", "model", user, None, days)


@router.get("/by-day")
def by_day(user: str | None = None, model: str | None = MODEL, days: int = Query(30, ge=1, le=366)):
    # Days in India time: IST is a fixed UTC+5:30 (no daylight saving), so no time-zone database is needed.
    return _query("((u.created_at AT TIME ZONE 'UTC') + interval '5 hours 30 minutes')::date", "day", user,
                  model, days)


@router.get("/recent")
def recent(user: str | None = None, model: str | None = MODEL, days: int | None = DAYS,
           limit: int = Query(20, ge=1, le=200)):
    where, args = _filters(user, model, days)
    sql = f"""SELECT u.id, us.name, u.model, u.model_requested, u.status, u.error_code, u.fallback_from,
                    u.input_tokens, u.output_tokens, u.tokens_estimated, u.cost_usd, u.cost_inr,
                    u.response_ms, u.local_compute_ms, u.sources, u.language, u.created_at
             FROM usage u JOIN users us ON us.id = u.user_id
             WHERE {' AND '.join(where)} ORDER BY u.id DESC LIMIT %s"""
    with get_conn() as conn:
        rows = conn.execute(sql, (*args, limit)).fetchall()
    keys = ["id", "user", "model", "model_requested", "status", "error_code", "fallback_from", "input_tokens",
            "output_tokens", "tokens_estimated", "cost_usd", "cost_inr", "response_ms", "local_compute_ms",
            "sources", "language", "created_at"]
    out = []
    for r in rows:
        item = dict(zip(keys, r))
        item["cost_usd"] = float(item["cost_usd"]) if item["cost_usd"] is not None else None
        item["cost_inr"] = float(item["cost_inr"]) if item["cost_inr"] is not None else None
        item["created_at"] = item["created_at"].isoformat()
        out.append(item)
    return out
