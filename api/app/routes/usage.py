"""Usage for the dashboard: totals, by user, by model, by project, by day, and the latest rows (model, tokens,
cost and time only; never question or answer text). Who sees what is decided from the session:
- without org_id: your own usage, across all your workspaces;
- with org_id: owners and admins see the whole workspace; members see only their own rows in it;
- project_id narrows to one project you can see (members again see only their own rows).
Filters: model (gemini, groq, local, local_small) and days. Costs are estimates from prices.json; rows whose
model has no published price are counted in "unpriced_requests" instead of as zero."""

from fastapi import APIRouter, Depends, Query

from ..access import require_org, require_project
from ..auth import User, current_user
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
MODEL = Query(None, pattern="^[a-z_]{1,32}$")
DAYS = Query(None, ge=1, le=366)


def _scope(user: User, org_id: int | None, project_id: int | None) -> tuple[list[str], list, bool]:
    """WHERE parts for what this user may see, and whether they see other people's rows."""
    where, args, everyone = [], [], False
    if project_id is not None:
        access = require_project(user, project_id)
        where.append("u.project_id = %s"); args.append(project_id)
        everyone = access.can_manage
    if org_id is not None:
        org = require_org(user, org_id)
        where.append("u.org_id = %s"); args.append(org_id)
        everyone = everyone or (project_id is None and org["role"] in ("owner", "admin"))
        if project_id is not None and org["role"] not in ("owner", "admin"):
            everyone = False
    if not everyone:
        where.append("u.user_id = %s"); args.append(user.id)
    return where, args, everyone


def _filters(user, org_id, project_id, model, days):
    where, args, everyone = _scope(user, org_id, project_id)
    if model:  # rows store "<key>:<model id>", e.g. "groq:openai/gpt-oss-120b"
        where.append("split_part(u.model, ':', 1) = %s"); args.append(model)
    if days:
        where.append("u.created_at >= now() - make_interval(days => %s)"); args.append(days)
    return where, args, everyone


def _query(group_sql, group_name, where, args) -> list[dict]:
    select_group = f"{group_sql} AS grp," if group_sql else ""
    group_by = f"GROUP BY {group_sql} ORDER BY {group_sql}" if group_sql else ""
    sql = (f"SELECT {select_group} {AGGREGATES} FROM usage u JOIN users us ON us.id = u.user_id "
           f"LEFT JOIN projects p ON p.id = u.project_id WHERE {' AND '.join(where) or 'true'} {group_by}")
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


@router.get("/totals")
def totals(org_id: int | None = None, project_id: int | None = None, model: str | None = MODEL,
           days: int | None = DAYS, user: User = Depends(current_user)):
    where, args, everyone = _filters(user, org_id, project_id, model, days)
    return {**_query(None, None, where, args)[0], "usd_to_inr": settings.usd_to_inr, "everyone": everyone,
            "note": "Estimated from prices.json, not a bill."}


@router.get("/by-user")
def by_user(org_id: int | None = None, project_id: int | None = None, model: str | None = MODEL,
            days: int | None = DAYS, user: User = Depends(current_user)):
    where, args, _ = _filters(user, org_id, project_id, model, days)
    return _query("coalesce(us.full_name, us.name)", "user", where, args)


@router.get("/by-model")
def by_model(org_id: int | None = None, project_id: int | None = None, days: int | None = DAYS,
             user: User = Depends(current_user)):
    where, args, _ = _filters(user, org_id, project_id, None, days)
    return _query("u.model", "model", where, args)


@router.get("/by-project")
def by_project(org_id: int | None = None, model: str | None = MODEL, days: int | None = DAYS,
               user: User = Depends(current_user)):
    where, args, _ = _filters(user, org_id, None, model, days)
    return _query("coalesce(p.name, 'No project')", "project", where, args)


@router.get("/by-day")
def by_day(org_id: int | None = None, project_id: int | None = None, model: str | None = MODEL,
           days: int = Query(30, ge=1, le=366), user: User = Depends(current_user)):
    # Days in India time: IST is a fixed UTC+5:30 (no daylight saving), so no time-zone database is needed.
    where, args, _ = _filters(user, org_id, project_id, model, days)
    return _query("((u.created_at AT TIME ZONE 'UTC') + interval '5 hours 30 minutes')::date", "day", where, args)


@router.get("/recent")
def recent(org_id: int | None = None, project_id: int | None = None, model: str | None = MODEL,
           days: int | None = DAYS, limit: int = Query(20, ge=1, le=200), user: User = Depends(current_user)):
    where, args, _ = _filters(user, org_id, project_id, model, days)
    sql = f"""SELECT u.id, coalesce(us.full_name, us.name), u.model, u.model_requested, u.status, u.error_code,
                     u.fallback_from, u.input_tokens, u.output_tokens, u.tokens_estimated, u.cost_usd, u.cost_inr,
                     u.response_ms, u.local_compute_ms, u.sources, u.language, u.created_at, p.name
              FROM usage u JOIN users us ON us.id = u.user_id LEFT JOIN projects p ON p.id = u.project_id
              WHERE {' AND '.join(where) or 'true'} ORDER BY u.id DESC LIMIT %s"""
    with get_conn() as conn:
        rows = conn.execute(sql, (*args, limit)).fetchall()
    keys = ["id", "user", "model", "model_requested", "status", "error_code", "fallback_from", "input_tokens",
            "output_tokens", "tokens_estimated", "cost_usd", "cost_inr", "response_ms", "local_compute_ms",
            "sources", "language", "created_at", "project"]
    out = []
    for r in rows:
        item = dict(zip(keys, r))
        item["cost_usd"] = float(item["cost_usd"]) if item["cost_usd"] is not None else None
        item["cost_inr"] = float(item["cost_inr"]) if item["cost_inr"] is not None else None
        item["created_at"] = item["created_at"].isoformat()
        out.append(item)
    return out
