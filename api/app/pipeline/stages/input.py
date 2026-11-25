"""Input stage: cleans the question and decides the answer language.
Order: the chat's language selector, else a language the question asks for ("explain it in Telugu"), else the
script the question is written in, else the agent's setting."""

import re

from ...rag.conversation import requested_language
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


# Telugu and Hindi typed in English letters ("bharatagent gurinchi cheppu", "refund kitna milega"), as most people
# type on a phone. Common function words only; two of them (or one of the unmistakable ones) count.
ROMAN_WORDS = {
    "te": {"cheppu", "cheppandi", "gurinchi", "enti", "emiti", "ela", "ekkada", "eppudu", "enta", "entha",
           "kavali", "unnayi", "ledu", "naaku", "naku", "meeru", "nenu", "chesthe", "cheyali", "vastundi", "untundi",
           "emi", "evaru", "inka", "mari", "andi"},
    "hi": {"kya", "hai", "hain", "kaise", "kaun", "batao", "bataiye", "kitna", "kitne", "kab", "kahan", "mujhe",
           "mera", "meri", "nahi", "kyun", "kyon", "milega", "chahiye", "aap", "tum", "ke", "ki", "ka", "ko", "se",
           "mein", "liye", "hoga"},
}
ROMAN_STRONG = {"te": {"cheppu", "cheppandi", "gurinchi", "emiti", "vastundi"},
                "hi": {"batao", "bataiye", "kitna", "milega", "chahiye", "kaise"}}


def romanized_language(text: str) -> str | None:
    """'te' or 'hi' for Telugu or Hindi written in Latin letters, else None."""
    words = re.findall(r"[a-z]+", text.lower())
    if not words:
        return None
    best, best_hits = None, 0
    for lang, vocab in ROMAN_WORDS.items():
        hits = sum(w in vocab for w in words)
        strong = any(w in ROMAN_STRONG[lang] for w in words)
        if (hits >= 2 or strong) and hits > best_hits:
            best, best_hits = lang, hits
    return best


def run(ctx: RunContext, node: Node, agent: Node) -> dict:
    ctx.question = " ".join(ctx.question.split())  # collapse extra spaces and newlines
    detected = detect_language(ctx.question)
    romanized = None if detected else romanized_language(ctx.question)

    asked = requested_language(ctx.question)  # "explain it in Telugu", "हिंदी में बताओ"
    if ctx.requested_language in LANGUAGES:
        ctx.language, chosen_by = ctx.requested_language, "chat selector"
    elif asked in LANGUAGES:
        ctx.language, chosen_by = asked, "asked in the question"
    elif detected:
        ctx.language, chosen_by = detected, "question script"
    elif romanized:
        ctx.language, chosen_by = romanized, "Telugu or Hindi typed in English letters"
        ctx.roman = True  # answer in English letters too, the way the question was typed
    elif agent.config.get("language") in LANGUAGES:
        ctx.language, chosen_by = agent.config["language"], "agent setting"
    else:
        ctx.language, chosen_by = "en", "default"

    return {"language": ctx.language, "chosen_by": chosen_by, "detected": detected or romanized, "roman": ctx.roman,
            "chars": len(ctx.question)}
