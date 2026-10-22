// The table of click budgets (e2e/budgets.ts) is data that a browser test indexes by id and a reporter
// prints. These hold the table itself, in the unit suite that runs on every change, so that it cannot be
// loosened by accident while the end-to-end run that walks it takes an hour: no budget above what 3.3.1
// took (the design's rule: "se conservan o bajan; ninguno sube"), no task twice, every address within
// the four segments the register allows.

import { describe, expect, it } from "vitest";

import { BUDGETS, budgetOf, renderTable } from "../e2e/budgets.ts";

describe("the click budgets", () => {
  it("name each task once", () => {
    const ids = BUDGETS.map((row) => row.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(() => budgetOf("T99")).toThrow('budgets.ts has no task "T99"');
    expect(budgetOf("T09").budget).toBe(4);
  });

  it("never allow more clicks than 3.3.1 took, unless the row says why", () => {
    for (const row of BUDGETS) {
      if (row.was === null) continue;
      if (row.raised === undefined) expect(row.budget, `${row.id} may take ${String(row.budget)} and 3.3.1 took ${String(row.was)}`).toBeLessThanOrEqual(row.was);
      else expect(row.budget, `${row.id} says why it is raised and is not`).toBeGreaterThan(row.was);
    }
  });

  it("raise a budget in one place only, the row that was walked and found to need the click", () => {
    expect(BUDGETS.filter((row) => row.raised !== undefined).map((row) => row.id)).toEqual(["T07c"]);
  });

  it("end on an address no deeper than the register allows, and need no more than four clicks", () => {
    for (const row of BUDGETS) {
      expect(row.depth, row.id).toBeGreaterThanOrEqual(1);
      expect(row.depth, row.id).toBeLessThanOrEqual(4);
      expect(row.budget, row.id).toBeGreaterThanOrEqual(1);
      expect(row.budget, row.id).toBeLessThanOrEqual(4);
    }
  });

  it("cover the fifteen tasks of the design, some in more than one way in", () => {
    const tasks = new Set(BUDGETS.map((row) => /^T\d\d/.exec(row.id)?.[0]));
    expect([...tasks].sort()).toEqual(Array.from({ length: 15 }, (_, index) => `T${String(index + 1).padStart(2, "0")}`));
  });

  it("print every task with what 3.3.1 took, the budget and what was measured in each theme", () => {
    const table = renderTable([
      { id: "T09", project: "light", count: 4 },
      { id: "T09", project: "dark", count: 4 },
      { id: "T01", project: "light", count: null },
      { id: "T06", project: "light", count: 1 },
      { id: "T06", project: "dark", count: 2 },
    ]);
    const lines = table.split("\n");

    for (const row of BUDGETS) expect(table, row.id).toContain(row.id);
    expect(lines.find((line) => line.startsWith("T09"))).toMatch(/\b7\s+4\s+4\s+3\s+\+sudo$/);
    expect(lines.find((line) => line.startsWith("T01"))).toMatch(/none\s+1\s+no path\b/);
    // A task the themes disagree on shows both numbers; one nobody measured shows a dash.
    expect(lines.find((line) => line.startsWith("T06"))).toMatch(/1 \/ 2/);
    expect(lines.find((line) => line.startsWith("T15a"))).toMatch(/\s-\s/);
  });
});
