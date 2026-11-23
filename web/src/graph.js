// Builds the pipeline graph JSON the chat sends. This fixed layout stands in for the canvas until
// Phase 6: a source is in the graph only while it holds content and its "connected" box is ticked.
export const SOURCES = [
  { id: "chat_pdf", type: "source_doc", kind: "pdf", label: "PDF" },
  { id: "chat_repo", type: "source_repo", kind: "repo", label: "GitHub repo" },
  { id: "chat_url", type: "source_doc", kind: "url", label: "Docs link" },
];

export const STAGES = [
  ["n1", "input", "Input"],
  ["n2", "guardrails", "Guardrails"],
  ["n3", "router", "Router"],
  ["n4", "agent", "Agent"],
  ["n5", "output", "Output"],
];

// sources: [{ id, type, config, connected }]
export function buildGraph({ sources, language, model }) {
  const nodes = [
    { id: "n1", type: "input", config: {} },
    { id: "n2", type: "guardrails", config: { block: ["empty", "abuse", "off_topic"] } },
    { id: "n3", type: "router", config: {} },
    { id: "n4", type: "agent", config: { model: model || "gemini", language: language || "en" } },
    { id: "n5", type: "output", config: {} },
  ];
  const edges = [
    { from: "n1", to: "n2" },
    { from: "n2", to: "n3" },
    { from: "n3", to: "n4" },
    { from: "n4", to: "n5" },
  ];
  for (const s of sources.filter((s) => s.connected)) {
    nodes.push({ id: s.id, type: s.type, config: s.config });
    edges.push({ from: s.id, to: "n4" });
  }
  return { nodes, edges };
}
