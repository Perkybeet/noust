/**
 * A failed deployment's page against the real backend: the first thing an operator wants from it
 * is the error and the way to try again, so both are in view without scrolling on a desktop and
 * on a phone, the version that still serves is named, and the app's own "deploy failed" banner
 * does not say the same thing a second time above the page. Runs in both themes, with the CSP
 * and console gates of the `problems` fixture.
 *
 * The seeded machine (scripts/console_server.py, "an application's tabs"): tienda.example.org is
 * on releases and has a build that failed on a type error, with the release removed and the
 * previous one still serving.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const APP = "tienda.example.org";
const ERROR_LINE = "Type error: Property 'total' does not exist on type 'Order'.";

interface Row {
  id: number;
  status: string;
  error: string | null;
}

async function failedBuild(page: Page): Promise<number> {
  const response = await page.request.get(`/api/deployments?domain=${APP}&limit=50`);
  const row = ((await response.json()) as { items: Row[] }).items.find((item) => item.status === "failed" && (item.error ?? "").includes("Type error"));
  if (!row) throw new Error(`no seeded failed build of ${APP}`);
  return row.id;
}

const SIZES = [
  { name: "a desktop", width: 1440, height: 900 },
  { name: "a phone", width: 390, height: 844 },
] as const;

for (const size of SIZES) {
  test(`on ${size.name} the error and the way to retry are in view, and the version that serves is named`, async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, consoleServer, `/apps/${APP}/deployments`);
    const id = await failedBuild(page);
    await page.goto(`/apps/${APP}/deployments/${String(id)}`);

    const title = page.getByText("The deploy failed while building");
    await expect(title).toBeVisible();
    await expect(title).toBeInViewport();
    // The error in the system's own words, and the line that names the fault, before any log.
    const error = page.locator("pre").filter({ hasText: ERROR_LINE }).first();
    await expect(error).toBeVisible();
    await expect(error).toBeInViewport();
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeInViewport();

    // What is still true: a build that failed took nothing down.
    await expect(page.getByText(/^Version #\d+ (still serves|kept serving)/).first()).toBeVisible();
    // The app's banner says the same thing the page does; one of them is enough.
    await expect(page.getByText(/^The last deploy failed/)).toHaveCount(0);

    await settle(page);
    await expectNoA11yViolations(page, `a failed build on ${size.name}`);
  });
}

test("a failed build offers to retry without the cache, and to fix the environment, from the page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/deployments`);
  const id = await failedBuild(page);
  await page.goto(`/apps/${APP}/deployments/${String(id)}`);

  // One verb with its variation beside it; neither is pressed, so the worker's app keeps its state.
  await page.getByRole("button", { name: "More options for Retry" }).click();
  await expect(page.getByRole("menuitem", { name: "Retry without cache" })).toBeVisible();
  await page.keyboard.press("Escape");

  const edit = page.getByRole("link", { name: "Edit environment" });
  await expect(edit).toHaveAttribute("href", `/apps/${APP}/environment`);
  await expect(page.getByRole("link", { name: "Open Diagnose" })).toHaveAttribute("href", `/apps/${APP}/diagnose`);
  await expect(page.getByRole("link", { name: "Show it in the log" })).toBeVisible();
});

test("the phases of a failed build are a list down the page on a phone, and a row from a tablet up", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}/deployments`);
  const id = await failedBuild(page);
  const phases = page.getByRole("list", { name: "Deploy phases" });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/apps/${APP}/deployments/${String(id)}`);
  const first = await phases.locator("[data-phase]").first().boundingBox();
  const last = await phases.locator("[data-phase]").last().boundingBox();
  if (!first || !last) throw new Error("the phases are not drawn");
  expect(last.y).toBeGreaterThan(first.y + first.height);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.setViewportSize({ width: 1440, height: 900 });
  await settle(page);
  const wideFirst = await phases.locator("[data-phase]").first().boundingBox();
  const wideLast = await phases.locator("[data-phase]").last().boundingBox();
  if (!wideFirst || !wideLast) throw new Error("the phases are not drawn");
  expect(wideLast.x).toBeGreaterThan(wideFirst.x + wideFirst.width);
});
