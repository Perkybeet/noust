/**
 * The charts' reading and enlarging against the real backend: a card beside the cursor with the
 * reading (dismissable with Escape, pinned with a click), the same moment marked in every chart of
 * the grid, the arrow keys stepping through the readings and saying each one politely, and Expand
 * opening the chart large, where zooming reads the stretch again and the numbers are a tab of the
 * same height, never a reflow of the page. The Overview's machine charts are fed by the live
 * collector (a young history); an application's charts by thirty days of seeded history. Both
 * themes, with axe and the CSP and console gates of every test.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";

function figure(page: Page, title: string): Locator {
  return page.getByRole("region", { name: "Machine" }).locator("figure").filter({ has: page.getByRole("heading", { name: title, exact: true }) });
}

/** The Overview on its last hour, where the live collector's few minutes of readings are. */
async function lastHour(page: Page, consoleServer: Parameters<typeof signIn>[1]): Promise<Locator> {
  await signIn(page, consoleServer, "/?window=1h");
  const cpu = figure(page, "CPU");
  await expect(cpu.getByRole("img", { name: /^CPU, last hour/ })).toBeVisible();
  // The collector records every few seconds: wait for a line to point at.
  await expect(cpu.locator("[data-chart-stats]")).toHaveText(/^Average /, { timeout: 30_000 });
  return cpu;
}

/**
 * Points at a fraction of the plot area's width (uPlot's own overlay, which is the area between
 * the axes; the newest readings are at its right edge), halfway down.
 */
async function pointAt(page: Page, plot: Locator, fraction: number): Promise<void> {
  const area = plot.locator(".u-over");
  await area.scrollIntoViewIfNeeded();
  const box = await area.boundingBox();
  if (box === null) throw new Error("the plot is not on screen");
  await page.mouse.move(box.x + Math.min(box.width - 1, box.width * fraction), box.y + box.height / 2, { steps: 4 });
}

test("hovering reads the moment in a card beside the cursor, and in every chart of the grid", async ({ page, consoleServer }) => {
  const cpu = await lastHour(page, consoleServer);
  const memory = figure(page, "Memory");
  await pointAt(page, cpu.getByRole("img"), 1);
  const card = cpu.locator("[data-chart-card]");
  await expect(card).toHaveAttribute("data-chart-card", "floating");
  // The newest cell may be empty yet (the read ended a moment after the last tick): the card
  // then says so in words, never a blank.
  await expect(card).toContainText(/\d+(\.\d+)?%|No reading: nothing was recorded at this moment\./);
  // The same moment in the row of the chart beside it, without a card of its own.
  await expect(memory.locator("[data-readout-time] time")).toHaveAttribute("datetime", /^\d{4}-/);
  await expect(memory.locator("[data-chart-card]")).toHaveAttribute("data-chart-card", "hidden");
  const cpuTime = await cpu.locator("[data-readout-time] time").getAttribute("datetime");
  await expect(memory.locator("[data-readout-time] time")).toHaveAttribute("datetime", cpuTime ?? "");

  // WCAG 1.4.13: Escape puts the card away without moving the pointer.
  await page.keyboard.press("Escape");
  await expect(card).toHaveAttribute("data-chart-card", "hidden");

  // A click pins it: it stays, and another click elsewhere lets it go.
  await pointAt(page, cpu.getByRole("img"), 1);
  await page.mouse.down();
  await page.mouse.up();
  await expect(card).toHaveAttribute("data-chart-card", "pinned");
  await settle(page);
  await expectNoA11yViolations(page, "the overview with a pinned reading");
  await page.mouse.click(5, 300);
  await expect(card).not.toHaveAttribute("data-chart-card", "pinned");

  // Off the chart: back to the newest values everywhere.
  await page.mouse.move(5, 5);
  await expect(cpu.locator("[data-readout-time]")).toHaveText("Latest");
  await expect(memory.locator("[data-readout-time]")).toHaveText("Latest");
});

