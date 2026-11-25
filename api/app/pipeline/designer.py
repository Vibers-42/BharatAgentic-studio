"""Design an agent from a plain description of the use case ("describe it, get a ready agent").

The model never writes the graph itself. It fills in a small JSON *design* (name, language, instructions,
which sources and tools, which guardrails); compile_design() turns that design into the graph with fixed ids
and only known node types, and validate_graph() checks the result like any graph drawn by hand. So a model
can suggest blocks, but it cannot invent a node type, an MCP server, a write mode or a broken line.

The project's existing ready sources are offered to the model by id, so a design can reuse knowledge that is
already there (the agent is then runnable at once). Anything else the user still has to add is returned as
"todo" steps, shown next to the canvas."""

import json
import re
from dataclasses import dataclass, field

from ..models import generate
from .graph import LANGUAGES, MAIN_LINE, Graph
from .validate import MAX_INSTRUCTIONS, validate_graph

MAX_DESCRIPTION = 1500
MAX_SOURCES = 4
DESIGN_ATTEMPTS = 2  # one retry when the reply holds no readable JSON
SOURCE_KINDS = {"pdf": "source_doc", "url": "source_doc", "repo": "source_repo"}
TOOLS = {"calculator": ("tool_calculator", {}),
         "orders": ("tool_mcp", {"server": "orders-db", "mode": "read_only"}),
         # Lookups plus actions that change orders (cancel). Every action waits for the user's Approve.
         "orders_actions": ("tool_mcp", {"server": "orders-db", "mode": "read_write", "allow_write": True})}
CHECKS = ["empty", "abuse", "off_topic"]
MAIN_IDS = {"input": "n1", "guardrails": "n2", "router": "n3", "agent": "n4", "output": "n5"}

DESIGNER_SYSTEM = """You design question-answering assistants for BharatAgent Studio, a platform for colleges,
clinics and small offices in India. From the user's description, choose the building blocks. Reply with JSON
only, no other text, in exactly this shape:
{
  "name": "short agent name, at most 6 words",
  "summary": "one sentence: what this agent does and for whom",
  "language": "en" | "te" | "hi",
  "instructions": "2-4 sentences: the agent's role, audience and tone (no rules about citations; those are fixed)",
  "sources": [{"kind": "pdf" | "url" | "repo", "title": "what document this is", "url": "only if the user gave one", "use_source_id": null}],
  "tools": ["calculator", "orders" or "orders_actions"],
  "guardrails": ["empty", "abuse", "off_topic"],
  "local_only": false,
  "reasons": ["one short line for each source, tool and the language you chose, saying why in plain words a clinic or college office would use (no field or block names; not for name or summary)"],
  "suggested_questions": ["2 or 3 example questions a real user would ask, in the agent's language"]
}
Blocks you can choose:
- sources: "pdf" = an uploaded file (PDF, Word, text, CSV or Excel: policies, prospectus, price lists, notes);
  "url" = one public docs web page; "repo" = a public GitHub repository. Use 1 to 4 sources: list EVERY
  document the agent needs as its own entry (the user adds the file afterwards; never leave a needed document
  out of "sources"). If one of the EXISTING project sources listed below fits, set "use_source_id" to its id
  instead of asking for a new one.
- tools: "calculator" when answers need arithmetic (fees, refunds, percentages, totals, discounts);
  "orders" only for looking up orders / order status in the demo orders system; "orders_actions" instead of
  "orders" when users must also be able to cancel orders (each cancellation waits for the user's approval).
  Use [] when no tool is needed.
- guardrails: keep all three unless the description says otherwise.
- language: "te" for Telugu, "hi" for Hindi, otherwise "en". Pick the language the users will ask in.
- local_only: true only if the description asks for privacy, offline use, or keeping data on this computer.
The assistant can only answer from its sources and tools; never promise anything else."""


