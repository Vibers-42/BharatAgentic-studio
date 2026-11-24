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
    gemini_base_url: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com")
    ollama_chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "qwen3:14b")
    ollama_backup_model: str = os.getenv("OLLAMA_BACKUP_MODEL", "qwen2.5:3b")
    # Most tokens a local model may write for one reply. A small model can fall into a repetition loop;
    # without a cap one question once ran for 11 minutes (40,960 tokens).
    ollama_num_predict: int = int(os.getenv("OLLAMA_NUM_PREDICT", "1536"))
    # Privacy: true means no question or document text is ever sent to a cloud LLM API, for anyone.
    local_only: bool = os.getenv("LOCAL_ONLY", "false").lower() in ("1", "true", "yes")
    ollama_embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")

    # Retrieval: how many chunks reach the model, and the lowest cosine similarity that counts as relevant.
    top_k: int = int(os.getenv("RAG_TOP_K", "5"))
    min_similarity: float = float(os.getenv("RAG_MIN_SIMILARITY", "0.50"))
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "20"))

    github_token: str = os.getenv("GITHUB_TOKEN", "")
    usd_to_inr: float = float(os.getenv("USD_TO_INR", "84"))

    # Phase 8 accounts. COOKIE_SECURE must be true when the app is served over HTTPS (production); it stays
    # false for http://localhost, where browsers would otherwise drop the cookie.
    session_hours: int = int(os.getenv("SESSION_HOURS", "24"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
    invitation_days: int = int(os.getenv("INVITATION_DAYS", "7"))
    history_messages: int = int(os.getenv("CHAT_HISTORY_MESSAGES", "4"))  # earlier messages a follow-up sees


settings = Settings()
