"""GET /models: the choices for the model dropdown, with whether each one can run right now and its
price from prices.json. The local model is "ready" only if it is pulled into the ollama container."""

import httpx
from fastapi import APIRouter

from ..config import settings
from ..models import pricing
from ..models.generate import LANGUAGE_NOTES, LOCAL, PROVIDERS

router = APIRouter()


def _local_pulled(model_id: str) -> bool:
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=3).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return False
    names = {m.get("name") for m in tags}
    return model_id in names or f"{model_id}:latest" in names


@router.get("/models")
def list_models():
    out = []
    for key, provider in PROVIDERS.items():
        model_id = provider.model_id()
        price = pricing.price_of(model_id) or {}
        local = key == LOCAL
        ready = _local_pulled(model_id) if local else provider.configured()
        out.append({
            "key": key,
            "provider": provider.PROVIDER,
            "model_id": model_id,
            "label": price.get("label", model_id),
            "local": local,
            "ready": ready,
            "why_not_ready": None if ready else (
                f"run: docker compose exec ollama ollama pull {model_id}" if local
                else f"set {key.upper()}_API_KEY in .env"),
            "input_per_million_usd": 0 if local else price.get("input_per_million"),
            "output_per_million_usd": 0 if local else price.get("output_per_million"),
            "priced": local or price.get("input_per_million") is not None,
            "price_status": price.get("status"),
            "note": LANGUAGE_NOTES.get(key),
        })
    return {"models": out, "prices_checked_on": pricing.table()["checked_on"],
            "usd_to_inr": settings.usd_to_inr, "fallback": LOCAL,
            "note": pricing.table()["note"]}