test("the arrow keys step through the readings and say each one", async ({ page, consoleServer }) => {
  const cpu = await lastHour(page, consoleServer);
  const chart = cpu.getByRole("application", { name: "CPU" });
  await chart.focus();
  await page.keyboard.press("End");
  const status = cpu.getByRole("status");
  // The newest cell may not have its reading yet: then it says so, in words.
  await expect(status).toHaveText(/^(?:[A-Z][a-z]{2} \d{1,2} )?\d{2}:\d{2}:\d{2}, CPU (?:\d+(\.\d+)?%|no reading)/);
  await page.keyboard.press("PageUp");
  await expect(cpu.locator("[data-readout-time] time")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(cpu.locator("[data-chart-card]")).toHaveAttribute("data-chart-card", "pinned");
  await page.keyboard.press("Escape");
  await expect(cpu.locator("[data-readout-time]")).toHaveText("Latest");
  await settle(page);
  await expectNoA11yViolations(page, "the overview with a chart focused");
});

test("Expand opens the chart large; its data is a tab, and the page behind never moves", async ({ page, consoleServer }) => {
  const cpu = await lastHour(page, consoleServer);
  const grid = page.getByRole("region", { name: "Machine" });
  const before = await Promise.all(["CPU", "Memory", "Network", "Disk"].map((title) => figure(page, title).boundingBox()));
  await cpu.getByRole("button", { name: "Expand CPU" }).click();
  const dialog = page.getByRole("dialog", { name: "CPU" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("[data-truth]")).toHaveText(/^Showing .+, a reading every 5 seconds/);
  const plotBox = await dialog.getByRole("img").boundingBox();
  expect(plotBox?.height ?? 0).toBeGreaterThanOrEqual(220);

  // The numbers: a tab of the same height, with the time to the second.
  const panel = dialog.getByRole("tabpanel");
  const chartHeight = (await panel.boundingBox())?.height ?? 0;
  await dialog.getByRole("tab", { name: "Data" }).click();
  const region = dialog.getByRole("region", { name: "CPU data" });
  await expect(region).toBeVisible();
  await expect(region.getByRole("row").nth(1).getByRole("cell").first()).toHaveText(/\d{2}:\d{2}:\d{2}$/);
  expect(Math.abs(((await dialog.getByRole("tabpanel").boundingBox())?.height ?? 0) - chartHeight)).toBeLessThanOrEqual(1);
  await settle(page);
  await expectNoA11yViolations(page, "an enlarged chart's data");

  // The page's range, from inside the dialog: the URL follows and the dialog stays.
  await dialog.getByRole("tab", { name: "Chart" }).click();
  await dialog.getByRole("radio", { name: "24h" }).click();
  await expect(page).not.toHaveURL(/window=/);
  await expect(dialog.getByRole("img", { name: /^CPU, last 24 hours/ })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  // Nothing in the grid moved: the table never lived on the page.
  await page.goto("/?window=1h");
  await expect(grid.getByRole("img", { name: /^CPU, last hour/ })).toBeVisible();
  const after = await Promise.all(["CPU", "Memory", "Network", "Disk"].map((title) => figure(page, title).boundingBox()));
  after.forEach((box, i) => {
    expect(box?.height).toBe(before[i]?.height);
    expect(box?.width).toBe(before[i]?.width);
  });
});

test("an enlarged app chart zooms by reading the dragged stretch again, and resets", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/metrics`);
  const figureCpu = page.locator("figure").filter({ has: page.getByRole("heading", { name: "CPU", exact: true }) }).first();
  await expect(figureCpu.getByRole("img", { name: /^CPU, last 24 hours/ })).toBeVisible();
  await figureCpu.getByRole("button", { name: "Expand CPU" }).click();
  const dialog = page.getByRole("dialog", { name: "CPU" });
  const plot = dialog.getByRole("img", { name: /^CPU, last 24 hours/ });
  await expect(plot).toBeVisible();
  const truth = dialog.locator("[data-truth]");
  await expect(truth).toHaveText(/1-minute averages/);
  await settle(page);

  const reset = dialog.getByRole("button", { name: "Reset zoom" });
  await expect(reset).toBeDisabled();
  const box = await plot.boundingBox();
  if (box === null) throw new Error("the plot is not on screen");
  const y = box.y + box.height / 2;
  // The last two hours of the day: the seed's raw readings are ten seconds apart there.
  await page.mouse.move(box.x + box.width * 0.92, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.995, y, { steps: 8 });
  await page.mouse.up();
  await expect(reset).toBeEnabled();
  // Read again at a finer step than the day's minute: the sentence says what came back.
  await expect(truth).not.toHaveText(/1-minute averages/);
  const zoomed = dialog.getByRole("img");
  const from = Number(await zoomed.getAttribute("data-domain-from"));
  const to = Number(await zoomed.getAttribute("data-domain-to"));
  expect(to - from).toBeLessThan(4 * 3_600);

  await reset.click();
  await expect(reset).toBeDisabled();
  await expect(truth).toHaveText(/1-minute averages/);

  // Zooming without dragging, for anyone who cannot drag.
  await dialog.getByRole("button", { name: "Zoom in" }).click();
  await expect(reset).toBeEnabled();
  await dialog.getByRole("button", { name: "Zoom out" }).click();
  await expect(reset).toBeDisabled();

  // A new range starts whole.
  await dialog.getByRole("button", { name: "Zoom in" }).click();
  await dialog.getByRole("radio", { name: "7d" }).click();
  await expect(page).toHaveURL(/[?&]range=7d/);
  await expect(dialog.getByRole("img", { name: /^CPU, last 7 days/ })).toBeVisible();
  await expect(reset).toBeDisabled();
  await settle(page);
  await expectNoA11yViolations(page, "an enlarged app chart");
});
