/**
 * The approval policy as the page edits it: the rows the server sent, the cells a person changed and not yet saved, and the body
 * that saves them. Pure. The server decides what is offered in each cell (the floor, the key, the narrow row of the assistant, the
 * security profile); this never invents a value, it keeps what was chosen among what was offered.
 */

import type { PolicyCellInfo, PolicyChange, PolicyRow, PolicyTable } from "../../../api/queries/callerPolicy";
import type { PolicyCellModel, PolicyReason, PolicyRowModel, PolicyValue } from "../../../components/page/PolicyMatrix";

/** What a person changed and has not saved: the value each edited cell should have, by its key. */
export type Edits = Readonly<Record<string, PolicyValue>>;

export function cellKey(permission: string, caller: string, environment: string): string {
  return `${permission}|${caller}|${environment}`;
}

const VALUES: readonly PolicyValue[] = ["alone", "propose", "never"];
const REASONS: readonly PolicyReason[] = ["assistant_floor", "confirm", "assistant_narrow", "profile_floor"];

function valueOf(value: string): PolicyValue {
  return VALUES.find((candidate) => candidate === value) ?? "never";
}

function reasonOf(reason: string | null | undefined): PolicyReason | null {
  return REASONS.find((candidate) => candidate === reason) ?? null;
}

/** The areas in the order a person looks for them, the ones the console knows first and any the server adds after. */
export const AREA_ORDER = ["apps", "databases", "backups", "docker", "server", "fleet", "secrets", "root_equivalent", "accounts", "teams", "audit", "security", "settings"] as const;

/**
 * One row of the table as the matrix draws it, with what has been edited on top of what is saved.
 *
 * @param row - The row the server sent.
 * @param edits - The unsaved edits.
 * @param label - The row's words in this console's language.
 * @param floor - The line of the assistant's floor that applies to the row, when one does.
 * @returns The row model.
 */
export function rowModel(row: PolicyRow, edits: Edits, label: string, floor: string | null): PolicyRowModel {
  const cells: PolicyCellModel[] = row.cells.map((cell) => {
    const edited = edits[cellKey(row.permission, cell.caller, cell.environment)];
    return {
      caller: cell.caller === "assistant" ? "assistant" : "token",
      environment: cell.environment === "other" ? "other" : "production",
      value: edited ?? valueOf(cell.value),
      default: valueOf(cell.default),
      offered: cell.offered.map(valueOf),
      fixed: cell.fixed,
      reason: reasonOf(cell.reason),
      legacy: cell.legacy,
    };
  });
  return { permission: row.permission, label, key: row.key, floor, cells };
}

/** The cell a person changed, among the table's, or undefined when the key matches none. */
function findCell(table: PolicyTable, key: string): { row: PolicyRow; cell: PolicyCellInfo } | undefined {
  for (const row of table.rows) {
    for (const cell of row.cells) {
      if (cellKey(row.permission, cell.caller, cell.environment) === key) return { row, cell };
    }
  }
  return undefined;
}

/**
 * The edits that really change something: a cell put back to the value it already has is not a change.
 *
 * @param table - What is saved.
 * @param edits - What the person set.
 * @returns The keys that differ.
 */
export function effectiveEdits(table: PolicyTable, edits: Edits): string[] {
  return Object.keys(edits).filter((key) => {
    const found = findCell(table, key);
    return found !== undefined && edits[key] !== valueOf(found.cell.value);
  });
}

/**
 * The body that saves the edits. A cell put back to the factory's value is a reset (it stops being a choice and follows the version
 * again); every other edit sets the value.
 *
 * @param table - What is saved.
 * @param edits - What the person set.
 * @returns The request body.
 */
export function changeBody(table: PolicyTable, edits: Edits): PolicyChange {
  const cells: PolicyChange["cells"] = [];
  const reset: PolicyChange["reset"] = [];
  for (const key of effectiveEdits(table, edits)) {
    const found = findCell(table, key);
    const value = edits[key];
    if (found === undefined || value === undefined) continue;
    const ref = { caller: found.cell.caller, environment: found.cell.environment, permission: found.row.permission };
    if (value === valueOf(found.cell.default) && found.cell.chosen !== null && found.cell.chosen !== undefined) reset.push(ref);
    else cells.push({ ...ref, value });
  }
  return { cells, reset };
}

/** Whether a row has any cell somebody chose: the "Changed" filter keeps these and the rows being edited. */
export function rowChanged(row: PolicyRow, edits: Edits): boolean {
  return row.changed || row.cells.some((cell) => edits[cellKey(row.permission, cell.caller, cell.environment)] !== undefined);
}
