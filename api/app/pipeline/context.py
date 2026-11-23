"""The state one question carries through the pipeline. Each stage reads it and fills in its part,
so stages never call each other directly."""

from dataclasses import dataclass, field

from .graph import Node


@dataclass
class RunContext:
    question: str
    requested_language: str | None = None  # from the chat's language selector, if set

    # Filled in by the stages, in order.
    language: str = "en"                    # input: language the answer must be in
    blocked: bool = False                   # guardrails: True stops router and agent
    block_reason: str = ""
    route: str = ""                         # router: "search", "tool" or "direct"
    sources: list[Node] = field(default_factory=list)  # agent: sources drawn into the agent
    tools: list[Node] = field(default_factory=list)    # agent: tools drawn into the agent
    answer: str = ""
    citations: list[dict] = field(default_factory=list)  # agent: checked [n] -> file, page, chunk
    supported: bool = False                 # agent: True only if the answer cites a real chunk
    grounded: bool = False                  # agent: True if the answer came from retrieved chunks
    model: str = ""
    usage: dict = field(default_factory=dict)
