/**
 * Prints the table of click budgets at the end of a run: what 3.3.1 took, the budget and what each
 * walk measured, per theme. It is a reporter beside the list, not a test, so the walks stay independent
 * (they run in any worker, in any order) and the table is still one:
 *
 *   NOUST_E2E_STATIC=<build> npx playwright test click-budget --workers=1 --reporter=list,./e2e/click-table-reporter.ts
 *
 * With `NOUST_E2E_MEASURE=1` on the build of 3.3.1 it is the "before" column of the integration's table.
 */

import type { Reporter, TestCase, TestResult } from "@playwright/test/reporter";

import { renderTable } from "./budgets";
import type { Measure } from "./budgets";

export default class ClickTableReporter implements Reporter {
  private readonly measures: Measure[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    for (const note of [...test.annotations, ...result.annotations]) {
      if (note.type !== "clicks" || note.description === undefined) continue;
      const [id, project, said] = note.description.split("\t");
      if (id === undefined || project === undefined || said === undefined) continue;
      const count = said === "no path" ? null : Number(said);
      const duplicate = this.measures.some((seen) => seen.id === id && seen.project === project && seen.count === count);
      if (!duplicate) this.measures.push({ id, project, count });
    }
  }

  onEnd(): void {
    if (this.measures.length === 0) return;
    console.log(`\nCLICK BUDGETS\n${renderTable(this.measures)}\n`);
  }

  printsToStdio(): boolean {
    return false;
  }
}
