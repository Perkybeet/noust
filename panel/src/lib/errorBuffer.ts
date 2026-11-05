/**
 * The last errors of this tab, kept in memory.
 *
 * A report of a problem (`features/report`) says what the browser saw, and by the time somebody
 * opens it the error is already gone from the screen. This buffer is what remembers it: an
 * uncaught error, a promise nobody handled, what the console itself logged as an error and the
 * calls to the API that failed. It is a small ring, never written anywhere, and what is in it is
 * masked only when a report is made (`features/report/redact.ts`): nothing leaves the tab before
 * the person has read it.
 */

/** Where an entry came from. */
export type ErrorKind = "error" | "rejection" | "console" | "request";

export interface ErrorEntry {
  /** When it happened, ISO 8601. */
  at: string;
  kind: ErrorKind;
  /** What the browser or the API said, cut to a length a report can carry. */
  message: string;
}

/** How many are kept: the last ones are the ones that explain the page the person is on. */
export const ERROR_LIMIT = 20;

/** The longest message kept whole; a stack of a framework is longer than anyone reads. */
export const MESSAGE_LIMIT = 800;

let entries: ErrorEntry[] = [];

function cut(message: string): string {
  const flat = message.trim();
  return flat.length <= MESSAGE_LIMIT ? flat : `${flat.slice(0, MESSAGE_LIMIT)}...`;
}

/**
 * Remembers one error.
 *
 * @param kind - Where it came from.
 * @param message - What was said; an empty one is not worth keeping.
 * @param now - When it happened.
 */
export function recordError(kind: ErrorKind, message: string, now: Date = new Date()): void {
  const text = cut(message);
  if (text === "") return;
  const last = entries.at(-1);
  // The same failure again and again (a poll that keeps failing) would push everything else out.
  if (last?.kind === kind && last.message === text) return;
  entries = [...entries, { at: now.toISOString(), kind, message: text }].slice(-ERROR_LIMIT);
}

/**
 * Remembers a call to the API that did not succeed.
 *
 * @param method - The HTTP method.
 * @param path - The path with its query, as it was sent.
 * @param status - The status of the answer; 0 when there was none.
 */
export function recordFailedRequest(method: string, path: string, status: number): void {
  recordError("request", status === 0 ? `${method} ${path} (no answer)` : `${method} ${path} ${String(status)}`);
}

/** The errors kept, oldest first. */
export function recentErrors(): readonly ErrorEntry[] {
  return entries;
}

/** Forgets everything. For tests, and for a sign-out. */
export function clearErrors(): void {
  entries = [];
}

/** How many lines of a stack are kept: where it broke, not the whole of the framework under it. */
const STACK_LINES = 5;

/** An error as its name, its message and the first lines of where it was thrown. */
function describeError(error: Error): string {
  const stack = error.stack;
  if (stack === undefined || stack === "") return `${error.name}: ${error.message}`;
  const lines = stack.split("\n");
  // Chrome's stack starts with the message, Firefox's with the first frame: say the message once.
  const head = stack.includes(error.message) ? [] : [`${error.name}: ${error.message}`];
  return [...head, ...lines.slice(0, STACK_LINES)].join("\n");
}

/** One argument of `console.error`, as a line. */
function describe(value: unknown): string {
  if (value instanceof Error) return describeError(value);
  if (typeof value === "string") return value;
  try {
    // JSON.stringify answers undefined for a function or a symbol, whatever its type says.
    const text = JSON.stringify(value) as string | undefined;
    return text ?? String(value);
  } catch {
    return String(value);
  }
}

let installed: (() => void) | null = null;

/**
 * Starts listening: uncaught errors, unhandled rejections and `console.error`. Once: a second
 * call changes nothing and returns the same way to stop.
 *
 * @returns A function that stops listening and puts `console.error` back.
 */
export function installErrorBuffer(): () => void {
  if (installed !== null) return installed;
  const onError = (event: ErrorEvent): void => {
    const error: unknown = event.error;
    recordError("error", error instanceof Error ? describeError(error) : event.message);
  };
  const onRejection = (event: PromiseRejectionEvent): void => {
    recordError("rejection", describe(event.reason));
  };
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  // The console's own log is one of the sources, and it still goes where it went.
  const original = console.error;
  console.error = (...args: unknown[]): void => {
    recordError("console", args.map(describe).join(" "));
    original.apply(console, args);
  };
  installed = () => {
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    if (console.error !== original) console.error = original;
    installed = null;
  };
  return installed;
}
