/**
 * The API's error contract, as the console receives it.
 *
 * Every route under /api answers a failure as `{error, detail, hint, fields, output}` (the
 * backend's `noust.web.api.deps.ErrorResponse`). `error` is the machine-readable code the
 * console branches on; `detail` is the system's own words and is shown verbatim; `hint` is
 * the fix, shown above it; `fields` maps a form field to its validation message; `output` is
 * a failing tool's own output verbatim (a rejected web server configuration, for example),
 * present only for the errors that carry one.
 */

import { getLocale } from "../app/locale";
import { translate } from "../i18n/translate";

/**
 * 401 codes that mean "these credentials were wrong", as opposed to "there is no session".
 * The login and elevate endpoints answer them; a 401 carrying anything else means the
 * session is gone and the operator has to sign in again.
 */
export const CREDENTIAL_ERRORS: ReadonlySet<string> = new Set([
  "invalid_token",
  "totp_required",
  "invalid_totp",
  // An account's sign-in or confirmation: one answer whatever was wrong (G10).
  "invalid_credentials",
  // The master token's second factor, when a passkey is (or may be) that factor.
  "passkey_required",
  "second_factor_required",
  // A passkey ceremony the server refused (noust.web.api.passkeys).
  "invalid_passkey",
  "passkey_unknown",
  "passkey_expired",
  "passkey_origin",
  // An invitation code that opens nothing: the invitation page is anonymous.
  "invalid_invitation",
]);

/** A request the API refused or could not answer. */
export class ApiError extends Error {
  /** HTTP status; 0 when the server could not be reached at all. */
  readonly status: number;
  /** Machine-readable code: `validation_error`, `elevation_required`, `locked_out`... */
  readonly error: string;
  /** What went wrong, in the system's own words. Never paraphrase it. */
  readonly detail: string;
  /** How to fix it, when the backend knows. */
  readonly hint: string | null;
  /** Validation message per field name, for a 422. */
  readonly fields: Readonly<Record<string, string>> | null;
  /** Seconds the server asked the client to wait (Retry-After), for a 429. */
  readonly retryAfter: number | null;
  /** A failing tool's own output, verbatim, when the error carries one. Never paraphrase it. */
  readonly output: string | null;
  /** The server that answered: a node's name, or null for this one. */
  readonly node: string | null;
  /**
   * Members of the body beyond the contract, verbatim: a deliberate refusal's `required`,
   * `blockers` or `holders` (409 confirmation_required, preflight_failed, host_busy).
   */
  readonly extra: Readonly<Record<string, unknown>>;

  constructor(
    status: number,
    error: string,
    detail: string,
    hint: string | null = null,
    fields: Record<string, string> | null = null,
    retryAfter: number | null = null,
    output: string | null = null,
    node: string | null = null,
    extra: Readonly<Record<string, unknown>> = {},
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.error = error;
    this.detail = detail;
    this.hint = hint;
    this.fields = fields;
    this.retryAfter = retryAfter;
    this.output = output;
    this.node = node;
    this.extra = extra;
  }

  /** True when the session is gone, not when a typed credential was wrong. */
  get sessionExpired(): boolean {
    return this.status === 401 && !CREDENTIAL_ERRORS.has(this.error);
  }
}

/** The operator closed "Confirm it's you" instead of confirming: the action did not run. */
export class ElevationCancelledError extends ApiError {
  constructor(code = "elevation_cancelled", detail?: string, hint?: string) {
    const locale = getLocale();
    super(
      403,
      code,
      detail ?? translate(locale, "common.apiErrors.elevationCancelled"),
      hint ?? translate(locale, "common.apiErrors.elevationCancelledHint"),
    );
    this.name = "ElevationCancelledError";
  }
}

/** Why an action that needs a second person's approval did not run (yet). */
export type ApprovalHold = "approval_pending" | "approval_cancelled" | "approval_rejected" | "approval_expired";

/**
 * The action needs a second person's approval and did not run: the operator closed the
 * request while it waited (it stays in Approvals, runnable once approved), declined to ask,
 * or the request was rejected or expired. Like a cancelled confirmation it is not a failure
 * of the system, so every caller that already treats ElevationCancelledError as "nothing was
 * done" treats this the same way.
 */
