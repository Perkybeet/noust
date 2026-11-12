/**
 * What the pages of the central's copies read beyond the API's shapes: the knobs as a form holds them and
 * as the API takes them, how much room is left, and which of a server's states each row says. Pure, so every
 * rule is tested without a screen.
 */

import type { CollectSettingsBody, CollectingNode, CollectingStatus } from "../../../api/queries/centralBackups";
import type { Status } from "../../../components/ui/StatusPill";
import type { PlainKey } from "../../../i18n";
import { gbValue, limitText, limitValue, validGb, validLimit } from "../policy/limits";
import { isTrue, numberOf, textOf } from "../policy/record";
import type { OpenRecord } from "../policy/record";

/** The kinds of copy a server makes, in the order the API lists them. */
export const KINDS = ["app", "database", "volume", "noust"] as const;
export type CopyKind = (typeof KINDS)[number];

/** The bounds the API holds each knob to. */
export const MIN_SPEED_KBPS = 16;
export const MAX_SPEED_KBPS = 10_000_000;
export const MAX_RETENTION_COUNT = 3650;
export const MAX_RETENTION_DAYS = 36_500;
export const MAX_MIN_FREE_GB = 1_000_000;

/** A kind the console has words for; a kind a newer server adds is shown as it came. */
export function isKind(value: string): value is CopyKind {
  return (KINDS as readonly string[]).includes(value);
}

/** The knobs of collecting, as the status says them. */
export interface Knobs {
  enabled: boolean;
  kinds: CopyKind[];
  rateLimitKbps: number | null;
  retentionCount: number | null;
  retentionDays: number | null;
  retentionGb: number | null;
  minFreeGb: number;
}

/** The knobs from a status, or null when the caller is not shown them (a team sees no knobs, they are the instance's). */
export function knobsOf(status: Pick<CollectingStatus, "settings"> | undefined): Knobs | null {
  const raw = status?.settings;
  if (raw === null || raw === undefined) return null;
  const record: OpenRecord = raw;
  const kinds = Array.isArray(record["kinds"]) ? record["kinds"].filter((kind): kind is CopyKind => typeof kind === "string" && isKind(kind)) : [...KINDS];
  return {
    enabled: isTrue(record, "enabled"),
    kinds: kinds.length > 0 ? kinds : [...KINDS],
    rateLimitKbps: numberOf(record, "rate_limit_kbps"),
    retentionCount: numberOf(record, "retention_count"),
    retentionDays: numberOf(record, "retention_days"),
    retentionGb: numberOf(record, "retention_gb"),
    minFreeGb: numberOf(record, "min_free_gb") ?? 0,
  };
}

/** The disk the copies are on. */
export interface Room {
  total: number;
  free: number;
  used: number;
  /** What is always left alone. */
  minFree: number;
  /** Less than a tenth of the disk, or the room kept free, is left. */
  low: boolean;
}

/** The disk from a status; null when the caller is not shown it or the server could not read it. */
export function roomOf(status: Pick<CollectingStatus, "space"> | undefined): Room | null {
  const raw = status?.space;
  if (raw === null || raw === undefined) return null;
  const record: OpenRecord = raw;
  const total = numberOf(record, "total");
  const free = numberOf(record, "free");
  if (total === null || free === null || total <= 0) return null;
  return { total, free, used: numberOf(record, "used") ?? 0, minFree: numberOf(record, "min_free") ?? 0, low: isTrue(record, "low") };
}

/** The settings as the drawer's form holds them: text, because empty means no limit. */
export interface SettingsForm {
  enabled: boolean;
  kinds: CopyKind[];
  speed: string;
  count: string;
  days: string;
  gb: string;
  minFree: string;
}

export function formOf(knobs: Knobs | null): SettingsForm {
  return {
    enabled: knobs?.enabled ?? false,
    kinds: knobs?.kinds ?? [...KINDS],
    speed: limitText(knobs?.rateLimitKbps),
    count: limitText(knobs?.retentionCount),
    days: limitText(knobs?.retentionDays),
    gb: limitText(knobs?.retentionGb),
    minFree: knobs === null ? "1" : String(knobs.minFreeGb),
  };
}

export type SettingsField = "kinds" | "speed" | "count" | "days" | "gb" | "minFree";

/** What is wrong with a form, per field: each a key of the catalog. */
export function problemsOf(form: SettingsForm): Partial<Record<SettingsField, PlainKey>> {
  const found: Partial<Record<SettingsField, PlainKey>> = {};
  if (form.kinds.length === 0) found.kinds = "centralBackups.settings.kindsRequired";
  const speed = limitValue(form.speed);
  if (speed !== null && !(Number.isInteger(speed) && speed >= MIN_SPEED_KBPS && speed <= MAX_SPEED_KBPS)) found.speed = "centralBackups.settings.speedInvalid";
  if (!validLimit(form.count, MAX_RETENTION_COUNT)) found.count = "centralBackups.settings.notNumber";
  if (!validLimit(form.days, MAX_RETENTION_DAYS)) found.days = "centralBackups.settings.notNumber";
  if (!validGb(form.gb)) found.gb = "centralBackups.settings.notAmount";
  const minFree = form.minFree.trim() === "" ? 0 : Number(form.minFree.trim().replace(",", "."));
  if (!Number.isFinite(minFree) || minFree < 0 || minFree > MAX_MIN_FREE_GB) found.minFree = "centralBackups.settings.notAmount";
  return found;
}

