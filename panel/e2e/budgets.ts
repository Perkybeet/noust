/**
 * The click budgets of the console's most frequent tasks, as data (docs/superpowers/specs, section 7 of
 * the 4.0 navigation design: "the 15 tasks"), and the table they print.
 *
 * click-budget.spec.ts walks each row; `was` is what the 3.3.1 console took (the design's "today"
 * column, measured again on its committed build with `NOUST_E2E_MEASURE=1`), `budget` the most the 4.0
 * console may take, `depth` the number of segments of the address the task ends on. A number that goes
 * up needs a sentence in the commit that raises it (NV-3) and a row in the table of the integration.
 *
 * Nothing here runs a browser, so a unit test and a reporter can read it.
 */

/** Where a task starts from: the machine the walk needs. */
export type Machine = "seeded" | "empty" | "fleet";

export interface Budget {
  /** "T09", "T07c": a task of the design and, when it has several ways in, the way. */
  id: string;
  name: string;
  /** The most clicks the task may take. */
  budget: number;
  /** The most segments the address it ends on may have (`/apps/$d/deployments/$id` is 4). */
  depth: number;
  /** The page the walk starts from, in words. */
  from: string;
  /** What the 3.3.1 console took, or null when it had no way to do it. */
  was: number | null;
  machine: Machine;
  /** Sudo mode is needed and set up before the count, as an operator who is already in it. */
  sudo?: true;
  /** Walked on a phone (390 by 844) and not on a desk. */
  phone?: true;
}

export const BUDGETS: readonly Budget[] = [
  { id: "T01", name: "from a server with nothing on it to a live address, with the demo", budget: 1, depth: 4, from: "the empty Overview", was: null, machine: "empty" },
  { id: "T02a", name: "a public repository to its first build, on a server with no domain of its own", budget: 3, depth: 4, from: "Applications", was: 4, machine: "empty" },
  { id: "T02b", name: "a public repository to its first build, on a server with domains", budget: 4, depth: 4, from: "Applications", was: 4, machine: "seeded" },
  { id: "T02p", name: "a public repository to its first build, on a phone", budget: 4, depth: 4, from: "Applications", was: 4, machine: "seeded", phone: true },
  { id: "T03a", name: "a recipe to its first build", budget: 3, depth: 3, from: "Applications", was: 3, machine: "seeded" },
  { id: "T03b", name: "a recipe to its first build, from the empty Overview", budget: 2, depth: 3, from: "the empty Overview", was: null, machine: "empty" },
  { id: "T04", name: "following the build live after an update", budget: 2, depth: 4, from: "the application", was: 3, machine: "seeded" },
  { id: "T04b", name: "the build log of the last deploy", budget: 2, depth: 4, from: "the application", was: 2, machine: "seeded" },
  { id: "T05", name: "the cause of a failed deploy, with its first error line and Retry in view", budget: 1, depth: 4, from: "the application, with its banner", was: 1, machine: "seeded" },
  { id: "T06", name: "retrying a failed deploy", budget: 1, depth: 4, from: "the failed deployment", was: 2, machine: "seeded" },
  { id: "T07a", name: "going back, from the application's header", budget: 2, depth: 3, from: "the application", was: 2, machine: "seeded" },
  { id: "T07b", name: "going back, from a deployment's page", budget: 2, depth: 4, from: "a deployment", was: 3, machine: "seeded" },
  { id: "T07c", name: "going back, from a row of the history", budget: 3, depth: 3, from: "the application's Deployments", was: 3, machine: "seeded" },
  { id: "T07o", name: "going back: opening the question", budget: 1, depth: 3, from: "the application", was: 1, machine: "seeded" },
  { id: "T07p", name: "going back: opening the question, on a phone", budget: 1, depth: 3, from: "the application", was: 1, machine: "seeded", phone: true },
  { id: "T08a", name: "restarting", budget: 1, depth: 2, from: "the application", was: 1, machine: "seeded" },
  { id: "T08b", name: "stopping, which asks first", budget: 2, depth: 2, from: "the application", was: 2, machine: "seeded" },
  { id: "T08p", name: "restarting, on a phone", budget: 1, depth: 2, from: "the application", was: 1, machine: "seeded", phone: true },
  { id: "T09", name: "changing a variable and applying it", budget: 4, depth: 3, from: "the application", was: 7, machine: "seeded", sudo: true },
  { id: "T10", name: "reading the live log and keeping the errors", budget: 2, depth: 3, from: "the application", was: 2, machine: "seeded" },
  { id: "T10b", name: "the log of what runs", budget: 1, depth: 3, from: "the application", was: 1, machine: "seeded" },
  { id: "T10p", name: "the last lines of the log, on a phone", budget: 1, depth: 3, from: "the application", was: 1, machine: "seeded", phone: true },
  { id: "T11", name: "adding a domain with TLS", budget: 3, depth: 3, from: "the application", was: 4, machine: "seeded" },
  { id: "T12", name: "changing the memory limit", budget: 2, depth: 4, from: "the application", was: 3, machine: "seeded", sudo: true },
  { id: "T13", name: "going to what needs me", budget: 2, depth: 4, from: "any page", was: null, machine: "seeded" },
  { id: "T14", name: "finding a page by its name, with the pointer", budget: 2, depth: 4, from: "any page", was: null, machine: "seeded" },
  { id: "T15a", name: "changing to another server and staying on the same page", budget: 2, depth: 2, from: "any page, on a fleet", was: 3, machine: "fleet" },
  { id: "T15b", name: "changing to all servers: the sidebar becomes the fleet's", budget: 2, depth: 2, from: "any page, on a fleet", was: 3, machine: "fleet" },
];

/** The row of a task. A walk naming an id that is not in the table is a typo, so this throws. */
export function budgetOf(id: string): Budget {
  const found = BUDGETS.find((row) => row.id === id);
  if (found === undefined) throw new Error(`budgets.ts has no task "${id}"`);
  return found;
}

/** One measurement: what a walk reported for a task in a project (a theme). */
export interface Measure {
  id: string;
  project: string;
  /** Null when the console under test had no way to do it. */
  count: number | null;
}

/**
 * The table of every task: what 3.3.1 took, the budget, and what was measured, per theme when the
 * themes disagree. A task nobody measured shows "-".
 *
 * @param measures - What the walks reported.
 * @returns The table as plain text, for the report of an integration.
 */
export function renderTable(measures: readonly Measure[]): string {
  const cell = (value: number | null | undefined): string => (value === undefined ? "-" : value === null ? "no path" : String(value));
  const rows = BUDGETS.map((row) => {
    const seen = measures.filter((measure) => measure.id === row.id);
    const distinct = [...new Set(seen.map((measure) => cell(measure.count)))];
    const now = seen.length === 0 ? "-" : distinct.join(" / ");
    return [row.id, row.name, row.was === null ? "none" : String(row.was), String(row.budget), now, String(row.depth), row.sudo ? "+sudo" : ""];
  });
  const head = ["id", "task", "3.3.1", "budget", "measured", "depth", ""];
  const widths = head.map((title, column) => Math.max(title.length, ...rows.map((row) => (row[column] ?? "").length)));
  const line = (row: readonly string[]): string => row.map((text, column) => text.padEnd(widths[column] ?? 0)).join("  ").trimEnd();
  return [line(head), line(widths.map((width) => "-".repeat(width))), ...rows.map(line)].join("\n");
}
