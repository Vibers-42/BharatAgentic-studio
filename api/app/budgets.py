"""Spend limits for a project: a monthly limit (calendar month in India time) and a limit per conversation, in
rupees, counted from the usage table (every model call is logged there with its estimated cost). At 80% the
answer carries a warning; at 100% new questions are refused before any model is called (PRD FR-38, FR-42).
Local models have no API fee, so they never count against a limit. Costs are estimates, like everywhere else."""

from .db import get_conn

WARN_AT = 0.8


def _money(value) -> float | None:
    return None if value is None else round(float(value), 4)


def status(project_id: int, conversation_id: int | None = None) -> dict:
    """{"month": {"limit", "spent", "ratio"}, "conversation": {...} or None}. limit None = no limit."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT p.budget_month_inr, p.budget_conversation_inr,
                      (SELECT coalesce(sum(cost_inr), 0) FROM usage u WHERE u.project_id = p.id
                        AND u.created_at >= (date_trunc('month', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')),
                      (SELECT coalesce(sum(cost_inr), 0) FROM usage u WHERE %s::int IS NOT NULL AND u.conversation_id = %s)
               FROM projects p WHERE p.id = %s""", (conversation_id, conversation_id, project_id)).fetchone()
    if row is None:
        return {"month": {"limit": None, "spent": 0.0, "ratio": None}, "conversation_limit": None, "conversation": None}

    def part(limit, spent):
        limit, spent = _money(limit), _money(spent) or 0.0
        return {"limit": limit, "spent": spent, "ratio": (spent / limit) if limit else None}

    return {"month": part(row[0], row[2]), "conversation_limit": _money(row[1]),
            "conversation": part(row[1], row[3]) if conversation_id is not None else None}


def check(project_id: int, conversation_id: int | None = None) -> tuple[str | None, list[str]]:
    """(refusal message or None, warnings). A limit of 0 or less is treated as no limit."""
    s = status(project_id, conversation_id)
    refusal, warnings = None, []
    for key, what in (("month", "this project's monthly"), ("conversation", "this conversation's")):
        part = s.get(key)
        if not part or not part["limit"] or part["limit"] <= 0:
            continue
        if part["ratio"] >= 1:
            refusal = refusal or (f"Spend limit reached: {what} limit of ₹{part['limit']:g} is used up "
                                  f"(₹{part['spent']:.2f} spent). A project manager can raise it in Projects, or "
                                  "switch on Local only (local models have no API fee).")
        elif part["ratio"] >= WARN_AT:
            warnings.append(f"{round(part['ratio'] * 100)}% of {what} spend limit is used "
                            f"(₹{part['spent']:.2f} of ₹{part['limit']:g}).")
    return refusal, warnings
