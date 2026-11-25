"""Router stage: decides whether the agent should search the sources, call a tool, or answer directly.
Simple, explainable rules based on what is drawn into the agent and what the question looks like."""

import re

from ..context import RunContext
from ..graph import SOURCE_TYPES, TOOL_TYPES, Graph, Node

# "1200 * 0.18", "450+30", "18% of 1200". Minus and slash need spaces around them,
# so dates like 2024-11-23 or 12/05 are not mistaken for sums.
ARITHMETIC = re.compile(r"\d\s*[+*x×÷]\s*\d|\d\s+[-/]\s+\d|\d\s*%\s*of\s*\d")
MATH_WORDS = {"calculate", "sum", "total", "percent", "percentage", "multiply", "divide",
              "जोड़", "गुणा", "प्रतिशत", "కూడిక", "గుణకారం", "శాతం"}
GREETINGS = {"hi", "hello", "hey", "namaste", "namaskar", "namaskaram", "thanks", "thank you", "dhanyavad",
             "नमस्ते", "धन्यवाद", "నమస్కారం", "ధన్యవాదాలు"}
WORD = re.compile(r"[\wऀ-౿]+")


# "My lab test cost Rs. 1200 ... how much will I get back?": a number plus a how-much / total question.
AMOUNT_QUESTION = ("how much", "how many rupees", "what amount", "get back", "total", "కి ఎంత", "कितना", "कितने")
ORDER_WORDS = re.compile(r"\bord-\d+|\border(s)?\b|ఆర్డర్|ऑर्डर", re.I)


# Questions about the assistant itself. "Who are you" style questions are answered from the built-in description
# at once; a question that only names BharatAgent still searches the sources first (someone may have connected
# BharatAgent's own docs) and falls back to the description when they hold nothing about it.
ABOUT_YOU = re.compile(
    r"\bwho are you\b|\bwhat are you\b|\bwhat can you do\b|\babout (?:you|yourself)\b|\bintroduce yourself\b|"
    r"\b(?:nuvvu|meeru) evaru\b|\b(?:tum|aap) kaun\b|నువ్వు ఎవరు|మీరు ఎవరు|तुम कौन|आप कौन|"
    r"నువ్వు ఏమి చేయగలవు|तुम क्या कर सकते", re.I)
APP_NAME = re.compile(r"bharat\s*agent|భారత్\s*ఏజెంట్|भारत\s*एजेंट", re.I)


ABOUT_YOU_MAX_WORDS = 6  # "who are you?", "what can you do", "nuvvu evaru": short; longer ones ask about something else


def about_app(question: str) -> str | None:
    """'you' when the question asks who or what the assistant is, 'name' when it names BharatAgent, else None."""
    if ABOUT_YOU.search(question) and len(question.split()) <= ABOUT_YOU_MAX_WORDS:
        return "you"
    if APP_NAME.search(question):
        return "name"
    return None


def looks_like_math(question: str) -> bool:
    text = question.lower()
    if ARITHMETIC.search(text) or set(WORD.findall(text)) & MATH_WORDS:
        return True
    return bool(re.search(r"\d", text)) and any(p in text for p in AMOUNT_QUESTION)


def mentions_orders(question: str) -> bool:
    return bool(ORDER_WORDS.search(question))


def is_greeting(question: str) -> bool:
    return question.lower().strip(" !.?।") in GREETINGS


def run(ctx: RunContext, node: Node, graph: Graph, agent: Node) -> dict:
    connected = graph.connected_to(agent.id)
    sources = [n.id for n in connected if n.type in SOURCE_TYPES]
    tools = [n.id for n in connected if n.type in TOOL_TYPES]
    calculator = any(n.type == "tool_calculator" for n in connected)
    mcp = any(n.type == "tool_mcp" for n in connected)

    about = about_app(ctx.question)
    ctx.about_app = about is not None
    if is_greeting(ctx.question):
        ctx.route, why = "direct", "greeting or thanks"
    elif about == "you" or (about == "name" and not sources):
        ctx.route, why = "direct", "the question is about BharatAgent Studio itself"
    elif calculator and looks_like_math(ctx.question):
        ctx.route, why = "tool", "the question needs a calculation and a calculator is connected"
    elif mcp and mentions_orders(ctx.question):
        ctx.route, why = "tool", "the question is about orders and an MCP tool is connected"
    elif sources:
        ctx.route, why = "search", ("sources are connected to the agent (if they say nothing about BharatAgent, "
                                    "the built-in description answers)" if about else "sources are connected to the agent")
    else:
        ctx.route, why = "direct", "no sources are connected to the agent"

    return {"route": ctx.route, "why": why, "sources": sources, "tools": tools}
