/**
 * The one way the console talks to the API.
 *
 * `api()` is the transport: same-origin cookies, JSON in and out, the CSRF header mirrored
 * from its cookie, and the answers that are not the caller's business handled here once: a
 * lost session sends the operator to sign in, a destructive action asks them to confirm it's
 * them and is retried once, a rate limit on a read waits out the server's own Retry-After and
 * is retried once (a write surfaces it instead, since repeating it is not free), a call that
 * needs a second person's approval (202 approval_required) waits for the decision and is sent
 * again once under it, and every other failure becomes an ApiError.
 *
 * On a central with a node selected, every call goes to that node through the central's proxy
 * (`/api/apps` becomes `/api/nodes/{node}/api/apps`); `nodeScope.ts` decides which server and
 * which path, here and nowhere else. Elevation is the central's: its proxy answers 403
 * elevation_required for a node's elevated operation, "Confirm it's you" elevates the
 * central's session (`/api/auth` is never forwarded), and the retry goes to the node again.
 *
 * `request()` is the same call typed by the OpenAPI contract (schema.gen.ts): a path that
 * does not exist, a missing path parameter or a wrong body is a compile error, and the
 * response is typed. Endpoints that still answer a bare dict are typed `unknown`; when the
 * backend declares their response model, the generated types sharpen with no change here.
 */

import { getLocale } from "../app/locale";
import { toast } from "../components/ui/toast";
import { translate } from "../i18n/translate";
import type { Locale } from "../i18n/types";
import type { paths } from "./schema.gen";
import { ApprovalPendingError, ElevationCancelledError, errorFromResponse, unreachable } from "./errors";
import type { ApiError } from "./errors";
import { activeNode, nodeApiPath, nodeOfProxyPath, resetNodeScope } from "./nodeScope";

export { ApiError, ApprovalPendingError, ElevationCancelledError, isApiError, isNodeError } from "./errors";

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** Names the approved request a call is sent again under (noust.web.api.approvals). */
export const APPROVAL_HEADER = "X-Noust-Approval";
/** Names the request a 202 approval_required created. */
export const APPROVAL_REQUEST_HEADER = "X-Noust-Approval-Request";
/** Why the call is made, percent-encoded UTF-8. */
export const REASON_HEADER = "X-Noust-Reason";

/**
 * A call held for a second person's approval, exactly as it was sent: sending it again with
 * the approval's id is what runs it, and the server checks it is the same call.
 */
export interface HeldCall {
  method: Method;
  /** The path it went to, node proxy included. */
  target: string;
  body: unknown;
  /** The server that answers it: a node's name, or null for this one. */
  node: string | null;
}

/** A call that became an approval request. */
export interface ApprovalRequested {
  /** The request's id: the value of X-Noust-Approval once it is approved. */
  id: string;
  call: HeldCall;
  /** `requested`, or `approved` when an earlier request for the same call already was. */
  state: string | null;
  /** The server's own words about it. */
  detail: string;
  hint: string | null;
}

export interface ApiHooks {
  /** A request came back 401 without being a failed sign-in: the session is gone. */
  onSessionExpired: () => void;
  /**
   * Asks the operator to confirm it's them. Resolves once the session is elevated; rejects
   * (with ElevationCancelledError) when they decline.
   */
  elevate: () => Promise<void>;
  /**
   * The server wants a reason before it files a call for a second person's approval (400
   * approval_reason_required). Resolves with the reason; rejects (ApprovalPendingError) when
   * the operator would rather not ask.
   */
  approvalReason: (call: HeldCall, refusal: ApiError) => Promise<string>;
  /**
   * A call became an approval request (202 approval_required). Resolves when it is approved
   * and the operator chooses to run it now, which sends the same call again under the
   * approval; rejects (ApprovalPendingError) when they leave it waiting, or it is rejected.
   */
  approval: (requested: ApprovalRequested) => Promise<void>;
}

const DEFAULT_HOOKS: ApiHooks = {
  onSessionExpired: () => undefined,
  elevate: () => Promise.reject(new ElevationCancelledError()),
  approvalReason: () => Promise.reject(new ApprovalPendingError("approval_cancelled")),
  approval: (requested) => Promise.reject(new ApprovalPendingError("approval_pending", requested.id)),
};

let hooks: ApiHooks = DEFAULT_HOOKS;

/**
 * Installs what the client calls when the session is lost or an action needs elevation.
 * Returns a function that restores the previous hooks (tests, hot reload).
 */
export function configureApi(next: Partial<ApiHooks>): () => void {
  const previous = hooks;
  hooks = { ...hooks, ...next };
  return () => {
    hooks = previous;
  };
}

/** Forgets every installed hook, any pending elevation and the selected server. For tests. */
export function resetApiHooks(): void {
  hooks = DEFAULT_HOOKS;
  pendingElevation = null;
  resetNodeScope();
}

/** Reports a lost session through the installed hook, for callers that learn it another way. */
export function expireSession(): void {
  hooks.onSessionExpired();
}

