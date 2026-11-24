// Unit tests for the canvas model: graph JSON round trip, node rules, and SSE event highlighting.
import { describe, expect, it } from "vitest";
import {
  applyEvent, autoLayout, defaultCanvas, edgesWithActivity, errorsByNode, fromGraph, kindOf, newNode, NODE_KINDS, PALETTE,
  summary, toGraph,
} from "./graphModel.js";

describe("node types", () => {
  it("covers the nine node types of the handoff", () => {
    const types = new Set(Object.values(NODE_KINDS).map((k) => k.type));
    expect([...types].sort()).toEqual(["agent", "guardrails", "input", "output", "router", "source_doc",
      "source_repo", "tool_calculator", "tool_mcp"]);
  });

  it("offers only sources and tools in the palette; main blocks are fixed", () => {
    expect(PALETTE.every((k) => NODE_KINDS[k].group !== "main")).toBe(true);
    const { nodes } = defaultCanvas();
    expect(nodes.every((n) => n.deletable === false)).toBe(true);
  });

  it("tells a PDF from a docs link by the url setting", () => {
    expect(kindOf("source_doc", { file: "a.pdf" })).toBe("pdf");
    expect(kindOf("source_doc", { url: "https://x" })).toBe("url");
  });
});

describe("graph JSON", () => {
  it("starts with the five blocks connected in order", () => {
    const graph = toGraph(...Object.values(defaultCanvas()));
    expect(graph.nodes.map((n) => n.type)).toEqual(["input", "guardrails", "router", "agent", "output"]);
    expect(graph.edges).toEqual([{ from: "n1", to: "n2" }, { from: "n2", to: "n3" }, { from: "n3", to: "n4" }, { from: "n4", to: "n5" }]);
  });

  it("round-trips through the agreed format, with positions", () => {
    const { nodes, edges } = defaultCanvas();
    const src = newNode("repo", nodes, { x: 10, y: 20 });
    src.data.config.url = "https://github.com/octocat/Spoon-Knife";
    const graph = toGraph([...nodes, src], [...edges, { id: "e", source: src.id, target: "n4" }]);
    expect(graph.nodes.at(-1)).toEqual({ id: "src1", type: "source_repo", config: { url: "https://github.com/octocat/Spoon-Knife" }, position: { x: 10, y: 20 } });
    expect(graph.edges.at(-1)).toEqual({ from: "src1", to: "n4" });
    const back = fromGraph(graph);
    expect(toGraph(back.nodes, back.edges)).toEqual(graph);
  });

  it("places nodes saved without a position", () => {
    const { nodes } = fromGraph({ nodes: [{ id: "n1", type: "input", config: {} }, { id: "s1", type: "source_doc", config: {} }], edges: [] });
    expect(nodes.every((n) => Number.isFinite(n.position.x) && Number.isFinite(n.position.y))).toBe(true);
  });

  it("lays the main line top to bottom, sources left and tools right of the Agent", () => {
    const { nodes } = defaultCanvas();
    const ys = nodes.map((n) => n.position.y);
    expect(ys).toEqual([...ys].sort((a, b) => a - b));
    const src = newNode("pdf", nodes, null);
    const tool = newNode("calculator", nodes, null);
    const agent = nodes.find((n) => n.data.type === "agent");
    expect(src.position.x).toBeLessThan(agent.position.x);
    expect(tool.position.x).toBeGreaterThan(agent.position.x);
  });

  it("tidies graphs saved with the old left-to-right layout and routes edges to the Agent's side handles", () => {
    const old = { nodes: ["input", "guardrails", "router", "agent", "output"].map((t, i) => ({ id: `n${i + 1}`, type: t, config: {}, position: { x: i * 230, y: 220 } }))
      .concat([{ id: "s1", type: "source_doc", config: {}, position: { x: 500, y: 30 } }, { id: "t1", type: "tool_calculator", config: {}, position: { x: 700, y: 30 } }]),
    edges: [{ from: "n3", to: "n4" }, { from: "s1", to: "n4" }, { from: "t1", to: "n4" }] };
    const { nodes, edges } = fromGraph(old);
    expect(nodes.find((n) => n.id === "n5").position.y).toBeGreaterThan(nodes.find((n) => n.id === "n1").position.y);
    expect(edges.map((e) => e.targetHandle)).toEqual(["top", "left", "right"]);
    expect(autoLayout(nodes)).toEqual(nodes);
  });

  it("gives new nodes unique ids", () => {
    const { nodes } = defaultCanvas();
    const a = newNode("pdf", nodes, { x: 0, y: 0 });
    const b = newNode("url", [...nodes, a], { x: 0, y: 0 });
    const t = newNode("calculator", [...nodes, a, b], { x: 0, y: 0 });
    expect([a.id, b.id, t.id]).toEqual(["src1", "src2", "tool1"]);
    expect(b.data.config).toEqual({ url: "" });
  });
});

