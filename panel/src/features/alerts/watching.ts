/**
 * What the opening of Alerts says, as pure functions so each rule is tested without a page. The table of what
 * Noust watches is the server's (`monitor/watching.py`, `GET /api/alerts/watching`): the console has no list of
 * its own, only the words of each id. A watch this console has no words for (a server newer than the console)
 * is still listed, under its id, and a test holds the words to the ids the server knows.
 */

import type { Watch, Watching } from "../../api/queries/watching";
import type { T } from "../../i18n";

export interface WatchWords {
  name: string;
  sentence: string;
}

function numberParam(watch: Watch, key: string): number {
  const value = watch.params?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** What a watch is called and what it looks at, in the console's language; an id nobody gave words for, by its id. */
export function watchWords(t: T, watch: Watch): WatchWords {
  switch (watch.id) {
    case "apps_down":
      return { name: t("alerts.watching.items.appsDown.name"), sentence: t("alerts.watching.items.appsDown.sentence") };
    case "deploys":
      return { name: t("alerts.watching.items.deploys.name"), sentence: t("alerts.watching.items.deploys.sentence") };
    case "certificates":
      return { name: t("alerts.watching.items.certificates.name"), sentence: t("alerts.watching.items.certificates.sentence") };
    case "domain_expiry":
      return { name: t("alerts.watching.items.domainExpiry.name"), sentence: t("alerts.watching.items.domainExpiry.sentence") };
    case "disk":
      return { name: t("alerts.watching.items.disk.name"), sentence: t("alerts.watching.items.disk.sentence", { percent: numberParam(watch, "percent") }) };
    case "updates":
      return { name: t("alerts.watching.items.updates.name"), sentence: t("alerts.watching.items.updates.sentence", { count: numberParam(watch, "hours") }) };
    case "access_changes":
      return { name: t("alerts.watching.items.accessChanges.name"), sentence: t("alerts.watching.items.accessChanges.sentence") };
    case "backups":
      return { name: t("alerts.watching.items.backups.name"), sentence: t("alerts.watching.items.backups.sentence") };
    case "backups_missing":
      return { name: t("alerts.watching.items.backupsMissing.name"), sentence: t("alerts.watching.items.backupsMissing.sentence", { count: numberParam(watch, "days") }) };
    case "tokens":
      return { name: t("alerts.watching.items.tokens.name"), sentence: t("alerts.watching.items.tokens.sentence", { count: numberParam(watch, "days") }) };
    case "releases":
      return { name: t("alerts.watching.items.releases.name"), sentence: t("alerts.watching.items.releases.sentence") };
    case "recipes":
      return { name: t("alerts.watching.items.recipes.name"), sentence: t("alerts.watching.items.recipes.sentence") };
    default:
      return { name: watch.id, sentence: "" };
  }
}

/** The ids this console has words for: a test holds them to the table the server sends. */
export const KNOWN_WATCHES = ["apps_down", "deploys", "certificates", "domain_expiry", "disk", "updates", "access_changes", "backups", "backups_missing", "tokens", "releases", "recipes"] as const;

/** A watch that ships off and is worth offering: what is not watched yet, and what turning it on writes. */
export function isOffered(watch: Watch): boolean {
  return watch.state === "off" && watch.offer !== null && watch.offer !== undefined;
}

export interface WatchingRows {
  /** What is watched, or was turned off by the operator: the table. */
  rows: readonly Watch[];
  /** What is off and worth turning on, apart from the table. */
  offers: readonly Watch[];
}

/** Splits the server's table: what Noust watches goes in the table, what it could and does not goes in the offers. */
export function splitWatching(data: Pick<Watching, "watches">): WatchingRows {
  const watches = data.watches ?? [];
  return { rows: watches.filter((watch) => !isOffered(watch)), offers: watches.filter(isOffered) };
}

/** The channels that do not hear everything the table says (a route, a severity floor, quiet hours or a summary narrow them), once each. */
export function limitedChannels(data: Pick<Watching, "watches">): string[] {
  const names = (data.watches ?? []).flatMap((watch) => (watch.reach ?? []).filter((channel) => channel.limited).map((channel) => channel.name));
  return [...new Set(names)];
}

/**
 * Where what Noust finds can go, as the top of the page says it: nothing leaves (notifications are off), a pause, no channel
 * to take it, or the channels that do. One answer for every row, so each row only says its own exceptions.
 */
export type Reach =
  | { kind: "off" }
  | { kind: "paused"; until: string }
  | { kind: "nobody" }
  | { kind: "channels"; names: readonly string[] };

export function reachOf(data: Pick<Watching, "notifications">): Reach {
  const { enabled, paused_until: pausedUntil, channels } = data.notifications;
  if (!enabled) return { kind: "off" };
  if (typeof pausedUntil === "string" && pausedUntil !== "") return { kind: "paused", until: pausedUntil };
  const names = (channels ?? []).map((channel) => channel.name);
  return names.length === 0 ? { kind: "nobody" } : { kind: "channels", names };
}

/** The names of the channels a watch reaches, in the order the server gave them; none when notifications are off. */
export function reachCell(watch: Watch, data: Pick<Watching, "notifications">): readonly string[] {
  if (!data.notifications.enabled) return [];
  return (watch.reach ?? []).map((channel) => channel.name);
}

/**
 * What a row says of where it does not reach: the channels of the server that do not take what it says (they switched the
 * message off), or null when every channel takes it - which the top of the page says once for all of them - and when
 * there are no channels, or notifications are off, which the top says as well.
 */
export function reachMissing(watch: Watch, data: Pick<Watching, "notifications">): readonly string[] | null {
  const everywhere = (data.notifications.channels ?? []).map((channel) => channel.name);
  if (everywhere.length === 0 || !data.notifications.enabled) return null;
  const taken = new Set(reachCell(watch, data));
  const missing = everywhere.filter((name) => !taken.has(name));
  return missing.length === 0 ? null : missing;
}