// The backend names both in GET /api/auth/session; these are its defaults.
const csrf = { header: "X-WASM-CSRF", cookie: "wasm_csrf" };

/** Adopts the CSRF header and cookie names the session endpoint announced. */
export function setCsrfNames(header: string, cookie: string): void {
  csrf.header = header;
  csrf.cookie = cookie;
}

export function readCookie(name: string): string | null {
  for (const part of document.cookie.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) continue;
    if (part.slice(0, index).trim() === name) return decodeURIComponent(part.slice(index + 1).trim());
  }
  return null;
}

export interface ApiInit {
  signal?: AbortSignal | undefined;
}

/** What a call carries for the approval protocol, when it does. */
interface ApprovalInit {
  /** The approved request it runs under. */
  approval?: string;
  /** Why it is made, when the server asked. */
  reason?: string;
}

// Several requests can hit a destructive endpoint at once; the operator confirms once.
let pendingElevation: Promise<void> | null = null;

function elevateOnce(): Promise<void> {
  pendingElevation ??= hooks.elevate().finally(() => {
    pendingElevation = null;
  });
  return pendingElevation;
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (text === "") return undefined;
  const type = response.headers.get("Content-Type") ?? "";
  return type.includes("json") ? (JSON.parse(text) as unknown) : text;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

/** Reusing this id updates one toast in place instead of stacking a new one per request. */
const RATE_LIMIT_TOAST_ID = "rate-limited";

/** How long a rate limit asks to wait, compactly: "30s", "2m". */
function waitWords(locale: Locale, retryAfter: number): string {
  if (retryAfter < 60) return translate(locale, "time.duration.seconds", { value: retryAfter });
  return translate(locale, "time.duration.minutes", { value: Math.ceil(retryAfter / 60) });
}

async function send(
  method: Method,
  path: string,
  body: unknown,
  init: ApiInit,
  node: string | null,
  mayElevate: boolean,
  mayRetryRateLimit = true,
  approval: ApprovalInit = {},
): Promise<unknown> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = readCookie(csrf.cookie);
  if (token !== null) headers[csrf.header] = token;
  if (approval.approval !== undefined) headers[APPROVAL_HEADER] = approval.approval;
  if (approval.reason !== undefined) headers[REASON_HEADER] = encodeURIComponent(approval.reason);

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      ...(init.signal ? { signal: init.signal } : {}),
    });
  } catch (cause: unknown) {
    // A cancelled query is not a failure; TanStack Query expects the abort to propagate.
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw unreachable(cause);
  }

  // 202 approval_required is a success status carrying a refusal: the call did not run, it
  // became a request a second person decides. Once approved, the same call goes again under
  // it, exactly once; a call already under an approval is never turned into another request.
  const requested = response.status === 202 ? response.headers.get(APPROVAL_REQUEST_HEADER) : null;
  if (requested !== null && approval.approval === undefined) {
    const held = (await readBody(response)) as { detail?: unknown; hint?: unknown; fields?: { state?: unknown } | null } | undefined;
    await hooks.approval({
      id: requested,
      call: { method, target: path, body, node },
      state: typeof held?.fields?.state === "string" ? held.fields.state : null,
      detail: typeof held?.detail === "string" ? held.detail : "",
      hint: typeof held?.hint === "string" ? held.hint : null,
    });
    return send(method, path, body, init, node, true, mayRetryRateLimit, { approval: requested });
  }

  if (response.ok) return readBody(response);

  const error = await errorFromResponse(response, node);
  if (error.sessionExpired) {
    hooks.onSessionExpired();
  } else if (mayElevate && error.status === 403 && error.error === "elevation_required") {
    await elevateOnce();
    // Exactly one retry: a second refusal is reported, never a second dialog.
    return send(method, path, body, init, node, false, mayRetryRateLimit, approval);
  } else if (error.error === "approval_reason_required" && approval.reason === undefined && approval.approval === undefined) {
    const reason = await hooks.approvalReason({ method, target: path, body, node }, error);
    return send(method, path, body, init, node, mayElevate, mayRetryRateLimit, { reason });
  } else if (error.status === 429 && error.error === "rate_limited") {
    // Reading again is safe to repeat; a mutation is not, so it surfaces the refusal for the
    // caller to report instead of silently repeating a write. TanStack Query is told never to
    // retry a 4xx (see createQueryClient), so this is the one retry that happens, not a storm.
    const retrying = method === "GET" && mayRetryRateLimit && error.retryAfter !== null;
    const locale = getLocale();
    const wait = error.retryAfter === null ? null : waitWords(locale, error.retryAfter);
    toast.warning(translate(locale, "common.rateLimit.title"), {
      id: RATE_LIMIT_TOAST_ID,
      detail: error.detail,
      description:
        retrying && wait !== null
          ? translate(locale, "common.rateLimit.retrying", { wait })
          : (error.hint ??
            (wait === null ? translate(locale, "common.rateLimit.tryAgainShortly") : translate(locale, "common.rateLimit.tryAgain", { wait }))),
    });
    if (retrying) {
      await delay(error.retryAfter * 1000);
      return send(method, path, body, init, node, mayElevate, false, approval);
    }
  }
  throw error;
}

