// Builds the pipeline graph JSON the chat sends. This fixed layout stands in for the canvas until
// Phase 6: the PDF source is in the graph only while "Connected to agent" is ticked.
export const SOURCE_ID = "chat_pdf";

export const STAGES = [
  ["n1", "input", "Input"],
  ["n2", "guardrails", "Guardrails"],
  ["n3", "router", "Router"],
  ["n4", "agent", "Agent"],
  ["n5", "output", "Output"],
];

export function buildGraph({ fileName, connected, language, model }) {
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
  if (fileName && connected) {
    nodes.push({ id: SOURCE_ID, type: "source_doc", config: { file: fileName } });
    edges.push({ from: SOURCE_ID, to: "n4" });
  }
  return { nodes, edges };
}
