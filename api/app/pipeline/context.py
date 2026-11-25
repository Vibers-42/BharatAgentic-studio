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
    local_only: bool = False               # privacy: no cloud LLM API may be called, not even as fallback
    # Phase 8: which stored source each connected source node may search (node id -> chunk source id). When it is
    # set, a source node that is not in it is never searched (the api only puts authorized, ready sources in it).
    source_map: dict | None = None
    history: list = field(default_factory=list)   # earlier [(role, text)] of this conversation, newest last
    owner: dict = field(default_factory=dict)      # org_id, project_id, graph_id, conversation_id for the usage row
    previous_citations: list = field(default_factory=list)  # what the last answer of this conversation cited
    can_approve: bool = False              # a live stream is open, so a risky tool can wait for the user's approval

    # Filled in by the stages, in order.
    language: str = "en"                    # input: language the answer must be in
    roman: bool = False                     # input: Telugu/Hindi typed in English letters; answer the same way
    blocked: bool = False                   # guardrails: True stops router and agent
    block_reason: str = ""
    route: str = ""                         # router: "search", "tool" or "direct"
    about_app: bool = False                 # router: the question is about BharatAgent Studio itself
    sources: list[Node] = field(default_factory=list)  # agent: sources drawn into the agent
    tools: list[Node] = field(default_factory=list)    # agent: tools drawn into the agent
    model_key: str = ""                     # agent: model asked for (gemini / groq / local)
    answer: str = ""
    citations: list[dict] = field(default_factory=list)  # agent: checked [n] -> file, page, chunk
    claims: list[dict] = field(default_factory=list)     # agent: per-sentence check against the cited chunk
    supported: bool = False                 # agent: True only if the answer cites a real chunk
    grounded: bool = False                  # agent: True if the answer came from retrieved chunks
    generation: dict | None = None          # agent: model used, tokens, timing, fallback (None = no model call)
    generations: list = field(default_factory=list)       # agent: every model call (tool plan, answer)
    generation_steps: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)  # agent: each tool call with its result or error
    cost: dict | None = None                # agent: estimated cost of this answer
    comparison: list[dict] = field(default_factory=list)  # agent: the same tokens priced on every model
    model_note: str | None = None           # agent: honest note, e.g. small local model in Telugu
    warnings: list[str] = field(default_factory=list)
    retrieval_info: dict = field(default_factory=dict)  # agent: search queries, rewrite, reused evidence
    detailed: bool = False                  # agent: the question asks for more than a one-line answer