/**
 * Calls the API and returns the decoded JSON body.
 *
 * @throws ApiError for any non-2xx answer or when the server cannot be reached.
 */
export async function api<T>(method: Method, path: string, body?: unknown, init: ApiInit = {}): Promise<T> {
  // Resolved once, before the first await: a retry after "Confirm it's you" or a rate limit
  // goes to the same server, even if the operator switched in the meantime.
  const node = activeNode();
  const target = nodeApiPath(node, path);
  const answeredBy = target === path ? nodeOfProxyPath(path) : node;
  return (await send(method, target, body, init, answeredBy, true)) as T;
}

/**
 * Sends a held call again under its approval: what "Run it now" does from the approvals inbox,
 * after the page that made the call is gone. The server runs it once, and only if it is exactly
 * the call that was approved.
 */
export function sendApproved(call: HeldCall, approvalId: string): Promise<unknown> {
  return send(call.method, call.target, call.body, {}, call.node, true, true, { approval: approvalId });
}

// ---------------------------------------------------------------------------------------
// The typed layer over the OpenAPI contract.

type HttpMethod = "get" | "post" | "put" | "patch" | "delete";
export type ApiPath = keyof paths;

type Operation<P extends ApiPath, M extends HttpMethod> = NonNullable<paths[P][M]>;

/** The methods a path actually declares. */
export type MethodOf<P extends ApiPath> = {
  [M in HttpMethod]: [paths[P][M]] extends [undefined] ? never : M;
}[HttpMethod];

type JsonOf<R> = R extends { content: { "application/json": infer B } } ? B : undefined;

type Success<R> = R extends { 200: infer S }
  ? JsonOf<S>
  : R extends { 201: infer S }
    ? JsonOf<S>
    : R extends { 202: infer S }
      ? JsonOf<S>
      : undefined;

/** The decoded body of a successful call. `unknown` until the backend declares a model. */
export type ResponseOf<P extends ApiPath, M extends MethodOf<P>> = Operation<P, M> extends { responses: infer R }
  ? Success<R>
  : never;

/** The JSON body a call takes, or undefined when it takes none. */
export type BodyOf<P extends ApiPath, M extends MethodOf<P>> = Operation<P, M> extends { requestBody?: never }
  ? undefined
  : Operation<P, M> extends { requestBody?: { content: { "application/json": infer B } } }
    ? B
    : undefined;

/** The query parameters a call accepts. */
export type QueryOf<P extends ApiPath, M extends MethodOf<P>> = Operation<P, M> extends { parameters: { query?: infer Q } }
  ? [Q] extends [undefined]
    ? undefined
    : Q
  : undefined;

type ParamNames<S extends string> = S extends `${string}{${infer Name}}${infer Rest}` ? Name | ParamNames<Rest> : never;

type PathParams<P extends string> = [ParamNames<P>] extends [never]
  ? undefined
  : Record<ParamNames<P>, string | number>;

export type RequestOptions<P extends ApiPath, M extends MethodOf<P>> = (PathParams<P> extends undefined
  ? { params?: undefined }
  : { params: PathParams<P> }) &
  (BodyOf<P, M> extends undefined ? { body?: undefined } : { body: BodyOf<P, M> }) &
  (QueryOf<P, M> extends undefined ? { query?: undefined } : { query?: QueryOf<P, M> }) & { signal?: AbortSignal };

type OptionsArg<P extends ApiPath, M extends MethodOf<P>> = object extends RequestOptions<P, M>
  ? [options?: RequestOptions<P, M>]
  : [options: RequestOptions<P, M>];

type QueryValue = string | number | boolean | null | undefined | readonly (string | number | boolean)[];

/** Fills `{name}` segments (encoded) and appends the query string, skipping empty values. */
export function buildPath(
  template: string,
  params?: Readonly<Record<string, string | number>>,
  query?: Readonly<Record<string, QueryValue>>,
): string {
  const path = template.replace(/\{([^}]+)\}/g, (_, name: string) => {
    const value = params?.[name];
    if (value === undefined) throw new Error(`Missing path parameter "${name}" for ${template}`);
    return encodeURIComponent(String(value));
  });
  if (!query) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const item of value as readonly (string | number | boolean)[]) search.append(key, String(item));
    } else {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  return qs === "" ? path : `${path}?${qs}`;
}

/**
 * Calls an endpoint of the OpenAPI contract.
 *
 *     request("get", "/api/apps/{domain}", { params: { domain } })
 */
export function request<P extends ApiPath, M extends MethodOf<P>>(
  method: M,
  path: P,
  ...[options]: OptionsArg<P, M>
): Promise<ResponseOf<P, M>> {
  const opts = (options ?? {}) as {
    params?: Record<string, string | number>;
    query?: Record<string, QueryValue>;
    body?: unknown;
    signal?: AbortSignal;
  };
  const url = buildPath(path, opts.params, opts.query);
  return api<ResponseOf<P, M>>(method.toUpperCase() as Method, url, opts.body, { signal: opts.signal });
}
