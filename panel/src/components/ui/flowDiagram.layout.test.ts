import { describe, expect, it } from "vitest";

import { PROGGEST_EDGES, PROGGEST_LAYERS, PROGGEST_LOGIN_ROUTE } from "../../dev/flowSample";
import { connectedPath, edgeId, layoutFlow, pathEdges } from "./flowDiagram.layout";
import type { FlowLayout, PlacedNode } from "./flowDiagram.layout";
import type { FlowEdge, FlowLayer } from "./flowDiagram.types";

function byLayer(layout: FlowLayout): PlacedNode[][] {
  const layers: PlacedNode[][] = layout.columns.map(() => []);
  for (const placed of layout.nodes) layers[placed.layer]?.push(placed);
  return layers.map((nodes) => [...nodes].sort((a, b) => a.y - b.y));
}

function centre(layout: FlowLayout, id: string): number {
  const placed = layout.nodes.find((entry) => entry.node.id === id);
  if (!placed) throw new Error(`no node ${id}`);
  return placed.y + placed.height / 2;
}

describe("layoutFlow", () => {
  const layout = layoutFlow(PROGGEST_LAYERS, PROGGEST_EDGES);

  it("is deterministic: the same data gives the same coordinates", () => {
    expect(layoutFlow(structuredClone(PROGGEST_LAYERS) as FlowLayer[], structuredClone(PROGGEST_EDGES) as FlowEdge[])).toEqual(layout);
  });

  it("places every node of Proggest's site, in one column per layer", () => {
    const total = PROGGEST_LAYERS.reduce((sum, layer) => sum + layer.nodes.length, 0);
    expect(layout.nodes).toHaveLength(total);
    expect(layout.columns.map((column) => column.layer.id)).toEqual(["ports", "servers", "locations", "destinations", "backends"]);
    for (const [index, nodes] of byLayer(layout).entries()) {
      const column = layout.columns[index];
      for (const placed of nodes) expect(placed.x).toBe(column?.x);
    }
    const xs = layout.columns.map((column) => column.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b));
    expect(new Set(xs).size).toBe(xs.length);
  });

  it("never overlaps two nodes of a column, nor a caption with a node", () => {
    for (const nodes of byLayer(layout)) {
      for (let index = 1; index < nodes.length; index++) {
        const above = nodes[index - 1];
        const below = nodes[index];
        if (!above || !below) continue;
        expect(below.y).toBeGreaterThanOrEqual(above.y + above.height + 4);
      }
    }
    for (const caption of layout.captions) {
      const column = byLayer(layout)[caption.layer] ?? [];
      for (const placed of column) {
        const overlaps = caption.y < placed.y + placed.height && caption.y + caption.height > placed.y;
        expect(overlaps, `${caption.label} over ${placed.node.id}`).toBe(false);
      }
    }
  });

  it("keeps everything inside its box with integer coordinates", () => {
    for (const placed of layout.nodes) {
      expect(Number.isInteger(placed.x) && Number.isInteger(placed.y)).toBe(true);
      expect(placed.y).toBeGreaterThanOrEqual(0);
      expect(placed.x + placed.width).toBeLessThanOrEqual(layout.width);
      expect(placed.y + placed.height).toBeLessThanOrEqual(layout.height);
    }
  });

  it("keeps the caller's order inside a group and the groups together", () => {
    const flat = layoutFlow(
      [
        {
          id: "only",
          title: "Only",
          nodes: [
            { id: "a1", kind: "location", label: "/a1", group: "a" },
            { id: "b1", kind: "location", label: "/b1", group: "b" },
            { id: "a2", kind: "location", label: "/a2", group: "a" },
          ],
        },
      ],
      [],
    );
    const order = [...flat.nodes].sort((a, b) => a.y - b.y).map((placed) => placed.node.id);
    expect(order).toEqual(["a1", "a2", "b1"]);
    expect(flat.captions.map((caption) => caption.label)).toEqual(["a", "b"]);
  });

  it("puts a destination level with what leads to it", () => {
    const sources = PROGGEST_EDGES.filter((edge) => edge.to === "u:nestjs_upstream").map((edge) => centre(layout, edge.from));
    const nest = centre(layout, "u:nestjs_upstream");
    expect(nest).toBeGreaterThanOrEqual(Math.min(...sources));
    expect(nest).toBeLessThanOrEqual(Math.max(...sources));
    // A backend sits beside its upstream.
    expect(Math.abs(centre(layout, "b:3000") - nest)).toBeLessThan(1);
  });

  it("puts what fans out level with the first thing it opens, so it stays on the first screen", () => {
    expect(centre(layout, "s1")).toBe(centre(layout, "s1/l0"));
    expect(centre(layout, "p443")).toBe(centre(layout, "s1"));
    expect(centre(layout, "s0")).toBe(centre(layout, "s0/l0"));
  });

  it("draws one smooth curve per connection, from the right of one node to the left of the next", () => {
    const doubled = [...PROGGEST_EDGES, { from: "p80", to: "s0" }, { from: "p80", to: "nowhere" }];
    const result = layoutFlow(PROGGEST_LAYERS, doubled);
    expect(result.edges).toHaveLength(PROGGEST_EDGES.length);
    const first = result.edges.find((placed) => placed.id === edgeId({ from: "p80", to: "s0" }));
    const from = result.nodes.find((placed) => placed.node.id === "p80");
    const to = result.nodes.find((placed) => placed.node.id === "s0");
    if (!first || !from || !to) throw new Error("missing");
    expect(first.d).toMatch(/^M[\d.]+ [\d.]+ C[\d.]+ [\d.]+ [\d.]+ [\d.]+ [\d.]+ [\d.]+$/);
    const numbers = first.d.match(/[\d.]+/g)?.map(Number) ?? [];
    expect(numbers.slice(0, 2)).toEqual([from.x + from.width, from.y + from.height / 2]);
    expect(numbers.slice(-2)).toEqual([to.x, to.y + to.height / 2]);
  });
});

describe("connectedPath", () => {
  it("lights a location's whole way: port, server, destination and backend, and no sibling", () => {
    const path = connectedPath("s1/l2", PROGGEST_EDGES);
    expect([...path.nodes].sort()).toEqual(["b:3000", "p443", "s1", "s1/l2", "u:nestjs_upstream"].sort());
    expect(path.edges.has(edgeId({ from: "s1", to: "s1/l2" }))).toBe(true);
    expect(path.edges.has(edgeId({ from: "s1", to: "s1/l3" }))).toBe(false);
  });
});

describe("pathEdges", () => {
  it("returns the connections between consecutive elements of a route", () => {
    expect([...pathEdges(PROGGEST_LOGIN_ROUTE, PROGGEST_EDGES)]).toEqual([
      edgeId({ from: "p443", to: "s1" }),
      edgeId({ from: "s1", to: "s1/l2" }),
      edgeId({ from: "s1/l2", to: "u:nestjs_upstream" }),
      edgeId({ from: "u:nestjs_upstream", to: "b:3000" }),
    ]);
  });
});
