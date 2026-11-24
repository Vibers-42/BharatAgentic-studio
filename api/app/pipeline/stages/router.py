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
GREETINGS = {"hi", "hello", "hey", "namaste", "namaskar", "thanks", "thank you",
             "नमस्ते", "धन्यवाद", "నమస్కారం", "ధన్యవాదాలు"}
WORD = re.compile(r"[\wऀ-౿]+")


# "My lab test cost Rs. 1200 ... how much will I get back?": a number plus a how-much / total question.
AMOUNT_QUESTION = ("how much", "how many rupees", "what amount", "get back", "total", "కి ఎంత", "कितना", "कितने")
ORDER_WORDS = re.compile(r"\bord-\d+|\border(s)?\b|ఆర్డర్|ऑर्डर", re.I)


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

    if is_greeting(ctx.question):
        ctx.route, why = "direct", "greeting or thanks"
    elif calculator and looks_like_math(ctx.question):
        ctx.route, why = "tool", "the question needs a calculation and a calculator is connected"
    elif mcp and mentions_orders(ctx.question):
        ctx.route, why = "tool", "the question is about orders and an MCP tool is connected"
    elif sources:
        ctx.route, why = "search", "sources are connected to the agent"
    else:
        ctx.route, why = "direct", "no sources are connected to the agent"

    return {"route": ctx.route, "why": why, "sources": sources, "tools": tools}
