/**
 * What a channel hears: its own events, the part of the server it is limited to, and the hours it
 * stays silent - read from `GET /api/notifications/channels` into something a form can hold, and
 * written back through `PUT /api/notifications/channels/{name}`.
 *
 * Nothing here decides what a route means: the server's own rules (noust.core.notifications.routing)
 * validate what is sent and name what they refuse. This only turns typed text into the lists and
 * the clock times the server reads, and back.
 */

import type { ChannelBody, NotificationChannel } from "../../api/queries/notifications";
import type { T } from "../../i18n";
import { EVENT_KINDS, OFF_BY_DEFAULT } from "./notifications";

export type MinSeverity = "" | "warning" | "failed";

/** The lowest severity that still gets through quiet hours (noust.core.notifications.routing.QUIET_ALLOW). */
export type QuietAllow = "failed" | "warning" | "none";

export interface RouteForm {
  /** Application domains, or patterns with `*` and `?`, as typed: separated by commas, spaces or lines. */
  apps: string;
  projects: string;
  environments: string;
  tags: string;
  minSeverity: MinSeverity;
  /** Also hear what is about the server and no application, when the lists above would leave it out. */
  serverEvents: boolean;
}

export interface QuietForm {
  on: boolean;
  /** "22:00". */
  start: string;
  end: string;
  /** An IANA name, "Europe/Madrid". */
  timezone: string;
  allow: QuietAllow;
  /** Deliver what was held as one message when the hours end. */
  digest: boolean;
}

export interface RoutingForm {
  /** The channel keeps its own switch per event; otherwise it follows the server's. */
  ownEvents: boolean;
  /** Every kind's switch: the channel's own where it has one, else what the server's apply. */
  events: Record<string, boolean>;
  route: RouteForm;
  quiet: QuietForm;
  /** A Telegram forum topic, as typed: digits, or empty for the chat's general thread. */
  topic: string;
}

/** The lists of a route, in the order a form shows them. */
export const ROUTE_LISTS = ["apps", "projects", "environments", "tags"] as const;
export type RouteList = (typeof ROUTE_LISTS)[number];

export const QUIET_ALLOWS: readonly QuietAllow[] = ["failed", "warning", "none"];
export const MIN_SEVERITIES: readonly MinSeverity[] = ["", "warning", "failed"];

/** The most a list may name: the server's own ceiling (noust.core.notifications.routing.MAX_ENTRIES). */
export const MAX_ENTRIES = 64;

/** Names typed into a list: separated by commas, spaces or lines, without blanks or repeats. */
export function parseList(value: string): string[] {
  const entries = value
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter((entry) => entry !== "");
  return [...new Set(entries)];
}

function listText(value: unknown): string {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").join(", ") : "";
}

function textOf(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : typeof value === "number" ? String(value) : fallback;
}

/** The time zone the browser is in, offered as the quiet hours' zone when none was chosen. */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** What a channel does with an event nothing names: on, but for the few that ship off. */
export function defaultSwitch(kind: string): boolean {
  return !OFF_BY_DEFAULT.has(kind);
}

/**
 * The form for a channel.
 *
 * @param serverEvents The server-wide switches (`notifications.events`), which a channel follows
 *   for every event it has no switch of its own for.
 */
