/**
 * A probe as a form holds it, and the words that say how it stands.
 *
 * The server owns what a probe may be (noust.core.probe_store): the console turns what is typed
 * into what the API takes and back, and never decides whether an address, a range of statuses or
 * a number is acceptable - the server's refusal is shown beside the field it names.
 */

import type { Probe, ProbeBody, ProbeStatus } from "../../../api/queries/probes";
import type { Locale, T } from "../../../i18n";
import { formatDuration, formatUptime } from "../../../lib/format";

export type ProbeMethod = "GET" | "HEAD";
export const PROBE_METHODS: readonly ProbeMethod[] = ["GET", "HEAD"];

/** The server's defaults (noust.core.probe_store), so a new probe opens with what it would be given. */
export const DEFAULT_INTERVAL_S = 60;
export const DEFAULT_TIMEOUT_S = 10;
export const DEFAULT_FAIL_AFTER = 3;

export interface ProbeForm {
  name: string;
  url: string;
  method: ProbeMethod;
  expectStatus: string;
  expectText: string;
  /** Whole numbers as typed. */
  interval: string;
  timeout: string;
  failAfter: string;
  /** Shown on the status page. */
  publicOn: boolean;
  title: string;
}

/** The fields the API names in a refusal. */
export const PROBE_FIELDS = ["name", "url", "method", "expect_status", "expect_text", "interval_s", "timeout_s", "fail_after", "title"] as const;
export type ProbeField = (typeof PROBE_FIELDS)[number];

export function emptyProbe(): ProbeForm {
  return {
    name: "",
    url: "",
    method: "GET",
    expectStatus: "200-399",
    expectText: "",
    interval: String(DEFAULT_INTERVAL_S),
    timeout: String(DEFAULT_TIMEOUT_S),
    failAfter: String(DEFAULT_FAIL_AFTER),
    publicOn: false,
    title: "",
  };
}

export function probeFormFrom(probe: Probe): ProbeForm {
  return {
    name: probe.name,
    url: probe.url,
    method: probe.method === "HEAD" ? "HEAD" : "GET",
    expectStatus: probe.expect_status,
    expectText: probe.expect_text,
    interval: String(probe.interval_s),
    timeout: String(probe.timeout_s),
    failAfter: String(probe.fail_after),
    publicOn: probe.public,
    title: probe.title,
  };
}

/** A whole number as typed; the server rules on whether it is in range, so a typo is sent as `NaN` never. */
function whole(value: string, fallback: number): number {
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/** The PUT body. A blank number takes the server's default rather than being sent as nothing. */
export function probeBody(form: ProbeForm, enabled: boolean): ProbeBody {
  return {
    url: form.url.trim(),
    method: form.method,
    expect_status: form.expectStatus.trim() || "200-399",
    expect_text: form.expectText.trim(),
    interval_s: whole(form.interval, DEFAULT_INTERVAL_S),
    timeout_s: whole(form.timeout, DEFAULT_TIMEOUT_S),
    fail_after: whole(form.failAfter, DEFAULT_FAIL_AFTER),
    public: form.publicOn,
    title: form.title.trim(),
    enabled,
  };
}

/** How a probe stands, as the state the console draws and the catalog key of its word. */
export function standing(probe: Probe): { state: "running" | "failed" | "stopped" | "queued"; key: "up" | "down" | "off" | "waiting" } {
  if (!probe.enabled) return { state: "stopped", key: "off" };
  if (probe.status.state === "up") return { state: "running", key: "up" };
  if (probe.status.state === "down") return { state: "failed", key: "down" };
  return { state: "queued", key: "waiting" };
}

/** An uptime percentage the way a status page says it: "99.95%", or a dash when it was never asked. */
export function uptimeText(percent: number | null | undefined, locale: Locale): string {
  if (percent === null || percent === undefined) return "-";
  return formatUptime(percent, locale);
}

/** The windows a probe reports uptime over, shortest first. */
export const UPTIME_WINDOWS = ["24h", "7d", "30d", "90d"] as const;

/** The last answer in one line: "200 in 184 ms", or the one line the server wrote for a bad one. */
export function lastAnswer(t: T, status: ProbeStatus): string | null {
  if (status.last_checked === null || status.last_checked === undefined) return null;
  const parts: string[] = [];
  if (status.last_status !== null && status.last_status !== undefined) parts.push(String(status.last_status));
  if (status.last_latency_ms !== null && status.last_latency_ms !== undefined) parts.push(t("probes.answer.inMs", { ms: status.last_latency_ms }));
  const base = parts.join(" ");
  if (status.last_error !== null && status.last_error !== undefined && status.last_error !== "") return base === "" ? status.last_error : `${base}: ${status.last_error}`;
  return base === "" ? null : base;
}

/** How long a stretch lasted ("7m 42s"); one that is still going says so by having no end. */
export function stretch(started: number, ended: number | null | undefined, now: number, locale: Locale): string {
  return formatDuration(Math.max(0, (ended ?? now) - started), locale);
}
