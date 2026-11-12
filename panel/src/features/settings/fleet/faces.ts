/**
 * What a setting of the fleet says about each server, as a page needs it and without drawing anything: the one word a
 * server holds a setting by (its face), the line "N of M servers follow it", what saving a value would do on each
 * server, the conditions that stand in the way of a whole page, and a server's file over every setting.
 *
 * The order of the checks is the point. A server that is independent is pushed nothing, so nothing else about how a
 * push would go (its Noust, its ceiling) is asked of it. What a push would meet comes next, then whether the server
 * answers, and only then what it holds. Independent, and a ceiling that does not let the central change settings, are
 * decisions of an operator and are drawn so: never as a failure.
 */

import type { Cell, StatusServer, StatusSetting } from "./data";

export type Face =
  /** Holds the fleet's value. */
  | "follows"
  /** Has a value of its own: the central gave it one, or the server keeps what it had. */
  | "exception"
  /** Somebody changed it on the server itself: the server rules on its own machine. */
  | "changedHere"
  /** The server answers and the change is not there yet. */
  | "pending"
  /** The server does not answer. */
  | "off"
  /** The central pushes it nothing, by choice. */
  | "independent"
  /** Its ceiling does not let the central change settings. */
  | "refused"
  /** Its Noust does not know this setting. */
  | "unsupported"
  /** The setting's route can match nothing on the server, so nothing was sent. */
  | "notSent"
  /** The fleet has nothing for this server. */
  | "none"
  /** The server could not be read. */
  | "unknown";

/**
 * Whether the central holds for this server a version newer than the one it applied, whether it answers or not. The
 * central says it (`waiting`); a central of 4.2 does not, and the two versions it has always sent say the same.
 */
export function isWaiting(cell: Cell): boolean {
  if (cell.waiting !== undefined) return cell.waiting;
  if (cell.wanted_version === null || cell.wanted_version === undefined) return false;
  return cell.applied_version === null || cell.applied_version === undefined || cell.applied_version < cell.wanted_version;
}

/** How a server holds a setting. */
export function faceOf(cell: Cell): Face {
  if (cell.origin === "independent") return "independent";
  if (cell.sync === "unsupported") return "unsupported";
  if (cell.sync === "refused") return "refused";
  if (cell.sync === "unreachable") return "off";
  if (cell.reach !== null && cell.reach !== undefined && !cell.reach.reaches) return "notSent";
  if (cell.origin === "none" || cell.sync === "not_applicable") return "none";
  if (cell.origin === "local_exception") return cell.sync === "diverged" || cell.restore_requested === true ? "changedHere" : "exception";
  if (cell.sync === "diverged") return "changedHere";
  // A value the central set for this server alone is said as that, whether it has arrived or not: the mark is its own.
  if (cell.origin === "central_exception" && cell.sync !== "unknown") return "exception";
  if (cell.sync === "pending" || cell.sync === "release_pending") return "pending";
  if (cell.sync === "unknown") return "unknown";
  return "follows";
}

/** The line "N of M servers follow it" before it is said: which of the four sentences it is, and its numbers. */
export type FollowWords = { kind: "single" } | { kind: "all"; of: number } | { kind: "some"; following: number; of: number } | { kind: "one"; of: number } | { kind: "none" };

/**
 * How many servers follow a setting, in words. The count is the server's (`following`, `of`): it is told once, by
 * the one that knows, and the console does not calculate it again.
 */
export function followWords(following: number, of: number): FollowWords {
  // A fleet of one server says it as that: "all 1 servers" is not a sentence.
  if (of === 1) return following >= 1 ? { kind: "single" } : { kind: "none" };
  if (of > 0 && following >= of) return { kind: "all", of };
  if (following <= 0) return { kind: "none" };
  if (following === 1) return { kind: "one", of };
  return { kind: "some", following, of };
}

/** What a mark on a row names: a server and what is true of it for this setting. */
export interface Mark {
  node: string;
  face: Face;
  cell: Cell;
}

/**
 * What is true of this setting on one server and is worth a mark on its row: a value of its own, a change made on the
 * server, a change that has not arrived and one that waits for a server that does not answer. What is true of every
 * setting on a server (independent, ceiling, version) is said once for the page (`conditionsOf`), not on each row.
 */
