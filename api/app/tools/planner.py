"""Decides which connected tools to call for a question. Each of the four models has a different native
function-calling format, so the same chosen model is simply asked for a small JSON plan, which works the
same way for Gemini, Groq and the local models. Only tools drawn into the agent are offered."""

import json
import re

from .runner import ToolCall

MAX_CALLS = 3

PLANNER_SYSTEM = (
    "You decide whether tools are needed to answer a question. Reply with JSON only, no other text:\n"
    '{"calls": [{"node_id": "<id>", "tool": "<tool name>", "args": {...}}]}\n'
    'Use {"calls": []} when no tool is needed. Use at most 3 calls. Use only the tools listed, with the exact '
    "node_id and tool name. For the calculator, write one arithmetic expression with the actual numbers "
    "(take numbers from the question and the sources, e.g. '1200 * 80%')."
)


def plan_prompt(question: str, tools: list[dict], sources: list[str]) -> str:
    lines = ["Tools:"]
    for t in tools:
        if t.get("tool"):
            lines.append(f'- node_id "{t["node_id"]}", tool "{t["tool"]}", args {json.dumps(t["args"])}: {t["description"]}')
    if sources:
        lines += ["", "Sources (for numbers you may need):"] + [f"- {s[:400]}" for s in sources[:4]]
    lines += ["", f"Question: {question}", "", "JSON:"]
    return "\n".join(lines)


def parse(text: str, tools: list[dict]) -> tuple[list[ToolCall], str | None]:
    """Read the model's JSON plan. Returns (calls, problem). Calls naming a tool that was not offered are
    dropped and reported, so a model cannot invent a tool or reach an unconnected node."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return [], "The model did not return a tool plan."
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return [], "The model's tool plan was not valid JSON."
    offered = {(t["node_id"], t["tool"]) for t in tools if t.get("tool")}
    calls, dropped = [], []
    for c in (data.get("calls") or [])[:MAX_CALLS]:
        if not isinstance(c, dict):
            continue
        key = (str(c.get("node_id", "")), str(c.get("tool", "")))
        if key in offered and isinstance(c.get("args", {}), dict):
            calls.append(ToolCall(key[0], key[1], c.get("args") or {}))
        else:
            dropped.append(f"{key[0]}.{key[1]}")
    return calls, (f"Ignored tools that are not connected: {', '.join(dropped)}" if dropped else None)
