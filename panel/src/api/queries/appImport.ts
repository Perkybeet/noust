import { request } from "../client";
import type { BodyOf, ResponseOf } from "../client";

export type ImportAppBody = BodyOf<"/api/apps/import", "post">;
export type AppExportDocument = ImportAppBody["document"];
export type ImportAccepted = ResponseOf<"/api/apps/import", "post">;

/** One part of an import, as the job's result reports it: `part` and `detail` are the server's words. */
export interface ImportStepReport {
  part: string;
  applied: boolean;
  detail: string;
}

/** What an import did (`report_summary` on the server): every step, and the ones not applied. */
export interface ImportReport {
  domain: string;
  steps: ImportStepReport[];
  notApplied: ImportStepReport[];
}

/**
 * Queues the creation of an application from an export. Sudo mode: the client asks the
 * operator to confirm it's them when the server answers `elevation_required`, then retries.
 */
export function importApp(body: ImportAppBody): Promise<ImportAccepted> {
  return request("post", "/api/apps/import", { body });
}

function stepsOf(value: unknown): ImportStepReport[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry: unknown) => {
    if (typeof entry !== "object" || entry === null) return [];
    const { part, applied, detail } = entry as { part?: unknown; applied?: unknown; detail?: unknown };
    if (typeof part !== "string") return [];
    return [{ part, applied: applied === true, detail: typeof detail === "string" ? detail : "" }];
  });
}

/** The report an import job's result carries, or null when it carries none (still running, or failed). */
export function importReportOf(result: unknown): ImportReport | null {
  if (typeof result !== "object" || result === null) return null;
  const { domain, steps, not_applied: notApplied } = result as { domain?: unknown; steps?: unknown; not_applied?: unknown };
  if (!Array.isArray(steps)) return null;
  return { domain: typeof domain === "string" ? domain : "", steps: stepsOf(steps), notApplied: stepsOf(notApplied) };
}