describe("highlighting from real SSE events", () => {
  const events = [
    { type: "stage", stage: "input", node_id: "n1", status: "running" },
    { type: "stage", stage: "input", node_id: "n1", status: "done" },
    { type: "stage", stage: "agent", node_id: "n4", status: "running" },
    { type: "token", stage: "agent", node_id: "n4", text: "80" },
    { type: "stage", stage: "agent", node_id: "n4", status: "done", detail: { retrieval: { searched: ["src1", "src2"] } } },
    { type: "result", citations: [{ source_id: "src2" }] },
  ];

  it("follows the engine's own stage statuses, node by node", () => {
    const after2 = events.slice(0, 3).reduce(applyEvent, {});
    expect(after2).toEqual({ n1: "done", n4: "running" });
  });

  it("marks searched and cited sources from the agent's report", () => {
    const final = events.reduce(applyEvent, {});
    expect(final).toMatchObject({ n4: "done", src1: "searched", src2: "cited" });
  });

  it("shows blocked and skipped stages and errors", () => {
    const s = [
      { type: "stage", node_id: "n2", status: "blocked" },
      { type: "stage", node_id: "n3", status: "skipped" },
      { type: "error", node_id: "n4", code: "all_providers_failed" },
    ].reduce(applyEvent, {});
    expect(s).toEqual({ n2: "blocked", n3: "skipped", n4: "error" });
  });

  it("lights tool nodes from tool_call and tool_result, and marks cited tools", () => {
    const s = [
      { type: "tool_call", node_id: "tool1", tool: "calculator" },
    ].reduce(applyEvent, {});
    expect(s).toEqual({ tool1: "running" });
    const after = [
      { type: "tool_result", node_id: "tool1", ok: true, output: "1200 * 80% = 960" },
      { type: "tool_call", node_id: "tool2", tool: "get_order" },
      { type: "tool_result", node_id: "tool2", ok: false, error: "MCP server unavailable" },
    ].reduce(applyEvent, s);
    expect(after).toEqual({ tool1: "called", tool2: "error" });
    expect(applyEvent(after, { type: "result", citations: [{ source_id: "tool1" }] }).tool1).toBe("cited");
  });

  it("marks the line of a cited source", () => {
    const out = edgesWithActivity([{ id: "c", source: "src1", target: "n4" }], { src1: "cited", n4: "done" });
    expect(out[0].className).toBe("edge-cited");
  });

  it("animates only edges into the running node", () => {
    const edges = [{ id: "a", source: "n1", target: "n2" }, { id: "b", source: "n2", target: "n3" }];
    const out = edgesWithActivity(edges, { n1: "done", n2: "running" });
    expect(out.map((e) => e.animated)).toEqual([true, false]);
  });
});

describe("validation errors and summaries", () => {
  it("groups backend errors by node", () => {
    const { byNode, general } = errorsByNode([
      { code: "not_connected", message: "Source 'src1' is not connected", node_id: "src1" },
      { code: "agent_count", message: "needs exactly one agent", node_id: null },
    ]);
    expect(byNode).toEqual({ src1: ["Source 'src1' is not connected"] });
    expect(general).toEqual(["needs exactly one agent"]);
  });

  it("summarises node settings on the node", () => {
    expect(summary("agent", { model: "local", language: "te", local_only: true })).toBe("local · te · local only");
    expect(summary("source_doc", { source_ref: 7 })).toBe("");  // the status line on the block says it instead
    expect(summary("source_repo", {})).toBe("");
    expect(summary("tool_mcp", { server: "orders-db", mode: "read_only" })).toBe("orders-db · read_only");
  });
});
