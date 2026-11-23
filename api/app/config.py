"""Reads every setting from environment variables in one place.
Other files import `settings` instead of calling os.getenv themselves."""

import os
from dataclasses import dataclass
from urllib.parse import quote


def _database_url() -> str:
    """Use DATABASE_URL if given, else build it from the POSTGRES_* values in .env.
    quote() keeps passwords with symbols like @ or / from breaking the URL."""
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = quote(os.getenv("POSTGRES_USER", "bharat"), safe="")
    password = quote(os.getenv("POSTGRES_PASSWORD", ""), safe="")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    name = os.getenv("POSTGRES_DB", "bharatagent")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


@dataclass(frozen=True)
class Settings:
    database_url: str = _database_url()
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    mcp_url: str = os.getenv("MCP_URL", "http://localhost:8100/mcp")
    upload_dir: str = os.getenv("UPLOAD_DIR", "./uploads")

    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    ollama_chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:3b")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")

    github_token: str = os.getenv("GITHUB_TOKEN", "")
    usd_to_inr: float = float(os.getenv("USD_TO_INR", "84"))


settings = Settings()
