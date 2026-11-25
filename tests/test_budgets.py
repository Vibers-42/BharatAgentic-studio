"""Spend limits: a warning from 80%, a refusal at 100%, no limit when unset or 0, for the month and per conversation."""

import pytest

from app import budgets


def fake_status(month_limit, month_spent, conv_limit=None, conv_spent=0.0, conversation=True):
    def part(limit, spent):
        return {"limit": limit, "spent": spent, "ratio": (spent / limit) if limit else None}
    return lambda project_id, conversation_id=None: {
        "month": part(month_limit, month_spent), "conversation_limit": conv_limit,
        "conversation": part(conv_limit, conv_spent) if conversation else None}


@pytest.mark.parametrize("limit,spent,refused,warned", [
    (None, 500.0, False, False),   # no limit
    (0, 5.0, False, False),        # 0 = no limit
    (10.0, 5.0, False, False),     # 50%
    (10.0, 8.0, False, True),      # 80%: warning
    (10.0, 9.99, False, True),
    (10.0, 10.0, True, False),     # 100%: refused
    (10.0, 12.5, True, False),
])
def test_monthly_limit(monkeypatch, limit, spent, refused, warned):
    monkeypatch.setattr(budgets, "status", fake_status(limit, spent, conversation=False))
    refusal, warnings = budgets.check(1)
    assert (refusal is not None) == refused and bool(warnings) == warned
    if refused:
        assert "Spend limit reached" in refusal and "monthly" in refusal and "Local only" in refusal


def test_conversation_limit_refuses_even_when_the_month_is_fine(monkeypatch):
    monkeypatch.setattr(budgets, "status", fake_status(100.0, 1.0, conv_limit=0.05, conv_spent=0.06))
    refusal, _ = budgets.check(1, conversation_id=7)
    assert refusal and "conversation" in refusal


def test_both_near_their_limits_give_two_warnings(monkeypatch):
    monkeypatch.setattr(budgets, "status", fake_status(10.0, 9.0, conv_limit=1.0, conv_spent=0.85))
    refusal, warnings = budgets.check(1, conversation_id=7)
    assert refusal is None and len(warnings) == 2 and warnings[0].startswith("90%")
