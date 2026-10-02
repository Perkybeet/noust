/**
 * Where a `FlowDiagram` puts things. The data is not an arbitrary graph but fixed layers from
 * left to right, so the layout is columns: no force simulation, no randomness, the same data
 * always at the same coordinates (a reload, a refetch or a new edit never shuffles the
 * picture). Pure functions, tested on their own.
 */

import type { FlowEdge, FlowLayer, FlowNode } from "./flowDiagram.types";

export interface FlowLayoutOptions {
  nodeWidth?: number;
  nodeHeight?: number;
  /** Room between two columns, where the curves run. */
  columnGap?: number;
  /** Between two nodes of the same group. */
  rowGap?: number;
  /** Between the last node of a group and the next group's caption. */
  groupGap?: number;
  captionHeight?: number;
}

export interface PlacedNode {
  node: FlowNode;
  layer: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PlacedCaption {
  layer: number;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface PlacedEdge {
  id: string;
  edge: FlowEdge;
  /** An SVG path: one cubic curve. */
  d: string;
}

export interface PlacedColumn {
  layer: FlowLayer;
  x: number;
  width: number;
}

export interface FlowLayout {
  nodes: PlacedNode[];
  edges: PlacedEdge[];
  captions: PlacedCaption[];
  columns: PlacedColumn[];
  width: number;
  height: number;
}

const DEFAULTS: Required<FlowLayoutOptions> = {
  nodeWidth: 200,
  nodeHeight: 48,
  columnGap: 72,
  rowGap: 8,
  groupGap: 16,
  captionHeight: 24,
};

/** One id per connection, whatever else the edge says. */
export function edgeId(edge: Pick<FlowEdge, "from" | "to">): string {
  return `${edge.from}->${edge.to}`;
}

/** The layer's nodes with each group together, groups in the order they first appear. */
function grouped(nodes: readonly FlowNode[]): FlowNode[] {
  const order: (string | undefined)[] = [];
  const members = new Map<string | undefined, FlowNode[]>();
  for (const node of nodes) {
    const list = members.get(node.group);
    if (list) list.push(node);
    else {
      order.push(node.group);
      members.set(node.group, [node]);
    }
  }
  return order.flatMap((group) => members.get(group) ?? []);
}

/**
 * The least distance from the node before (or, for the first, from the top): a row gap inside
 * a group, a group gap and a caption where a new group starts.
 */
function spacings(nodes: readonly FlowNode[], o: Required<FlowLayoutOptions>): number[] {
  return nodes.map((node, index) => {
    const previous = nodes[index - 1];
    if (!previous) return node.group !== undefined ? o.captionHeight : 0;
    const newGroup = node.group !== previous.group && node.group !== undefined;
    return o.nodeHeight + (newGroup ? o.groupGap + o.captionHeight : o.rowGap);
  });
}

/** Each node at its least distance from the one before. */
function stacked(space: readonly number[]): number[] {
  const ys: number[] = [];
  for (const [index, step] of space.entries()) ys.push((ys[index - 1] ?? 0) + step);
  return ys;
}

/**
 * Positions as close as possible to the wanted ones (least squares) that keep the order and the
 * spacing: isotonic regression by pooling adjacent violators on `wanted - cumulative spacing`.
 */
function settle(wanted: readonly number[], space: readonly number[]): number[] {
  const offsets: number[] = [];
  let sum = 0;
  for (const step of space) {
    sum += step;
    offsets.push(sum);
  }
  const blocks: { total: number; count: number }[] = [];
  wanted.forEach((value, index) => {
    blocks.push({ total: value - (offsets[index] ?? 0), count: 1 });
    for (;;) {
      const last = blocks[blocks.length - 1];
      const before = blocks[blocks.length - 2];
      if (!last || !before || before.total / before.count <= last.total / last.count) break;
      blocks.pop();
      before.total += last.total;
      before.count += last.count;
    }
  });
  const levels = blocks.flatMap((block) => Array<number>(block.count).fill(block.total / block.count));
  const placed: number[] = [];
  levels.forEach((level, index) => {
    const step = space[index] ?? 0;
    const floor = index === 0 ? step : (placed[index - 1] ?? 0) + step;
    placed.push(Math.max(Math.round(level + (offsets[index] ?? 0)), floor));
  });
  return placed;
}

function curve(from: PlacedNode, to: PlacedNode): string {
  const y1 = from.y + from.height / 2;
  const y2 = to.y + to.height / 2;
  if (to.layer > from.layer) {
    const x1 = from.x + from.width;
    const x2 = to.x;
    const bend = (x2 - x1) / 2;
    return `M${String(x1)} ${String(y1)} C${String(x1 + bend)} ${String(y1)} ${String(x2 - bend)} ${String(y2)} ${String(x2)} ${String(y2)}`;
  }
  // Within a column or backwards: a loop out of the right side and back into it.
  const x1 = from.x + from.width;
  const x2 = to.x + to.width;
  return `M${String(x1)} ${String(y1)} C${String(x1 + 40)} ${String(y1)} ${String(x2 + 40)} ${String(y2)} ${String(x2)} ${String(y2)}`;
}

/**
 * Lays the layers out in columns. The tallest column is stacked from the top; every other
 * column, moving outwards from it, puts each node level with what it connects to in the columns
 * already placed (a server beside its first location, a destination beside the middle of its
 * locations, a port beside its server), then settles them so nothing overlaps and the caller's order is kept. Edges whose ends are
 * not in any layer are left out; a repeated connection is drawn once.
 */
export function layoutFlow(layers: readonly FlowLayer[], edges: readonly FlowEdge[], options: FlowLayoutOptions = {}): FlowLayout {
  const o = { ...DEFAULTS, ...options };
  const ordered = layers.map((layer) => grouped(layer.nodes));
  const space = ordered.map((nodes) => spacings(nodes, o));
  const layerOf = new Map<string, number>();
  ordered.forEach((nodes, layer) => {
    for (const node of nodes) if (!layerOf.has(node.id)) layerOf.set(node.id, layer);
  });

  const neighbours = new Map<string, string[]>();
  const link = (a: string, b: string): void => {
    const list = neighbours.get(a);
    if (list) list.push(b);
    else neighbours.set(a, [b]);
  };
  for (const edge of edges) {
    if (!layerOf.has(edge.from) || !layerOf.has(edge.to)) continue;
    link(edge.from, edge.to);
    link(edge.to, edge.from);
  }

  const heights = space.map((steps) => steps.reduce((sum, step) => sum + step, 0) + (steps.length > 0 ? o.nodeHeight : 0));
  const anchor = heights.reduce((best, height, index) => (height > (heights[best] ?? 0) ? index : best), 0);

  const centres = new Map<string, number>();
  const tops: number[][] = ordered.map(() => []);

  const place = (layer: number): void => {
    const nodes = ordered[layer] ?? [];
    const steps = space[layer] ?? [];
    const wanted: number[] = [];
    nodes.forEach((node, index) => {
      const known = (neighbours.get(node.id) ?? []).map((id) => centres.get(id)).filter((value): value is number => value !== undefined);
      const fallback = index === 0 ? (steps[0] ?? 0) : (wanted[index - 1] ?? 0) + (steps[index] ?? 0);
      if (known.length === 0) wanted.push(fallback);
      // Left of the tallest column things fan out (a server opens its locations): level with
      // the first one, like a heading over its group, so a server of 25 locations is not
      // drawn a screen below its caption. Right of it things fan in (locations into an
      // upstream): level with the middle of what leads there.
      else if (layer < anchor) wanted.push(Math.min(...known) - o.nodeHeight / 2);
      else wanted.push(known.reduce((sum, value) => sum + value, 0) / known.length - o.nodeHeight / 2);
    });
    // The tallest column is simply stacked: it is what the others line up against.
    const ys = layer === anchor ? stacked(steps) : settle(wanted, steps);
    tops[layer] = ys;
    nodes.forEach((node, index) => centres.set(node.id, (ys[index] ?? 0) + o.nodeHeight / 2));
  };

  place(anchor);
  for (let layer = anchor + 1; layer < ordered.length; layer++) place(layer);
  for (let layer = anchor - 1; layer >= 0; layer--) place(layer);

  const pitch = o.nodeWidth + o.columnGap;
  const nodes: PlacedNode[] = [];
  const captions: PlacedCaption[] = [];
  const byId = new Map<string, PlacedNode>();
  ordered.forEach((list, layer) => {
    const x = layer * pitch;
    list.forEach((node, index) => {
      const y = tops[layer]?.[index] ?? 0;
      if (node.group !== undefined && node.group !== list[index - 1]?.group) {
        captions.push({ layer, label: node.group, x, y: y - o.captionHeight, width: o.nodeWidth, height: o.captionHeight });
      }
      if (byId.has(node.id)) return;
      const placed = { node, layer, x, y, width: o.nodeWidth, height: o.nodeHeight };
      nodes.push(placed);
      byId.set(node.id, placed);
    });
  });

  const seen = new Set<string>();
  const placedEdges: PlacedEdge[] = [];
  for (const edge of edges) {
    const from = byId.get(edge.from);
    const to = byId.get(edge.to);
    const id = edgeId(edge);
    if (!from || !to || seen.has(id)) continue;
    seen.add(id);
    placedEdges.push({ id, edge, d: curve(from, to) });
  }

  const bottom = nodes.reduce((max, placed) => Math.max(max, placed.y + placed.height), 0);
  return {
    nodes,
    edges: placedEdges,
    captions,
    columns: layers.map((layer, index) => ({ layer, x: index * pitch, width: o.nodeWidth })),
    width: layers.length > 0 ? layers.length * pitch - o.columnGap : 0,
    height: bottom,
  };
}

/**
 * Everything a request through a node passes: what leads to it, all the way back, and where it
 * goes, all the way on. Siblings (another location of the same server) are not on its path.
 */
export function connectedPath(nodeId: string, edges: readonly FlowEdge[]): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([nodeId]);
  const lit = new Set<string>();
  const walk = (start: string, downstream: boolean): void => {
    const queue = [start];
    const visited = new Set<string>([start]);
    for (let current = queue.shift(); current !== undefined; current = queue.shift()) {
      for (const edge of edges) {
        if ((downstream ? edge.from : edge.to) !== current) continue;
        const other = downstream ? edge.to : edge.from;
        lit.add(edgeId(edge));
        nodes.add(other);
        if (!visited.has(other)) {
          visited.add(other);
          queue.push(other);
        }
      }
    }
  };
  walk(nodeId, true);
  walk(nodeId, false);
  return { nodes, edges: lit };
}

/** The connections between consecutive elements of a route, in its order. */
export function pathEdges(route: readonly string[], edges: readonly FlowEdge[]): Set<string> {
  const lit = new Set<string>();
  for (let index = 1; index < route.length; index++) {
    const from = route[index - 1];
    const to = route[index];
    const edge = edges.find((candidate) => candidate.from === from && candidate.to === to);
    if (edge) lit.add(edgeId(edge));
  }
  return lit;
}