export class ApprovalPendingError extends ElevationCancelledError {
  /** The approval request, when one was filed. */
  readonly approvalId: string | null;

  constructor(code: ApprovalHold, approvalId: string | null = null, detail?: string) {
    const locale = getLocale();
    super(
      code,
      detail ?? translate(locale, `approvals.errors.${code}.detail`),
      translate(locale, `approvals.errors.${code}.hint`),
    );
    this.name = "ApprovalPendingError";
    this.approvalId = approvalId;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

/**
 * The central's answers for a node it could not use (502): its tunnel did not answer, or the
 * node refused the central's fleet token. The error's `detail` and `output` are ssh's or the
 * node's own words.
 */
export const NODE_ERRORS: ReadonlySet<string> = new Set(["node_unreachable", "node_refused"]);

/** True when a request failed because the central could not use the node, not because the node said no. */
export function isNodeError(value: unknown): value is ApiError {
  return value instanceof ApiError && NODE_ERRORS.has(value.error);
}

/** The fallback code for a response that did not carry one, mirroring the backend's table. */
const CODE_BY_STATUS: Readonly<Record<number, string>> = {
  400: "validation_error",
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  422: "validation_error",
  429: "rate_limited",
};

/** The members every error body has; anything else is kept in `ApiError.extra`. */
const CONTRACT_MEMBERS: ReadonlySet<string> = new Set(["error", "detail", "hint", "fields", "output"]);

function codeFor(status: number): string {
  return CODE_BY_STATUS[status] ?? (status >= 500 ? "internal" : "http_error");
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function fieldMap(value: unknown): Record<string, string> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const entries = Object.entries(value as Record<string, unknown>).filter(
    (entry): entry is [string, string] => typeof entry[1] === "string",
  );
  return entries.length > 0 ? Object.fromEntries(entries) : null;
}

function retryAfterSeconds(response: Response): number | null {
  const header = response.headers.get("Retry-After");
  if (header === null) return null;
  const seconds = Number.parseInt(header, 10);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

/**
 * Builds an ApiError from a failed response. Anything that is not the contract (a proxy's
 * HTML error page, an empty 502) is still reported with its body verbatim, so the operator
 * sees what the proxy said instead of a generic message.
 */
export async function errorFromResponse(response: Response, node: string | null = null): Promise<ApiError> {
  const text = await response.text().catch(() => "");
  const retryAfter = retryAfterSeconds(response);
  let body: unknown;
  try {
    body = text === "" ? null : JSON.parse(text);
  } catch {
    body = null;
  }

  if (typeof body === "object" && body !== null && !Array.isArray(body)) {
    const record = body as Record<string, unknown>;
    // FastAPI's own shape outside the contract: {"detail": "..."} or a list of problems.
    const detail =
      stringOrNull(record["detail"]) ??
      (record["detail"] !== undefined ? JSON.stringify(record["detail"]) : null) ??
      `${String(response.status)} ${response.statusText}`.trim();
    return new ApiError(
      response.status,
      stringOrNull(record["error"]) ?? codeFor(response.status),
      detail,
      stringOrNull(record["hint"]),
      fieldMap(record["fields"]),
      retryAfter,
      stringOrNull(record["output"]),
      node,
      Object.fromEntries(Object.entries(record).filter(([key]) => !CONTRACT_MEMBERS.has(key))),
    );
  }

  const detail = text.trim() !== "" ? text.trim() : `${String(response.status)} ${response.statusText}`.trim();
  return new ApiError(response.status, codeFor(response.status), detail, null, null, retryAfter, null, node);
}

/** The server did not answer at all: it is down, restarting, or the network is gone. */
export function unreachable(cause: unknown): ApiError {
  const locale = getLocale();
  // The browser's own words for what failed, when it has any, are what the operator needs.
  const detail = cause instanceof Error && cause.message !== "" ? cause.message : translate(locale, "common.apiErrors.unreachable");
  return new ApiError(0, "network", detail, translate(locale, "common.apiErrors.unreachableHint"));
}
