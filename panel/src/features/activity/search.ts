/**
 * The Activity page's search parameters, in a module of their own: a route's options are loaded
 * with the first page, so the address must be read without importing the page's model (its
 * words, its merge, the server's messages) with it.
 */

/** `JobStatus`. */
export const JOB_STATUSES: ReadonlySet<string> = new Set(["pending", "running", "completed", "failed", "cancelled"]);
/** The `result` values the audit log writes. */
export const AUDIT_RESULTS: ReadonlySet<string> = new Set(["success", "ok", "denied", "failure", "locked"]);

export interface ActivitySearch {
  /** The view: every change when absent; only the deployments, sign-ins and access, or everything. */
  kind?: "deployments" | "access" | "all";
  /** A job status or an audit result, whichever the view allows. */
  result?: string;
  /** Free text, matched against what each row shows and what the backend recorded. */
  q?: string;
  /** The exact actor value: `master`, `token:<name>`, `webhook`, `anonymous` or a session id. */
  actor?: string;
  /** What one person did, by the name of their account. */
  person?: string;
  /** Only this server's events, on a central that has servers (`@central` for the central itself). */
  server?: string;
}

/** Whether a result value means anything in a view: sign-ins have no jobs, so no job status. */
export function resultValidFor(result: string, kind: ActivitySearch["kind"]): boolean {
  if (kind === "access") return AUDIT_RESULTS.has(result);
  return JOB_STATUSES.has(result) || AUDIT_RESULTS.has(result);
}

function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed === "" ? undefined : trimmed.slice(0, 200);
}

/**
 * The page's search params. 3.0's `kind=jobs` and `kind=audit` still open: jobs are the
 * operations view now, and the audit log alone is everything.
 */
export function validateActivitySearch(search: Record<string, unknown>): ActivitySearch {
  const kindRaw = text(search["kind"]);
  const kind = kindRaw === "deployments" || kindRaw === "access" || kindRaw === "all" ? kindRaw : kindRaw === "audit" ? "all" : undefined;
  const resultRaw = text(search["result"]);
  const result = resultRaw !== undefined && resultValidFor(resultRaw, kind) ? resultRaw : undefined;
  const q = text(search["q"]);
  const actor = text(search["actor"]);
  const person = text(search["person"]);
  const server = text(search["server"]);
  return {
    ...(kind !== undefined ? { kind } : {}),
    ...(result !== undefined ? { result } : {}),
    ...(q !== undefined ? { q } : {}),
    ...(actor !== undefined ? { actor } : {}),
    ...(person !== undefined ? { person } : {}),
    ...(server !== undefined ? { server } : {}),
  };
}

export function isFiltered(search: ActivitySearch): boolean {
  return search.kind !== undefined || search.result !== undefined || search.q !== undefined || search.actor !== undefined || search.person !== undefined || search.server !== undefined;
}
