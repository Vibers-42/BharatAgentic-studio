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


def looks_like_math(question: str) -> bool:
    text = question.lower()
    return bool(ARITHMETIC.search(text)) or bool(set(WORD.findall(text)) & MATH_WORDS)


def is_greeting(question: str) -> bool:
    return question.lower().strip(" !.?।") in GREETINGS


def run(ctx: RunContext, node: Node, graph: Graph, agent: Node) -> dict:
    connected = graph.connected_to(agent.id)
    sources = [n.id for n in connected if n.type in SOURCE_TYPES]
    tools = [n.id for n in connected if n.type in TOOL_TYPES]

    if is_greeting(ctx.question):
        ctx.route, why = "direct", "greeting or thanks"
    elif tools and looks_like_math(ctx.question):
        ctx.route, why = "tool", "the question needs a calculation and a tool is connected"
    elif sources:
        ctx.route, why = "search", "sources are connected to the agent"
    else:
        ctx.route, why = "direct", "no sources are connected to the agent"

    return {"route": ctx.route, "why": why, "sources": sources, "tools": tools}
