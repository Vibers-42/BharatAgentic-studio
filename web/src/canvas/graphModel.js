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

// Layout: the main line runs top to bottom down the middle; sources sit to the left of the Agent and send
// into its left handle, tools sit to the right and send into its right handle.
export const LAYOUT = { mainX: 300, sourceX: 0, toolX: 600, rowGap: 140, sideGap: 115 };

export function mainPosition(type) {
  return { x: LAYOUT.mainX, y: MAIN_LINE.indexOf(type) * LAYOUT.rowGap };
}

// Where the n-th source (or tool) goes: a column beside the Agent, centred on it.
export function sidePosition(group, index, count) {
  const agentY = mainPosition("agent").y;
  const top = agentY - ((count - 1) * LAYOUT.sideGap) / 2;
  return { x: group === "tool" ? LAYOUT.toolX : LAYOUT.sourceX, y: Math.round(top + index * LAYOUT.sideGap) };
}

function flowNode(id, type, config, position) {
  const kind = kindOf(type, config);
  const main = NODE_KINDS[kind]?.group === "main";
  return { id, type: "pipeline", position, data: { type, config, kind }, deletable: !main };
}

// Which Agent handle an edge enters: sources on the left, tools on the right, the router from the top.
export function targetHandle(sourceType) {
  if (sourceType?.startsWith("source_")) return "left";
  if (sourceType?.startsWith("tool_")) return "right";
  return "top";
}

export function flowEdge(from, to, sourceType, targetType) {
  const edge = { id: `${from}->${to}`, source: from, target: to };
  if (targetType === "agent") edge.targetHandle = targetHandle(sourceType);
  return edge;
}

// The five main blocks top to bottom, connected, ready to run.
export function defaultCanvas() {
  const nodes = MAIN_LINE.map((t) => flowNode(MAIN_IDS[t], t, structuredClone(NODE_KINDS[t].config), mainPosition(t)));
  const edges = MAIN_LINE.slice(1).map((t, i) => flowEdge(MAIN_IDS[MAIN_LINE[i]], MAIN_IDS[t], MAIN_LINE[i], t));
  return { nodes, edges };
}

export function newNodeId(kind, nodes) {
  const prefix = NODE_KINDS[kind].group === "tool" ? "tool" : "src";
  const used = new Set(nodes.map((n) => n.id));
  let i = 1;
  while (used.has(`${prefix}${i}`)) i += 1;
  return `${prefix}${i}`;
}

// A new block: at the given spot (dropped on the canvas), or in the next free place of its column.
export function newNode(kind, nodes, position) {
  const spec = NODE_KINDS[kind];
  const inColumn = nodes.filter((n) => NODE_KINDS[n.data.kind]?.group === spec.group).length;
  const at = position || sidePosition(spec.group, inColumn, inColumn + 1);
  if (!position) at.y = mainPosition("agent").y + (inColumn % 2 ? 1 : -1) * Math.ceil(inColumn / 2) * LAYOUT.sideGap;
  return flowNode(newNodeId(kind, nodes), spec.type, structuredClone(spec.config), at);
}

// Tidy every block into the standard layout (main line down the middle, sources left, tools right).
export function autoLayout(nodes) {
  const sides = { source: nodes.filter((n) => NODE_KINDS[n.data.kind]?.group === "source"),
    tool: nodes.filter((n) => NODE_KINDS[n.data.kind]?.group === "tool") };
  return nodes.map((n) => {
    const group = NODE_KINDS[n.data.kind]?.group;
    if (group === "main") return { ...n, position: mainPosition(n.data.type) };
    const list = sides[group] || [];
    return { ...n, position: sidePosition(group, list.indexOf(n), list.length) };
  });
}

// True when the main line is not laid out top to bottom (e.g. a graph saved with the older left-to-right
// layout), so it is tidied on load.
export function needsLayout(nodes) {
  const ys = MAIN_LINE.map((t) => nodes.find((n) => n.data.type === t)?.position.y);
  return ys.some((y, i) => y === undefined || (i > 0 && y <= ys[i - 1]));
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

// Graph JSON -> React Flow state. Graphs saved without positions, or with the old left-to-right layout,
// are tidied into the standard layout.
export function fromGraph(graph) {
  let nodes = graph.nodes.map((n) => flowNode(n.id, n.type, n.config || {}, n.position || { x: 0, y: 0 }));
  if (graph.nodes.some((n) => !n.position) || needsLayout(nodes)) nodes = autoLayout(nodes);
  const typeOf = Object.fromEntries(graph.nodes.map((n) => [n.id, n.type]));
  const edges = graph.edges.map((e) => flowEdge(e.from, e.to, typeOf[e.from], typeOf[e.to]));
  return { nodes, edges };
}

// One SSE event -> the next status map { nodeId: status }. Statuses for main blocks come straight from
// the engine's stage events (running / done / blocked / skipped / error). Source nodes are marked from
// the agent's own report: "searched" when retrieval looked at them, "cited" when the answer cites them.
// Tool nodes light up from the agent's tool events: "running" on tool_call, then "called" or "error" on
// tool_result, and "cited" when the answer cites the tool's result.
export function applyEvent(statuses, event) {
  const next = { ...statuses };
  if (event.type === "stage") {
    next[event.node_id] = event.status;
    if (event.stage === "agent" && event.status === "done" && event.detail) {
      for (const id of event.detail.retrieval?.searched || []) next[id] = "searched";
    }
  } else if (event.type === "tool_call") {
    next[event.node_id] = "running";
  } else if (event.type === "tool_result") {
    next[event.node_id] = event.ok ? "called" : "error";
  } else if (event.type === "error") {
    next[event.node_id] = "error";
  } else if (event.type === "result") {
    for (const c of event.citations || []) next[c.source_id] = "cited";
  }
  return next;
}

// Edges into the running block are animated, so the flow of the question is visible;
// edges the run has passed are drawn as "passed", and a source or tool line whose content was cited glows.
export function edgesWithActivity(edges, statuses) {
  return edges.map((e) => {
    const from = statuses[e.source];
    const to = statuses[e.target];
    const live = to === "running";
    let className;
    if (from === "cited") className = "edge-cited";
    else if (!live && to && from) className = "edge-passed";
    return { ...e, animated: live, className };
  });
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
    case "source_repo":
      // Phase 8: a source block points at a project source; its status line (chunks and title, or
      // "Choose project knowledge") says everything, so there is no separate summary.
      return "";
    case "tool_mcp":
      return `${config.server || "?"} · ${config.mode || "no mode"}`;
    case "tool_calculator":
      return "exact arithmetic";
    default:
      return "";
  }
}
