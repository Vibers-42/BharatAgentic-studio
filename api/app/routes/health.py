"""Health and status endpoints used by Docker health checks and the web page.
/health only proves the api is up; /status also checks db, ollama and mcp-runner."""

import socket
import urllib.request
from urllib.parse import urlparse

from fastapi import APIRouter

from ..config import settings
from ..db import get_conn

router = APIRouter()


@router.get("/health")
def health():
    return {"ok": True}


def _pgvector_version() -> str | None:
    """Return the installed pgvector version, or None if the db or extension is missing."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _check_http(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


def _check_port(url: str) -> bool:
    parsed = urlparse(url)
    try:
        socket.create_connection((parsed.hostname, parsed.port), timeout=2).close()
        return True
    except OSError:
        return False


@router.get("/status")
def status():
    pgvector = _pgvector_version()
    return {
        "api": True,
        "db": pgvector is not None,
        "pgvector": pgvector,
        "ollama": _check_http(settings.ollama_url),
        "mcp_runner": _check_port(settings.mcp_url),
        "keys": {
            "gemini": bool(settings.gemini_api_key),
            "groq": bool(settings.groq_api_key),
        },
    }
