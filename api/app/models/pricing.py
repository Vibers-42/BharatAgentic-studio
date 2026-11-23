"""Turns token counts into an estimated cost using prices.json (USD per million tokens), shown in INR
with USD_TO_INR from .env. A model with no published price gets no cost (never a made-up zero).
Local models cost nothing in API fees; their compute time is reported separately, not as money."""

import json
from functools import lru_cache
from pathlib import Path

from ..config import settings

PRICES_FILE = Path(__file__).parent / "prices.json"


@lru_cache
def table() -> dict:
    return json.loads(PRICES_FILE.read_text(encoding="utf-8"))


def price_of(model_id: str) -> dict | None:
    return table()["models"].get(model_id)


def cost(model_id: str, input_tokens: int, output_tokens: int, local: bool = False) -> dict:
    """{"usd", "inr", "priced", "api_fee"}; usd/inr are None when the model has no published price."""
    if local:
        return {"usd": 0.0, "inr": 0.0, "priced": True, "api_fee": False}
    p = price_of(model_id)
    if not p or p.get("input_per_million") is None or p.get("output_per_million") is None:
        return {"usd": None, "inr": None, "priced": False, "api_fee": True}
    usd = (input_tokens * p["input_per_million"] + output_tokens * p["output_per_million"]) / 1_000_000
    return {"usd": round(usd, 8), "inr": round(usd * settings.usd_to_inr, 6), "priced": True, "api_fee": True}


def comparison(models: list[tuple[str, str, bool]], input_tokens: int, output_tokens: int) -> list[dict]:
    """What the same tokens would cost on each model: [(key, model_id, is_local), ...] -> one row each."""
    rows = []
    for key, model_id, local in models:
        c = cost(model_id, input_tokens, output_tokens, local)
        label = (price_of(model_id) or {}).get("label", model_id)
        rows.append({"model": key, "model_id": model_id, "label": label, **c})
    return rows
