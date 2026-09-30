/**
 * The audit log page beyond the raw API shape: its filters (in the URL), an event's outcome in
 * the state language, and who acted, in words.
 */

import type { AuditEntry } from "../../api/queries/audit";
import type { Status } from "../../components/ui/StatusPill";
import type { T } from "../../i18n";

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

/** The outcomes the log records, as the server writes them. */
export const RESULTS = ["ok", "success", "failure", "denied", "warning"] as const;

/** An outcome in the state language: done, refused, failed, or worth a look. */
export function outcomeView(t: T, result: string): { status: Status; label: string } {
  switch (result) {
    case "ok":
    case "success":
      return { status: "running", label: t("audit.outcome.ok") };
    case "failure":
    case "error":
      return { status: "failed", label: t("audit.outcome.failure") };
    case "denied":
      return { status: "failed", label: t("audit.outcome.denied") };
    case "warning":
      return { status: "warning", label: t("audit.outcome.warning") };
    default:
      return { status: "unknown", label: result };
  }
}

/** Who acted: the structured actor's name, else the label the line was written with. */
export function actorName(entry: Pick<AuditEntry, "actor" | "who">): string {
  return entry.who?.name ?? entry.actor;
}

/** Words to match against the loaded events. */
export function matches(entry: AuditEntry, q: string | undefined): boolean {
  const needle = q?.trim().toLowerCase() ?? "";
  if (needle === "") return true;
  return [entry.action, entry.actor, entry.who?.name ?? "", entry.resource ?? "", entry.detail ?? "", entry.client_ip ?? "", entry.correlation_id ?? ""].some((value) =>
    value.toLowerCase().includes(needle),
  );
}