/**
 * What the API takes. A limit that is empty is sent as 0, which takes it off: leaving it out would keep what was
 * there, and the person who emptied a field means no limit.
 */
export function bodyOf(form: SettingsForm): CollectSettingsBody {
  const minFree = form.minFree.trim() === "" ? 0 : Number(form.minFree.trim().replace(",", "."));
  return {
    enabled: form.enabled,
    kinds: KINDS.filter((kind) => form.kinds.includes(kind)),
    rate_limit_kbps: limitValue(form.speed) ?? 0,
    retention_count: limitValue(form.count) ?? 0,
    retention_days: limitValue(form.days) ?? 0,
    retention_gb: gbValue(form.gb) ?? 0,
    min_free_gb: minFree,
  };
}

/** Whether every kind of copy is taken: the drawer says so in fewer words than listing them. */
export function takesEverything(knobs: Knobs): boolean {
  return KINDS.every((kind) => knobs.kinds.includes(kind));
}

/** How one server reads in the list: its state, the words for it and, where there is one, the fix. */
export interface NodeView {
  status: Status;
  /** The state's words; none while a copy is moving, which names it (`moving`) instead. */
  label: PlainKey | null;
  /** The sentence under the state when it is a problem, or a note. */
  hint?: PlainKey;
  /** The copy being taken, as the server's board names it ("app: shop.example.com"). */
  moving?: string;
}

const ERROR_VIEW: Readonly<Record<string, NodeView>> = {
  unreachable: { status: "failed", label: "centralBackups.servers.state.unreachable", hint: "centralBackups.servers.hint.unreachable" },
  refused: { status: "failed", label: "centralBackups.servers.state.refused", hint: "centralBackups.servers.hint.refused" },
  ceiling: { status: "failed", label: "centralBackups.servers.state.ceiling", hint: "centralBackups.servers.hint.ceiling" },
  disk_full: { status: "failed", label: "centralBackups.servers.state.diskFull", hint: "centralBackups.servers.hint.diskFull" },
  mismatch: { status: "failed", label: "centralBackups.servers.state.mismatch", hint: "centralBackups.servers.hint.mismatch" },
};

/** What is moving for a server, as the board says it. */
export interface Moving {
  item: string;
  file: string;
  done: number;
  size: number;
}

export function movingOf(node: Pick<CollectingNode, "moving">): Moving | null {
  const raw = node.moving;
  if (raw === null || raw === undefined) return null;
  const record: OpenRecord = raw;
  return { item: textOf(record, "item"), file: textOf(record, "file"), done: numberOf(record, "done") ?? 0, size: numberOf(record, "size") ?? 0 };
}

/**
 * The state of a server, most urgent first: a server that is not collected says so before anything else; one that is
 * moving a copy says which; a failure says its stable word; a server before 4.3 gives only its database dumps; one never
 * looked at is waiting; the rest is up to date.
 */
export function nodeView(node: CollectingNode): NodeView {
  if (!node.following) return { status: "stopped", label: "centralBackups.servers.state.off" };
  const moving = movingOf(node);
  if (moving !== null) return { status: "deploying", label: null, moving: moving.item };
  const code = node.last_error_code ?? null;
  if (code !== null || (node.last_error ?? null) !== null) {
    return (code === null ? undefined : ERROR_VIEW[code]) ?? { status: "failed", label: "centralBackups.servers.state.failed" };
  }
  if (textOf(node.support, "mode") === "databases_only") {
    return { status: "warning", label: "centralBackups.servers.state.databasesOnly", hint: "centralBackups.servers.hint.databasesOnly" };
  }
  if ((node.last_ok_at ?? null) === null) return { status: "unknown", label: "centralBackups.servers.state.waiting" };
  return { status: "running", label: "centralBackups.servers.state.upToDate" };
}

/** A server's own answer to being collected from, as the three choices of its menu. */
export type FollowChoice = "fleet" | "on" | "off";

export function followChoice(node: Pick<CollectingNode, "follow">): FollowChoice {
  return node.follow === true ? "on" : node.follow === false ? "off" : "fleet";
}

/** The API's answer for a choice: null follows the fleet's. */
export function followValue(choice: FollowChoice): boolean | null {
  return choice === "on" ? true : choice === "off" ? false : null;
}

/** Which copy a row is: the three that name it on the server. */
export function copyKey(copy: { node: string; kind: string; id: string }): string {
  return `${copy.node}/${copy.kind}/${copy.id}`;
}
