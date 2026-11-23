"""The pipeline graph exactly as the canvas saves it: nodes (id, type, config) and edges (from, to).
Only the shape is checked here; the pipeline rules live in validate.py so their errors read clearly."""

from pydantic import BaseModel, ConfigDict, Field

MAIN_LINE = ["input", "guardrails", "router", "agent", "output"]
# The 9 node types from the handoff. A docs URL is a source_doc with {"url": ...} instead of {"file": ...}.
SOURCE_TYPES = {"source_repo", "source_doc"}
TOOL_TYPES = {"tool_calculator", "tool_mcp"}
NODE_TYPES = set(MAIN_LINE) | SOURCE_TYPES | TOOL_TYPES
LANGUAGES = {"en", "te", "hi"}
MODELS = ["gemini", "groq", "local"]  # keys of models/generate.py PROVIDERS


class Node(BaseModel):
    id: str
    type: str
    config: dict = Field(default_factory=dict)


class Edge(BaseModel):
    # "from" is a Python keyword, so the field is from_ but the JSON key stays "from".
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge] = Field(default_factory=list)

    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def of_type(self, node_type: str) -> list[Node]:
        return [n for n in self.nodes if n.type == node_type]

    def connected_to(self, node_id: str) -> list[Node]:
        """Nodes that have an edge into node_id (for the agent: its sources and tools)."""
        ids = {e.from_ for e in self.edges if e.to == node_id}
        return [n for n in self.nodes if n.id in ids]
