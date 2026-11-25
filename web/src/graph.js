// Builds the pipeline graph JSON the quick chat sends: the five main blocks, plus each quick-chat source
// that holds content and each tool the user switched on, all drawn into the agent. Switched-off sources
// and tools are left out of the graph entirely, so they can never influence an answer.
export const SOURCES = [
  { id: "chat_pdf", type: "source_doc", kind: "pdf", label: "PDF" },
  { id: "chat_repo", type: "source_repo", kind: "repo", label: "GitHub repo" },
  { id: "chat_url", type: "source_doc", kind: "url", label: "Docs link" },
];

export const TOOLS = [
  { id: "chat_calc", type: "tool_calculator", kind: "calculator", label: "Calculator", config: {} },
  { id: "chat_orders", type: "tool_mcp", kind: "mcp", label: "MCP tool · orders-db", config: { server: "orders-db", mode: "read_only" } },
];

export const STAGES = [
  ["n1", "input", "Input"],
  ["n2", "guardrails", "Guardrails"],
  ["n3", "router", "Router"],
  ["n4", "agent", "Agent"],
  ["n5", "output", "Output"],
];

// sources: [{ id, type, config, connected }]; tools: [{ id, type, config, connected }]
export function buildGraph({ sources, tools = [], language, model }) {
  const nodes = [
    { id: "n1", type: "input", config: {} },
    { id: "n2", type: "guardrails", config: { block: ["empty", "abuse", "off_topic"] } },
    { id: "n3", type: "router", config: {} },
    { id: "n4", type: "agent", config: { model: model || "groq", language: language || "en" } },
    { id: "n5", type: "output", config: {} },
  ];
  const edges = [
    { from: "n1", to: "n2" },
    { from: "n2", to: "n3" },
    { from: "n3", to: "n4" },
    { from: "n4", to: "n5" },
  ];
  for (const s of [...sources, ...tools].filter((s) => s.connected)) {
    nodes.push({ id: s.id, type: s.type, config: s.config });
    edges.push({ from: s.id, to: "n4" });
  }
  return { nodes, edges };
}