export function marksOf(row: StatusSetting): Mark[] {
  return row.servers.flatMap((cell): Mark[] => {
    const face = faceOf(cell);
    const worth = face === "exception" || face === "changedHere" || face === "pending" || (face === "off" && isWaiting(cell));
    return worth ? [{ node: cell.node, face, cell }] : [];
  });
}

/** What saving a value of the fleet does on one server. */
export type Does = "applies" | "waits" | "keepsOwn" | "skips" | "refused" | "unsupported";

export interface SavePlan {
  rows: { node: string; does: Does }[];
  /** Servers that get it now. */
  now: number;
  /** Servers that get it when they are back. */
  later: number;
}

/** What saving the value of the fleet does on each server: the plan the dialog shows before it is saved. */
export function savePlan(row: StatusSetting): SavePlan {
  const rows = row.servers.map((cell): { node: string; does: Does } => {
    switch (faceOf(cell)) {
      case "follows":
      case "pending":
        return { node: cell.node, does: "applies" };
      case "off":
        return { node: cell.node, does: "waits" };
      case "exception":
      case "changedHere":
        return { node: cell.node, does: "keepsOwn" };
      case "refused":
        return { node: cell.node, does: "refused" };
      case "unsupported":
        return { node: cell.node, does: "unsupported" };
      default:
        return { node: cell.node, does: "skips" };
    }
  });
  return { rows, now: rows.filter((entry) => entry.does === "applies").length, later: rows.filter((entry) => entry.does === "waits").length };
}

/** A server whose condition stands in the way of every setting of a page. */
export interface Condition {
  node: string;
  face: Extract<Face, "off" | "independent" | "refused" | "unsupported">;
}

/**
 * What stands in the way of the settings of a page on a server, whatever the setting: it is independent, its Noust
 * is too old, its ceiling is low or it does not answer. Said once for the page. The central is not among the servers
 * the status lists, and a lone server has none: nothing is said where there is nobody to tell apart.
 */
export function conditionsOf(servers: readonly StatusServer[]): Condition[] {
  return servers.flatMap((server): Condition[] => {
    if (server.mode === "independent") return [{ node: server.node, face: "independent" }];
    if (server.outcome === "unsupported") return [{ node: server.node, face: "unsupported" }];
    if (server.outcome === "forbidden") return [{ node: server.node, face: "refused" }];
    if (server.outcome === "unreachable" || server.outcome === "stale" || server.outcome === "error") return [{ node: server.node, face: "off" }];
    return [];
  });
}

export interface ServerSummary {
  total: number;
  follows: number;
  /** Values of its own (the central's exceptions). */
  exceptions: number;
  /** Values changed on the server itself. */
  changedHere: number;
  /** Changes that have not arrived. */
  waiting: number;
}

const cellOn = (row: StatusSetting, node: string): Cell | undefined => row.servers.find((cell) => cell.node === node);

/** A server's file over the settings of the fleet: how many it follows and how many it holds by itself. */
export function serverSummary(node: string, rows: readonly StatusSetting[]): ServerSummary {
  const cells = rows.flatMap((row) => {
    const cell = cellOn(row, node);
    return cell === undefined ? [] : [cell];
  });
  const faces = cells.map(faceOf);
  const exceptions = faces.filter((face) => face === "exception").length;
  const changedHere = faces.filter((face) => face === "changedHere").length;
  return {
    total: cells.length,
    follows: cells.length - exceptions - changedHere,
    exceptions,
    changedHere,
    waiting: cells.filter(isWaiting).length,
  };
}

export interface ReturnPlan {
  total: number;
  /** Settings the fleet changed while the server was apart: it is given the new value. */
  receives: number;
  /** Already what the fleet says. */
  same: number;
}

/**
 * What happens when an independent server follows the fleet again: it is given what changed in the meantime. The central
 * says it for each setting (`if_followed`, "pending" is what would be sent); a central that does not (4.2) leaves it to the
 * version the independent server still reports.
 */
export function returnPlan(node: string, rows: readonly StatusSetting[]): ReturnPlan {
  let receives = 0;
  let same = 0;
  let total = 0;
  for (const row of rows) {
    const cell = cellOn(row, node);
    if (cell === undefined) continue;
    total += 1;
    const sent = cell.if_followed !== undefined && cell.if_followed !== null ? cell.if_followed === "pending" : cell.applied_version !== row.version;
    if (sent) receives += 1;
    else same += 1;
  }
  return { total, receives, same };
}
