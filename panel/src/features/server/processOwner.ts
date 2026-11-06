/**
 * Whose a process is, in the words a person uses for it: the application it runs, or the service that started it.
 * The machine says `node` and `postgres`; what an operator asks is which of their things it is. One answer for the
 * pages that list processes (Status and Processes), so they never name the same process two ways.
 */

import { appOfUnit } from "../services/data";

export type ProcessOwner = { kind: "app"; domain: string } | { kind: "unit"; unit: string };

/**
 * The application or the service a process belongs to.
 *
 * @param process - A process of the server's list; only its unit is read.
 * @param apps - The applications of this server, to name the one whose unit it is.
 * @returns The application, else the unit, else null for a process no service started.
 */
export function ownerOfProcess(process: { unit?: string | null | undefined }, apps: readonly { domain: string; unit?: string | null | undefined }[]): ProcessOwner | null {
  const unit = process.unit;
  if (unit === null || unit === undefined || unit === "") return null;
  const app = appOfUnit(apps, unit);
  return app !== null ? { kind: "app", domain: app.domain } : { kind: "unit", unit };
}

/** What a search matches in a process: its name, its command, and the application or service it belongs to. */
export function processWords(process: { name: string; command?: string | null | undefined }, owner: ProcessOwner | null): string {
  const belongs = owner === null ? "" : owner.kind === "app" ? owner.domain : owner.unit;
  return [process.name, process.command ?? "", belongs].filter((word) => word !== "").join(" ").toLowerCase();
}
