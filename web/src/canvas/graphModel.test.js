// Unit tests for the canvas model: graph JSON round trip, node rules, and SSE event highlighting.
import { describe, expect, it } from "vitest";
import {
  applyEvent, defaultCanvas, edgesWithActivity, errorsByNode, fromGraph, kindOf, newNode, NODE_KINDS, PALETTE,
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
    expect(summary("source_doc", { url: "" })).toBe("no link yet");
    expect(summary("tool_mcp", { server: "orders-db", mode: "read_only" })).toBe("orders-db · read_only");
  });
});