@dataclass
class Design:
    name: str
    summary: str
    language: str
    instructions: str
    sources: list[dict]
    tools: list[str]
    guardrails: list[str]
    local_only: bool
    reasons: list[str] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)


def design_prompt(description: str, existing: dict[int, dict]) -> str:
    lines = ["EXISTING project sources (reuse by id when one fits):"]
    lines += [f'- id {sid}: "{info["title"]}" ({info["kind"]})' for sid, info in existing.items()] or ["- none yet"]
    lines += ["", "Use case described by the user:", description.strip(), "", "JSON:"]
    return "\n".join(lines)


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _url(value) -> str:
    url = str(value or "").strip()
    return url if re.match(r"^https?://\S+$", url) and len(url) <= 500 else ""


# Internal block names the model sometimes repeats in its reasons, said the way a user would.
PLAIN_NAMES = [(re.compile(r"\borders_actions\b(?:\s+tool)?", re.I), "the orders tool (lookups and cancellations, with approval)"),
               (re.compile(r"\buse_source_id\b", re.I), "an existing project source"),
               (re.compile(r"\bsource_doc\b|\bsource_repo\b", re.I), "a source"),
               (re.compile(r"\btool_calculator\b", re.I), "the calculator"),
               (re.compile(r"\btool_mcp\b", re.I), "the orders tool"),
               (re.compile(r"\blocal_only\b", re.I), "Local only"),
               (re.compile(r"\s*\(id \d+\)"), "")]


def plain(text: str) -> str:
    for pattern, words in PLAIN_NAMES:
        text = pattern.sub(words, text)
    return re.sub(r"\s{2,}", " ", text).strip()


