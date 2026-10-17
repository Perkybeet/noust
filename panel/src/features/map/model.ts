/**
 * The map as the diagram draws it: the server's layers and relations turned into the
 * `FlowDiagram`'s, with the words the console says them in. Pure, so every rule is tested without
 * rendering; no position is computed here, the diagram lays columns out from the order alone.
 */

import type { MachineMap, MapNode } from "../../api/queries/map";
import type { FlowEdge, FlowLayer, FlowNode, FlowNodeKind, FlowNodeState } from "../../components/ui/flowDiagram.types";
import { appStatus } from "../../components/page/status";
import type { T } from "../../i18n";

const KINDS: readonly FlowNodeKind[] = ["domain", "application", "database", "scheduled", "storage"];

function kindOf(kind: string): FlowNodeKind {
  return (KINDS as readonly string[]).includes(kind) ? (kind as FlowNodeKind) : "other";
}

function stateOf(state: string | null | undefined): FlowNodeState {
  return state === "ok" || state === "fail" || state === "unknown" ? state : "none";
}

/** The column heading of a layer. A layer a newer server adds is headed by the server's own id. */
function layerTitle(t: T, id: string): string {
  switch (id) {
    case "names":
      return t("map.layers.names");
    case "applications":
      return t("map.layers.applications");
    case "databases":
      return t("map.layers.databases");
    case "scheduled":
      return t("map.layers.scheduled");
    case "data":
      return t("map.layers.data");
    default:
      return id;
  }
}

/** The word for a relation. One a newer server adds is shown as the server named it. */
function relationWord(t: T, relation: string): string {
  switch (relation) {
    case "serves":
      return t("map.relation.serves");
    case "uses":
      return t("map.relation.uses");
    case "runs":
      return t("map.relation.runs");
    case "keeps":
      return t("map.relation.keeps");
    default:
      return relation;
  }
}

function flowNode(t: T, node: MapNode, typeName: (type: string) => string): FlowNode {
  const kind = kindOf(node.kind);
  const state = stateOf(node.state);
  // An application's state is said in the console's own words for it, not as "Responds".
  const stateLabel = kind === "application" && node.state_word ? appStatus(node.state_word, t.locale).label : kind === "scheduled" && node.state_word === "disabled" ? t("map.disabled") : undefined;
  const detail = kind === "application" && node.detail ? typeName(node.detail) : (node.detail ?? undefined);
  return {
    id: node.id,
    kind,
    label: node.label,
    ...(detail !== undefined ? { detail } : {}),
    ...(state !== "none" ? { state } : {}),
    ...(stateLabel !== undefined ? { stateLabel } : {}),
    ...(node.group ? { group: node.group } : {}),
  };
}

export interface Diagram {
  layers: FlowLayer[];
  edges: FlowEdge[];
  /** Every node by id, to open the thing it is. */
  nodes: ReadonlyMap<string, MapNode>;
}

/**
 * @param machine - What the server answered.
 * @param t - The translator.
 * @param typeName - An application type as a person reads it ("Next.js").
 */
export function diagramOf(machine: MachineMap, t: T, typeName: (type: string) => string): Diagram {
  return {
    layers: machine.layers.map((layer) => ({
      id: layer.id,
      title: layerTitle(t, layer.id),
      nodes: layer.nodes.map((node) => flowNode(t, node, typeName)),
    })),
    edges: machine.edges.map((edge) => ({ from: edge.source, to: edge.target, relation: relationWord(t, edge.relation) })),
    nodes: new Map(machine.layers.flatMap((layer) => layer.nodes.map((node) => [node.id, node] as const))),
  };
}

/** How many of each thing the map holds, for the sentence under the title. */
export function countsOf(machine: MachineMap): { applications: number; databases: number; scheduled: number } {
  const count = (kind: string): number => machine.layers.filter((layer) => layer.kind === kind).reduce((sum, layer) => sum + layer.nodes.length, 0);
  return { applications: count("application"), databases: count("database"), scheduled: count("scheduled") };
}