export function routingFormFrom(channel: NotificationChannel, serverEvents: Readonly<Record<string, boolean>>): RoutingForm {
  // The schema marks what has a default as optional; the server always sends them.
  const own = channel.events ?? {};
  const route = channel.route ?? {};
  const quiet = channel.quiet;
  const severity = textOf(route["min_severity"]);
  const allow = textOf(quiet?.["allow"], "failed");
  return {
    ownEvents: Object.keys(own).length > 0,
    events: Object.fromEntries(EVENT_KINDS.map((kind) => [kind, own[kind] ?? serverEvents[kind] ?? defaultSwitch(kind)])),
    route: {
      apps: listText(route["apps"]),
      projects: listText(route["projects"]),
      environments: listText(route["environments"]),
      tags: listText(route["tags"]),
      minSeverity: severity === "warning" || severity === "failed" ? severity : "",
      serverEvents: route["server_events"] === true,
    },
    quiet: {
      on: quiet !== null && quiet !== undefined && Object.keys(quiet).length > 0,
      start: textOf(quiet?.["start"], "22:00"),
      end: textOf(quiet?.["end"], "07:00"),
      timezone: textOf(quiet?.["timezone"], browserTimeZone()),
      allow: allow === "warning" || allow === "none" ? allow : "failed",
      digest: quiet?.["digest"] !== false,
    },
    topic: textOf(channel.settings?.["thread_id"]),
  };
}

/** The `route` block a form writes: only what is set, `{}` for a channel that hears everything. */
export function routeBlock(form: RouteForm): Record<string, unknown> {
  const block: Record<string, unknown> = {};
  for (const list of ROUTE_LISTS) {
    const entries = parseList(form[list]);
    if (entries.length > 0) block[list] = entries;
  }
  if (form.minSeverity !== "") block["min_severity"] = form.minSeverity;
  if (form.serverEvents) block["server_events"] = true;
  return block;
}

/** The `quiet` block a form writes: `{}` clears the hours. */
export function quietBlock(form: QuietForm): Record<string, unknown> {
  if (!form.on) return {};
  return { start: form.start, end: form.end, timezone: form.timezone.trim() || "UTC", allow: form.allow, digest: form.digest };
}

/**
 * The PUT body for a form. Events are written whole when the channel has its own, and cleared
 * when it follows the server's; a route or quiet hours the form emptied are cleared the same way.
 */
export function routingBody(form: RoutingForm, channel: Pick<NotificationChannel, "type">): ChannelBody {
  const body: ChannelBody = {
    events: form.ownEvents ? Object.fromEntries(EVENT_KINDS.map((kind) => [kind, form.events[kind] === true])) : {},
    route: routeBlock(form.route),
    quiet: quietBlock(form.quiet),
  };
  if (channel.type === "telegram") body.settings = { thread_id: form.topic.trim() };
  return body;
}

/** Whether a form differs from the one read from the server. */
export function routingDirty(form: RoutingForm, initial: RoutingForm, channel: Pick<NotificationChannel, "type">): boolean {
  return JSON.stringify(routingBody(form, channel)) !== JSON.stringify(routingBody(initial, channel));
}

/** The clock time the server reads: `H:MM` or `HH:MM`. */
export function isClock(value: string): boolean {
  return /^([01]?\d|2[0-3]):[0-5]\d$/.test(value);
}

/**
 * What a channel is limited to, in words for its row: nothing for a channel that hears everything,
 * else one short phrase for each limit it has.
 */
export function routeSummary(t: T, channel: Pick<NotificationChannel, "route" | "quiet" | "events">): string[] {
  const phrases: string[] = [];
  const route = channel.route ?? {};
  for (const list of ROUTE_LISTS) {
    const entries = route[list];
    if (Array.isArray(entries) && entries.length > 0) {
      phrases.push(t(`notifyRouting.summary.${list}`, { names: entries.join(", ") }));
    }
  }
  if (route["min_severity"] === "failed") phrases.push(t("notifyRouting.summary.failedOnly"));
  else if (route["min_severity"] === "warning") phrases.push(t("notifyRouting.summary.warningUp"));
  const quiet = channel.quiet;
  if (quiet !== null && quiet !== undefined && Object.keys(quiet).length > 0) {
    phrases.push(t("notifyRouting.summary.quiet", { start: textOf(quiet["start"]), end: textOf(quiet["end"]) }));
  }
  const own = Object.keys(channel.events ?? {}).length;
  if (own > 0) phrases.push(t("notifyRouting.summary.ownEvents", { count: own }));
  return phrases;
}
