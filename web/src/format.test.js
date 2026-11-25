// Unit tests for the shared formatting helpers and the quick chat's graph builder.
import { describe, expect, it } from "vitest";
import { buildGraph, SOURCES, TOOLS } from "./graph.js";
import { compact, fillDays, inr, istToday, niceMax, secs, splitCitations, timeAgo } from "./format.js";

describe("formatting", () => {
  it("formats rupees, seconds and large numbers", () => {
    expect(inr(0)).toBe("₹0");
    expect(inr(0.0288)).toBe("₹0.0288");
    expect(inr(1234.5)).toBe("₹1,234.50");
    expect(inr(null)).toBe("—");
    expect(secs(450)).toBe("450 ms");
    expect(secs(45657)).toBe("45.7 s");
    expect(compact(12345)).toBe("12.3k");
    expect(compact(2500000)).toBe("2.5M");
  });

  it("splits an answer into text and citation markers", () => {
    expect(splitCitations("You get Rs. 960 [1][4].")).toEqual([{ text: "You get Rs. 960 " }, { cite: 1 }, { cite: 4 }, { text: "." }]);
    expect(splitCitations("no markers")).toEqual([{ text: "no markers" }]);
  });

  it("fills missing days so the daily chart has a continuous axis", () => {
    const days = fillDays([{ day: "2026-11-22", requests: 3, cost_inr: 1 }], 3, "2026-11-23");
    expect(days.map((d) => [d.day, d.requests])).toEqual([["2026-11-21", 0], ["2026-11-22", 3], ["2026-11-23", 0]]);
  });

  it("rounds the chart axis to a readable maximum", () => {
    expect([0, 1, 3, 7, 12, 545, 1000].map(niceMax)).toEqual([1, 1, 5, 10, 20, 1000, 1000]);
  });

  it("uses India time for today", () => {
    expect(istToday(Date.parse("2026-11-23T20:00:00Z"))).toBe("2026-11-24");
    expect(istToday(Date.parse("2026-11-23T10:00:00Z"))).toBe("2026-11-23");
  });

  it("says how long ago something was indexed", () => {
    const now = Date.parse("2026-11-23T12:00:00Z");
    expect(timeAgo("2026-11-23T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-11-23T11:00:00Z", now)).toBe("1 h ago");
  });
});

describe("quick chat graph", () => {
  it("draws only switched-on sources and tools into the agent", () => {
    const sources = SOURCES.map((s, i) => ({ ...s, config: {}, connected: i === 0 }));
    const tools = TOOLS.map((t) => ({ ...t, connected: t.kind === "calculator" }));
    const g = buildGraph({ sources, tools, language: "en", model: "local" });
    expect(g.nodes.map((n) => n.id)).toEqual(["n1", "n2", "n3", "n4", "n5", "chat_pdf", "chat_calc"]);
    expect(g.edges.slice(4)).toEqual([{ from: "chat_pdf", to: "n4" }, { from: "chat_calc", to: "n4" }]);
    expect(g.nodes.find((n) => n.id === "n4").config).toEqual({ model: "local", language: "en" });
  });

  it("keeps the MCP tool read-only", () => {
    expect(TOOLS.find((t) => t.kind === "mcp").config).toEqual({ server: "orders-db", mode: "read_only" });
  });
});

import { toolArgs } from "./format.js";

it("tool arguments: one by value, several with their names", () => {
  expect(toolArgs({ expression: "1200 * 80%" })).toBe('"1200 * 80%"');
  expect(toolArgs({ reason: "not needed", order_id: "ORD-1004" })).toBe('reason: "not needed", order_id: "ORD-1004"');
  expect(toolArgs({})).toBe("");
});
