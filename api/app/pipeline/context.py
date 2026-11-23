"""The state one question carries through the pipeline. Each stage reads it and fills in its part,
so stages never call each other directly."""

from dataclasses import dataclass, field

from .graph import Node


@dataclass
class RunContext:
    question: str
    requested_language: str | None = None  # from the chat's language selector, if set
    requested_model: str | None = None     # from the chat's model dropdown, if set
    user_id: int | None = None             # who asked; None means "do not log usage" (tests)
    scope: str = "inline"                  # whose chunks to search: "graph:<id>" or "inline"

    # Filled in by the stages, in order.
    language: str = "en"                    # input: language the answer must be in
    blocked: bool = False                   # guardrails: True stops router and agent
    block_reason: str = ""
    route: str = ""                         # router: "search", "tool" or "direct"
    sources: list[Node] = field(default_factory=list)  # agent: sources drawn into the agent
    tools: list[Node] = field(default_factory=list)    # agent: tools drawn into the agent
    model_key: str = ""                     # agent: model asked for (gemini / groq / local)
    answer: str = ""
    citations: list[dict] = field(default_factory=list)  # agent: checked [n] -> file, page, chunk
    claims: list[dict] = field(default_factory=list)     # agent: per-sentence check against the cited chunk
    supported: bool = False                 # agent: True only if the answer cites a real chunk
    grounded: bool = False                  # agent: True if the answer came from retrieved chunks
    generation: dict | None = None          # agent: model used, tokens, timing, fallback (None = no model call)
    cost: dict | None = None                # agent: estimated cost of this answer
    comparison: list[dict] = field(default_factory=list)  # agent: the same tokens priced on every model
    model_note: str | None = None           # agent: honest note, e.g. small local model in Telugu
    warnings: list[str] = field(default_factory=list)
