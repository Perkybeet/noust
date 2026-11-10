/**
 * The audit log page beyond the raw API shape: its filters (in the URL), an event's outcome in
 * the state language, and who acted, in words.
 */

import type { AuditEntry } from "../../api/queries/audit";
import type { Status } from "../../components/ui/StatusPill";
import { hasMessage } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { describeActor, describeWho } from "../activity/data";
import type { ActorWords } from "../activity/data";
import { roleLabel } from "../settings/accounts/roles";

export interface AuditSearch {
  /** Words to find in the loaded events: actor, action, target, detail. */
  q?: string;
  category?: string;
  result?: string;
  /** Every event of one request, command or job. */
  correlation?: string;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

export function validateAuditSearch(search: Record<string, unknown>): AuditSearch {
  const q = text(search["q"]);
  const category = text(search["category"]);
  const result = text(search["result"]);
  const correlation = text(search["correlation"]);
  return {
    ...(q !== undefined ? { q } : {}),
    ...(category !== undefined ? { category } : {}),
    ...(result !== undefined ? { result } : {}),
    ...(correlation !== undefined ? { correlation } : {}),
  };
}

export function isFiltered(search: AuditSearch): boolean {
  return search.q !== undefined || search.category !== undefined || search.result !== undefined || search.correlation !== undefined;
}

/** The outcomes the log records, as the server writes them ("success" reads as "ok": one choice). */
export const RESULTS = ["ok", "failure", "denied", "warning"] as const;

/** An outcome in the state language: done, refused, failed, or worth a look. */
export function outcomeView(t: T, result: string): { status: Status; label: string } {
  switch (result) {
    case "ok":
    case "success":
      return { status: "running", label: t("common.words.done") };
    case "failure":
    case "error":
      return { status: "failed", label: t("common.statusPill.failed") };
    case "denied":
      return { status: "failed", label: t("audit.outcome.denied") };
    case "warning":
      return { status: "warning", label: t("common.statusPill.warning") };
    default:
      return { status: "unknown", label: result };
  }
}

/**
 * Who acted, in the words Activity uses for the same credential (item 152): the master token is "The master token"
 * here as there, a person is their name, a token is `Token "ci"`. `raw` is what the server recorded.
 */
export function actorWordsOf(t: T, entry: Pick<AuditEntry, "actor" | "who">): ActorWords {
  return entry.who !== null && entry.who !== undefined ? describeWho(t, entry.who, entry.actor) : describeActor(t, entry.actor);
}

/**
 * The role line under the actor. The events of the master token carry the way it holds the console (`break_glass`,
 * `recovery`, `compat`) where an account carries its role: said as what it means, never as the word the server wrote.
 */
export function actorRoleWords(t: T, entry: Pick<AuditEntry, "who">): string | null {
  const role = entry.who?.role;
  if (role === undefined || role === null || role === "") return null;
  if (entry.who?.kind === "master" && (role === "compat" || role === "break_glass" || role === "recovery")) return t(`audit.grant.${role}`);
  return roleLabel(t, role);
}

/** Words to match against the loaded events. */
export function matches(entry: AuditEntry, q: string | undefined): boolean {
  const needle = q?.trim().toLowerCase() ?? "";
  if (needle === "") return true;
  return [entry.action, entry.actor, entry.who?.name ?? "", entry.resource ?? "", entry.detail ?? "", entry.client_ip ?? "", entry.correlation_id ?? ""].some((value) =>
    value.toLowerCase().includes(needle),
  );
}

/** A category in words; one this console does not know, as the server wrote it. */
export function categoryLabel(t: T, category: string): string {
  if (!hasMessage(`auditEvents.category.${category}`)) return category;
  return t(`auditEvents.category.${category}` as PlainKey);
}
