// The canvas model, kept free of React so it can be unit-tested:
// - the nine node types of the handoff and how they look,
// - converting between React Flow's nodes/edges and the agreed graph JSON (nodes: id, type, config;
//   edges: from, to), which is what the api validates, saves and runs,
// - turning the api's real SSE events into a highlight status per node.

export const MAIN_LINE = ["input", "guardrails", "router", "agent", "output"];

// kind: how the canvas presents a node. A docs link is a source_doc with config.url (handoff node types).
export const NODE_KINDS = {
  input: { type: "input", label: "Input", group: "main", config: {} },
  guardrails: { type: "guardrails", label: "Guardrails", group: "main", config: { block: ["empty", "abuse", "off_topic"] } },
  router: { type: "router", label: "Router", group: "main", config: {} },
  agent: { type: "agent", label: "Agent", group: "main", config: { model: "gemini", language: "en", local_only: false } },
  output: { type: "output", label: "Output", group: "main", config: {} },
  pdf: { type: "source_doc", label: "PDF", group: "source", config: { file: "" } },
  url: { type: "source_doc", label: "Docs link", group: "source", config: { url: "" } },
  repo: { type: "source_repo", label: "GitHub repo", group: "source", config: { url: "" } },
  calculator: { type: "tool_calculator", label: "Calculator", group: "tool", config: {} },
  mcp: { type: "tool_mcp", label: "MCP tool", group: "tool", config: { server: "orders-db", mode: "read_only" } },
};

export const PALETTE = ["pdf", "url", "repo", "calculator", "mcp"];

export function kindOf(type, config = {}) {
  if (type === "source_doc") return "url" in config ? "url" : "pdf";
  if (type === "source_repo") return "repo";
  if (type === "tool_calculator") return "calculator";
  if (type === "tool_mcp") return "mcp";
  return type;
}

const MAIN_IDS = { input: "n1", guardrails: "n2", router: "n3", agent: "n4", output: "n5" };

function flowNode(id, type, config, position) {
  const kind = kindOf(type, config);
  const main = NODE_KINDS[kind]?.group === "main";
  return { id, type: "pipeline", position, data: { type, config, kind }, deletable: !main };
}

function flowEdge(from, to) {
  return { id: `${from}->${to}`, source: from, target: to };
}

// The five main blocks in a row, connected, ready to run.
export function defaultCanvas() {
  const nodes = MAIN_LINE.map((t, i) =>
    flowNode(MAIN_IDS[t], t, structuredClone(NODE_KINDS[t].config), { x: 40 + i * 230, y: 220 }));
  const edges = MAIN_LINE.slice(1).map((t, i) => flowEdge(MAIN_IDS[MAIN_LINE[i]], MAIN_IDS[t]));
  return { nodes, edges };
}

export function newNodeId(kind, nodes) {
  const prefix = NODE_KINDS[kind].group === "tool" ? "tool" : "src";
  const used = new Set(nodes.map((n) => n.id));
  let i = 1;
  while (used.has(`${prefix}${i}`)) i += 1;
  return `${prefix}${i}`;
}

export function newNode(kind, nodes, position) {
  const spec = NODE_KINDS[kind];
  return flowNode(newNodeId(kind, nodes), spec.type, structuredClone(spec.config), position);
}

// React Flow state -> the graph JSON the api understands (positions are kept for saving the layout).
export function toGraph(nodes, edges) {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.type,
      config: n.data.config,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({ from: e.source, to: e.target })),
  };
}

// Graph JSON -> React Flow state. Nodes saved without a position get a tidy default spot.
export function fromGraph(graph) {
  let extra = 0;
  const nodes = graph.nodes.map((n) => {
    const mainIndex = MAIN_LINE.indexOf(n.type);
    const fallback = mainIndex >= 0 ? { x: 40 + mainIndex * 230, y: 220 } : { x: 500 + (extra++ % 3) * 200, y: 30 + Math.floor((extra - 1) / 3) * 110 };
    return flowNode(n.id, n.type, n.config || {}, n.position || fallback);
  });
  const edges = graph.edges.map((e) => flowEdge(e.from, e.to));
  return { nodes, edges };
}

// One SSE event -> the next status map { nodeId: status }. Statuses for main blocks come straight from
// the engine's stage events (running / done / blocked / skipped / error). Source nodes are marked from
// the agent's own report: "searched" when retrieval looked at them, "cited" when the answer cites them.
export function applyEvent(statuses, event) {
  const next = { ...statuses };
  if (event.type === "stage") {
    next[event.node_id] = event.status;
    if (event.stage === "agent" && event.status === "done" && event.detail) {
      for (const id of event.detail.retrieval?.searched || []) next[id] = "searched";
    }
  } else if (event.type === "error") {
    next[event.node_id] = "error";
  } else if (event.type === "result") {
    for (const c of event.citations || []) next[c.source_id] = "cited";
  }
  return next;
}

// Edges into a node that is running are animated, so the flow of the question is visible.
export function edgesWithActivity(edges, statuses) {
  return edges.map((e) => ({
    ...e,
    animated: statuses[e.target] === "running",
    className: statuses[e.target] && statuses[e.target] !== "running" && statuses[e.source] ? "edge-passed" : undefined,
  }));
}

// Backend validation errors -> { nodeId: [messages] } for marking nodes, plus messages with no node.
export function errorsByNode(errors) {
  const byNode = {};
  const general = [];
  for (const e of errors) {
    if (e.node_id) (byNode[e.node_id] ||= []).push(e.message);
    else general.push(e.message);
  }
  return { byNode, general };
}

// A short description of a node's settings, shown on the node itself.
export function summary(type, config = {}) {
  switch (type) {
    case "agent":
      return `${config.model || "gemini"} · ${config.language || "en"}${config.local_only ? " · local only" : ""}`;
    case "guardrails":
      return (config.block || []).join(", ") || "no checks";
    case "source_doc":
      return "url" in config ? config.url || "no link yet" : config.file || "no PDF yet";
    case "source_repo":
      return config.url ? config.url.replace("https://github.com/", "") : "no repo yet";
    case "tool_mcp":
      return `${config.server || "?"} · ${config.mode || "no mode"}`;
    case "tool_calculator":
      return "exact arithmetic";
    default:
      return "";
  }
}
