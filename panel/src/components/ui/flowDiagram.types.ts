/**
 * The data a `FlowDiagram` draws: fixed layers from left to right (ports, server blocks,
 * locations, destinations, backends), the elements in each, and what connects to what. The
 * caller decides the layers and the order inside each; the diagram never reorders.
 */

/** What an element is. Each kind has its own outline and icon, so it is not told by colour. */
export type FlowNodeKind = "listener" | "server" | "location" | "upstream" | "upstream-server" | "static" | "redirect" | "other";

/**
 * Whether an element answers: `ok` responds (green), `fail` does not (red), `unknown` was not
 * reached (grey), `none` is not something that can be checked (nothing is drawn).
 */
export type FlowNodeState = "ok" | "fail" | "unknown" | "none";

export interface FlowNode {
  /** Stable across renders: the analyser's id (`"s0/l3"`, `"u:nestjs_upstream"`). */
  id: string;
  kind: FlowNodeKind;
  /** The system value, in mono: `443 · TLS`, `/api/`, `nestjs_upstream`, `127.0.0.1:3000`. */
  label: string;
  /** A second line in words: "Next.js · frontend service of proggest.es". */
  detail?: string;
  /** Defaults to `none`. */
  state?: FlowNodeState;
  /** Replaces the state's default word when the context says more ("Expires in 5 days"). */
  stateLabel?: string;
  /**
   * Keeps elements of a layer together under a caption, in the order groups first appear:
   * the locations of each server block, the destinations of each upstream.
   */
  group?: string;
}

export interface FlowLayer {
  id: string;
  /** The column's heading: "Ports", "Locations". */
  title: string;
  nodes: readonly FlowNode[];
}

export interface FlowEdge {
  from: string;
  to: string;
  /** Encrypted: its dashes move faster. */
  tls?: boolean;
  /** A WebSocket upgrade: drawn as a double line. */
  websocket?: boolean;
}