def parse_design(text: str, existing: dict[int, dict]) -> Design | None:
    """Read the model's reply into a Design, keeping only known values (anything else is dropped or clipped).
    Returns None when the reply holds no JSON object."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    sources, used = [], set()
    for s in data.get("sources") or []:
        if not isinstance(s, dict) or len(sources) >= MAX_SOURCES:
            continue
        ref = s.get("use_source_id")
        try:
            ref = int(ref) if ref is not None else None
        except (TypeError, ValueError):
            ref = None
        if ref is not None and ref in existing and ref not in used:
            info = existing[ref]
            kind = "repo" if info["kind"] == "repo" else "url" if info["kind"] == "url" else "pdf"
            sources.append({"kind": kind, "title": _text(info["title"], 120), "url": "", "source_ref": ref})
            used.add(ref)
            continue
        kind = s.get("kind") if s.get("kind") in SOURCE_KINDS else "pdf"
        url = _url(s.get("url")) if kind != "pdf" else ""
        if kind == "url" and not url:
            kind = "pdf"  # "a docs page" with no address is a document the user has: ask for the file instead
        title = _text(s.get("title"), 120) or {"pdf": "Document", "url": "Docs page", "repo": "GitHub repository"}[kind]
        sources.append({"kind": kind, "title": title, "url": url, "source_ref": None})

    tools = [t for t in dict.fromkeys(data.get("tools") or []) if t in TOOLS]
    if "orders_actions" in tools and "orders" in tools:
        tools.remove("orders")  # the actions block also looks orders up; one orders block is enough
    if not sources and tools not in (["orders"], ["orders_actions"]):
        # Answers come only from sources and tools, so an agent without any source could answer almost nothing.
        # Models sometimes describe the document in "reasons" but leave "sources" empty: add the block for it.
        sources.append({"kind": "pdf", "title": "Your documents", "url": "", "source_ref": None})
    guardrails = [c for c in CHECKS if c in (data.get("guardrails") or CHECKS)] or ["empty"]
    language = data.get("language") if data.get("language") in LANGUAGES else "en"
    return Design(
        name=_text(data.get("name"), 60) or "My agent",
        summary=_text(data.get("summary"), 240),
        language=language,
        instructions=_text(data.get("instructions"), MAX_INSTRUCTIONS),
        sources=sources,
        tools=tools,
        guardrails=guardrails,
        local_only=data.get("local_only") is True,
        reasons=[plain(_text(r, 200)) for r in (data.get("reasons") or []) if _text(r, 200)][:6],
        suggested_questions=[_text(q, 200) for q in (data.get("suggested_questions") or []) if _text(q, 200)][:3],
    )


def compile_design(design: Design, model: str) -> Graph:
    """The deterministic step: a design always becomes the five main blocks in order, plus one node per
    source and tool, each with a single line into the agent. Positions are left out (the canvas lays it out)."""
    nodes = [
        {"id": MAIN_IDS["input"], "type": "input", "config": {}},
        {"id": MAIN_IDS["guardrails"], "type": "guardrails", "config": {"block": design.guardrails}},
        {"id": MAIN_IDS["router"], "type": "router", "config": {}},
        {"id": MAIN_IDS["agent"], "type": "agent", "config": {
            "model": model, "language": design.language, "local_only": design.local_only,
            **({"instructions": design.instructions} if design.instructions else {})}},
        {"id": MAIN_IDS["output"], "type": "output", "config": {}},
    ]
    edges = [{"from": MAIN_IDS[a], "to": MAIN_IDS[b]} for a, b in zip(MAIN_LINE, MAIN_LINE[1:])]
    for i, s in enumerate(design.sources, start=1):
        config = {"title": s["title"]}
        config.update({"url": s["url"]} if s["kind"] in ("url", "repo") else {"file": ""})
        if s.get("source_ref") is not None:
            config["source_ref"] = s["source_ref"]
        nodes.append({"id": f"src{i}", "type": SOURCE_KINDS[s["kind"]], "config": config})
        edges.append({"from": f"src{i}", "to": MAIN_IDS["agent"]})
    for i, t in enumerate(design.tools, start=1):
        node_type, config = TOOLS[t]
        nodes.append({"id": f"tool{i}", "type": node_type, "config": dict(config)})
        edges.append({"from": f"tool{i}", "to": MAIN_IDS["agent"]})
    return Graph.model_validate({"nodes": nodes, "edges": edges})


def todo_steps(design: Design) -> list[dict]:
    """What the user still has to do before the agent can answer: one step per source without knowledge."""
    steps = []
    for i, s in enumerate(design.sources, start=1):
        if s.get("source_ref") is not None:
            continue
        what = {"pdf": "Add the file", "url": "Fetch the page", "repo": "Download the repository"}[s["kind"]]
        steps.append({"node_id": f"src{i}", "text": f"{what} for “{s['title']}”"})
    return steps


class DesignError(RuntimeError):
    pass


def design_agent(description: str, existing: dict[int, dict], local_only: bool = False,
                 model: str = "groq") -> tuple[Design, Graph, generate.Generation]:
    """Ask the model for a design (one retry if the reply is unreadable), compile it and validate it.
    Uses the normal generate() chain, so a failing cloud model falls back to the local models."""
    if local_only and not generate.is_local(model):
        model = "local"
    prompt = design_prompt(description, existing)
    for _ in range(DESIGN_ATTEMPTS):
        gen = generate.generate(model, DESIGNER_SYSTEM, prompt, local_only=local_only)
        design = parse_design(gen.text, existing)
        if design is not None:
            break
        prompt = design_prompt(description, existing) + "\n(Your last reply was not valid JSON. Reply with the JSON object only.)"
    else:
        raise DesignError("The model did not return a readable design. Please try again or reword the description.")
    design.local_only = design.local_only or local_only
    # The agent runs on the model that designed it, unless local-only asks for the local model.
    agent_model = "local" if design.local_only and not generate.is_local(gen.model) else gen.model
    graph = compile_design(design, agent_model)
    errors = validate_graph(graph)
    if errors:  # cannot happen with compile_design, but never hand out a graph the api would refuse
        raise DesignError("The designed agent was not valid: " + "; ".join(e["message"] for e in errors))
    return design, graph, gen
