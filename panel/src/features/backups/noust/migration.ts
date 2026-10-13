/**
 * The result of the job that prepares a move to another server, read back from the job: the
 * steps it took in order, whether the state was sealed, and what to run on the new server.
 */

export interface MigrationStepView {
  kind: string;
  name: string;
  state: string;
  detail: string;
  backup: string | null;
}

export interface MigrationReportView {
  host: string;
  destination: string;
  ok: boolean;
  bundle: string | null;
  steps: MigrationStepView[];
  nextSteps: string[];
  warnings: string[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/** Read a job's result as a preparation report; null for any other kind of result. */
export function readMigrationReport(result: unknown): MigrationReportView | null {
  if (!isRecord(result) || !Array.isArray(result["steps"]) || typeof result["host"] !== "string") return null;
  const steps: MigrationStepView[] = [];
  for (const entry of result["steps"] as unknown[]) {
    if (!isRecord(entry)) continue;
    steps.push({
      kind: typeof entry["kind"] === "string" ? entry["kind"] : "",
      name: typeof entry["name"] === "string" ? entry["name"] : "",
      state: typeof entry["state"] === "string" ? entry["state"] : "",
      detail: typeof entry["detail"] === "string" ? entry["detail"] : "",
      backup: typeof entry["backup"] === "string" ? entry["backup"] : null,
    });
  }
  return {
    host: result["host"],
    destination: typeof result["destination"] === "string" ? result["destination"] : "",
    ok: result["ok"] === true,
    bundle: typeof result["bundle"] === "string" ? result["bundle"] : null,
    steps,
    nextSteps: strings(result["next_steps"]),
    warnings: strings(result["warnings"]),
  };
}
