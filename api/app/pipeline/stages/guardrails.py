"""Guardrails stage: blocks empty, too long, abusive or off-topic input before it reaches a model,
and records the reason so the output can show it. Which checks run comes from the node's "block" list."""

import re

from ..context import RunContext
from ..graph import Node
from .guardrails_words import ABUSE, OFF_TOPIC, OFF_TOPIC_PHRASES

DEFAULT_CHECKS = ["empty", "abuse", "off_topic"]
MAX_CHARS = 2000
MIN_LETTERS = 2

# \w matches letters in every script, but Indic vowel signs are "marks", so include ऀ-౿ too.
WORD = re.compile(r"[\wऀ-౿]+")


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def check(question: str, checks: list[str]) -> tuple[str, str] | None:
    """Return (rule, reason) for the first rule the question breaks, or None if it is allowed."""
    if len(question) > MAX_CHARS:
        return "too_long", f"The question is longer than {MAX_CHARS} characters. Please shorten it."
    if "empty" in checks and sum(ch.isalnum() for ch in question) < MIN_LETTERS:
        return "empty", "The question is empty. Please type a question."

    words = _words(question)
    if "abuse" in checks and words & ABUSE:
        return "abuse", "The question contains abusive language, so it was not sent to the model."
    if "off_topic" in checks:
        text = question.lower()
        if words & OFF_TOPIC or any(p in text for p in OFF_TOPIC_PHRASES):
            return "off_topic", ("This assistant answers questions about your connected documents, "
                                 "repos and tools. That request is outside its scope.")
    return None


def run(ctx: RunContext, node: Node) -> dict:
    checks = node.config.get("block", DEFAULT_CHECKS)
    hit = check(ctx.question, checks)
    if hit:
        ctx.blocked = True
        ctx.block_reason = hit[1]
        return {"checks": checks, "allowed": False, "rule": hit[0], "reason": hit[1]}
    return {"checks": checks, "allowed": True}
