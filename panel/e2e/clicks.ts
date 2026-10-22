/**
 * What every click-counting spec shares (click-budget.spec.ts, nav-reach.spec.ts): the counter, the
 * report that says the number whatever the budget, and the few moves a walk needs beside the clicks it
 * counts (sudo mode set up without one, the depth of an address).
 *
 * A click is anything that activates a control: a pointer press and release, or Enter on a button.
 * The counter listens in the capture phase of the document and counts only what the browser itself
 * dispatched (`isTrusted`), so a script's `.click()` does not count and typing is not a click. The
 * sudo confirmation a destructive action needs is not counted either: the session is put in sudo mode
 * first with `elevate`, and the flow is counted as it would be for an operator who is already in it.
 *
 * The walks go by what is on screen, not by the steps there are, so the same spec runs against the build
 * before the change (`NOUST_E2E_STATIC=<3.3.1 build> NOUST_E2E_MEASURE=1`), where it reports the number
 * without asserting the budget, and says "no path" for a task the old console cannot do.
 */

import type { Locator, Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/** Only measures: reports the count and leaves the budget unasserted (to run on the old build). */
export const MEASURE_ONLY = process.env.NOUST_E2E_MEASURE === "1";

declare global {
  interface Window {
    __noustClicks?: number;
  }
}

/** Counts every activation the page receives, from every document it loads. */
export async function countClicks(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.__noustClicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) window.__noustClicks = (window.__noustClicks ?? 0) + 1;
      },
      true,
    );
  });
}

/** Starts the count at zero: the operator is where the task starts from. */
export async function startCounting(page: Page): Promise<void> {
  await page.evaluate(() => {
    window.__noustClicks = 0;
  });
}

/** The activations since `startCounting`. */
export async function clicks(page: Page): Promise<number> {
  return page.evaluate(() => window.__noustClicks ?? 0);
}

/**
 * Says the measured number in the report, whatever the budget: an annotation (which
 * click-table-reporter.ts gathers into the table) and a line of output.
 *
 * @param id - The task's id in `BUDGETS` ("T09").
 * @param name - What was walked.
 * @param count - The clicks, or null when the console under test has no way to do it.
 */
export function report(id: string, name: string, count: number | null): void {
  const said = count === null ? "no path" : String(count);
  test.info().annotations.push({ type: "clicks", description: `${id}\t${test.info().project.name}\t${said}\t${name}` });
  console.log(`CLICKS ${id} ${name}: ${said}`);
}

/**
 * Reports the count and, unless the run only measures, fails when it is over the budget.
 *
 * @param id - The task's id in `BUDGETS`.
 * @param name - What was walked.
 * @param count - The clicks.
 * @param budget - The most that is allowed.
 */
export function withinBudget(id: string, name: string, count: number, budget: number): void {
  report(id, name, count);
  if (!MEASURE_ONLY) expect(count, `${id} ${name} takes ${String(count)} clicks; the budget is ${String(budget)}`).toBeLessThanOrEqual(budget);
}

/** Sudo mode without a click: a public repository never asks, a local directory does. */
export async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const elevated = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(elevated.ok(), await elevated.text()).toBe(true);
}

/** The segments of an address, after the node prefix `/n/<server>` a fleet adds: `/apps/shop/deployments/7` is 4. */
export function depthOf(url: string): number {
  const segments = new URL(url, "http://console.invalid").pathname.split("/").filter(Boolean);
  return segments[0] === "n" ? segments.length - 2 : segments.length;
}

/** Whether a locator is wholly inside the window (not only touching it), for the first screen of a failure. */
export async function inViewport(locator: Locator): Promise<void> {
  await expect(locator).toBeInViewport({ ratio: 1 });
}
