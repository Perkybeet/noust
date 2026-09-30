/**
 * The approvals inbox beyond the raw API shape: its filters (kept in the URL), and whether a
 * request of another page, or of an earlier visit, can still be run from here.
 */

import type { HeldCall, Method } from "../../api/client";
import { nodeOfProxyPath } from "../../api/nodeScope";
import type { PlainKey, T } from "../../i18n";
import type { Approval, ApprovalState } from "./api";

/** Each approval rule (`src/noust/core/accounts/approvals.py`) in plain words, by its action. */
const ACTIONS: Readonly<Record<string, PlainKey>> = {
  root_equivalent: "approvals.actions.rootEquivalent",
  "fleet.node.add": "approvals.actions.fleetNodeAdd",
  "fleet.node.remove": "approvals.actions.fleetNodeRemove",
  "db.rows.write": "approvals.actions.dbRowsWrite",
  "db.query.write": "approvals.actions.dbQueryWrite",
  "db.query.analyze": "approvals.actions.dbQueryAnalyze",
  "apps.local_source": "approvals.actions.appsLocalSource",
  "user.role_change": "approvals.actions.userRoleChange",
  "user.create": "approvals.actions.userCreate",
  "user.invite": "approvals.actions.userInvite",
};

/**
 * What a request does, in the operator's words: by its rule's action, else by its kind, and only
 * for a rule this console has never heard of, the server's own sentence.
 */
export function approvalDescription(t: T, approval: Pick<Approval, "action" | "kind" | "description">): string {
  const key = ACTIONS[approval.action];
  if (key !== undefined) return t(key);
  if (approval.kind === "role_change") return t("approvals.actions.roleChange");
  if (approval.kind === "infrastructure") return t("approvals.actions.infrastructure");
  return approval.description;
}

export type ApprovalsView = "waiting" | "decided" | "all";

export interface ApprovalsSearch {
  view?: ApprovalsView;
  mine?: true;
}

export function validateApprovalsSearch(search: Record<string, unknown>): ApprovalsSearch {
  const view = search["view"] === "decided" || search["view"] === "all" || search["view"] === "waiting" ? search["view"] : undefined;
  return { ...(view !== undefined ? { view } : {}), ...(search["mine"] === true || search["mine"] === "true" ? { mine: true as const } : {}) };
}

/** Which requests a view shows: waiting ones by default, the ones already decided, or all. */
export function stateFor(view: ApprovalsView): ApprovalState | null {
  return view === "waiting" ? "requested" : null;
}

export function inView(approval: Pick<Approval, "state">, view: ApprovalsView): boolean {
  if (view === "all") return true;
  if (view === "waiting") return approval.state === "requested";
  return approval.state !== "requested";
}

/** The redaction marker the server writes where a secret was (noust.core.config.REDACTED). */
const REDACTED = "***";

function holdsRedaction(value: unknown): boolean {
  if (value === REDACTED) return true;
  if (Array.isArray(value)) return value.some(holdsRedaction);
  if (typeof value === "object" && value !== null) return Object.values(value).some(holdsRedaction);
  return false;
}

const METHODS: readonly Method[] = ["POST", "PUT", "PATCH", "DELETE"];

/**
 * The call a request allows, rebuilt from its snapshot, when the snapshot is the whole call: a
 * body with a secret in it was redacted and one too large was summarised, and those can only be
 * run again from where they started. The server checks the rebuilt call is the one approved.
 */
export function callFromSnapshot(approval: Pick<Approval, "method" | "path" | "parameters">): HeldCall | null {
  const method = METHODS.find((verb) => verb === approval.method.toUpperCase());
  if (method === undefined) return null;
  const parameters = approval.parameters as { query?: unknown; body?: unknown };
  const body = parameters.body;
  if (typeof body === "object" && body !== null && !Array.isArray(body) && (body as { shown?: unknown }).shown === false) return null;
  if (holdsRedaction(body)) return null;
  const pairs = Array.isArray(parameters.query) ? (parameters.query as unknown[]) : [];
  const search = new URLSearchParams();
  for (const pair of pairs) {
    if (Array.isArray(pair) && pair.length === 2) search.append(String(pair[0]), String(pair[1]));
  }
  const query = search.toString();
  const target = query === "" ? approval.path : `${approval.path}?${query}`;
  return { method, target, body: body === null ? undefined : body, node: nodeOfProxyPath(approval.path) };
}
