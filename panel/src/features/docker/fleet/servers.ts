/**
 * How each server answered a view of the fleet's Docker, in the kit's words: fresh and whole, late (the last it gave, and how old it is),
 * without a connection, or running a Noust that does not count Docker (before 4.3: a sentence, not a fault, and its version). The central
 * answers this itself, in `nodes` of every view; a refusal or an error is not one of these, it keeps its own notice.
 */

import type { FleetServer, ServerAnswer } from "../../../components/page/fleetServers";
import type { DockerView } from "../../../api/queries/dockerFleet";

type Node = DockerView["nodes"][number];

/** One node of a view as the kit's piece names it. */
export function answerOfNode(node: Pick<Node, "status" | "age_seconds" | "version" | "fetched_at">, now = Date.now()): ServerAnswer {
  switch (node.status) {
    case "stale": {
      const since = node.fetched_at !== null && node.fetched_at !== undefined ? new Date(node.fetched_at) : node.age_seconds !== null && node.age_seconds !== undefined ? new Date(now - node.age_seconds * 1000) : null;
      return { kind: "stale", since };
    }
    case "unreachable":
      return { kind: "unreachable" };
    case "unsupported":
      return { kind: "unsupported", version: node.version ?? null };
    default:
      return { kind: "fresh" };
  }
}

/** The servers of a view, each with how it answered, the central marked. */
export function fleetServersOf(view: Pick<DockerView, "nodes"> | undefined): FleetServer[] {
  return (view?.nodes ?? []).map((node) => ({ name: node.name, ...(node.local ? { central: true } : {}), answer: answerOfNode(node) }));
}
