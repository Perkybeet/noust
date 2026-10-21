/**
 * The Logs tab of an application as it lives in the URL (`?source=access&since=24h&q=timeout`),
 * and the command that asks the same from a terminal. The tab shares its toolbar and its query
 * language with the Logs page (`features/logs`), so what an operator learns on one works on the
 * other; what is its own is the source (the application's output, or the web server's access or
 * error log for its site) and one range that only an application has: since its last deploy.
 */

import type { SiteLogKind } from "../../../api/queries/siteLogs";

/** Where an application's lines come from besides its own output (the default): the web server's logs. */
export const SITE_SOURCES = ["access", "error"] as const satisfies readonly SiteLogKind[];
export type AppLogSource = "app" | SiteLogKind;

/**
 * How far back the tab reads. "live" is the default: the application's journal followed as it is
 * written. The others ask the server for a window of the past, and "deploy" for everything since
 * the last deployment started.
 */
export const APP_RANGES = ["live", "15m", "1h", "6h", "24h", "7d", "deploy"] as const;
export type AppLogRange = (typeof APP_RANGES)[number];

export interface AppLogsSearch {
  /** Absent means the application's own output. */
  source?: SiteLogKind;
  /** Absent means live. */
  since?: Exclude<AppLogRange, "live">;
  q?: string;
}

/** What was typed in the search box, as it was typed: a space at the end is the start of the next word. */
function typed(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value.slice(0, 1000) : undefined;
}

/** Reads the search params, dropping anything malformed instead of failing. */
export function validateAppLogsSearch(search: Record<string, unknown>): AppLogsSearch {
  const source = search["source"];
  const since = search["since"];
  const q = typed(search["q"]);
  return {
    ...(source === "access" || source === "error" ? { source } : {}),
    ...(typeof since === "string" && since !== "live" && (APP_RANGES as readonly string[]).includes(since) ? { since: since as Exclude<AppLogRange, "live"> } : {}),
    ...(q !== undefined ? { q } : {}),
  };
}

/** The question with a change applied and nothing in it that is empty or is the default. */
export function patchedApp(search: AppLogsSearch, patch: { [K in Exclude<keyof AppLogsSearch, "since">]?: AppLogsSearch[K] | undefined } & { since?: AppLogRange | undefined }): AppLogsSearch {
  return validateAppLogsSearch({ ...search, ...patch });
}

const quote = (value: string): string => (/^[\w.@%+=:,/-]+$/.test(value) ? value : `'${value.replaceAll("'", `'\\''`)}'`);

/**
 * The command that reads the same lines from a terminal, or nothing when it cannot be said yet.
 *
 * @param deployedAt - When the last deployment started, Unix seconds: what "since the last deploy" is for the command.
 */
export function appLogsCommand(domain: string, source: AppLogSource, search: AppLogsSearch, deployedAt: number | null): string | null {
  if (source !== "app") return `noust site logs ${domain} --kind ${source}`;
  const range = search.since ?? "live";
  if (range === "live") return `noust logs ${domain} --follow`;
  if (range === "deploy" && deployedAt === null) return null;
  const parts = ["noust log search"];
  if (search.q !== undefined) parts.push(quote(search.q));
  parts.push(`--app ${domain}`, `--since ${range === "deploy" ? String(deployedAt) : range}`);
  return parts.join(" ");
}
