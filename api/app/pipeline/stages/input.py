"""Input stage: cleans the question and decides the answer language.
Order: the chat's language selector, else the script the question is written in, else the agent's setting."""

from ..context import RunContext
from ..graph import LANGUAGES, Node

TELUGU = (0x0C00, 0x0C7F)
DEVANAGARI = (0x0900, 0x097F)  # Hindi script


def detect_language(text: str) -> str | None:
    """Return 'te' or 'hi' if the text is mostly in that script, else None (Latin or unknown)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return None
    te = sum(TELUGU[0] <= ord(ch) <= TELUGU[1] for ch in letters)
    hi = sum(DEVANAGARI[0] <= ord(ch) <= DEVANAGARI[1] for ch in letters)
    if max(te, hi) / len(letters) < 0.5:
        return None
    return "te" if te >= hi else "hi"


def run(ctx: RunContext, node: Node, agent: Node) -> dict:
    ctx.question = " ".join(ctx.question.split())  # collapse extra spaces and newlines
    detected = detect_language(ctx.question)

    if ctx.requested_language in LANGUAGES:
        ctx.language, chosen_by = ctx.requested_language, "chat selector"
    elif detected:
        ctx.language, chosen_by = detected, "question script"
    elif agent.config.get("language") in LANGUAGES:
        ctx.language, chosen_by = agent.config["language"], "agent setting"
    else:
        ctx.language, chosen_by = "en", "default"

    return {"language": ctx.language, "chosen_by": chosen_by, "detected": detected,
            "chars": len(ctx.question)}
