/**
 * "Investigate this stretch" against the real backend (owner item 64): in an enlarged chart, a
 * dragged selection or the window on screen opens the timeline of that stretch in a drawer over
 * the chart, with the processes that used the machine most in each minute (seeded by
 * scripts/console_server.py: a build of the release app forty minutes ago) and every event in
 * order. Both a server chart and an application's, with axe and the CSP and console gates of
 * every test.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";

test("an app chart investigates the dragged stretch: the busiest processes and what the monitor saw", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/metrics`);
  const figureCpu = page.locator("figure").filter({ has: page.getByRole("heading", { name: "CPU", exact: true }) }).first();
  await expect(figureCpu.getByRole("img", { name: /^CPU, last 24 hours/ })).toBeVisible();
  await figureCpu.getByRole("button", { name: "Expand CPU" }).click();
  const dialog = page.getByRole("dialog", { name: "CPU" });
  const plot = dialog.getByRole("img", { name: /^CPU, last 24 hours/ });
  await expect(plot).toBeVisible();
  await settle(page);

  // The last two hours of the day, where the seeded peak is.
  const box = await plot.boundingBox();
  if (box === null) throw new Error("the plot is not on screen");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width * 0.92, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.995, y, { steps: 8 });
  await page.mouse.up();
  await expect(dialog.getByRole("button", { name: "Reset zoom" })).toBeEnabled();

  const asked = page.waitForRequest((request) => request.url().includes("/api/timeline?"));
  await dialog.getByRole("button", { name: "Investigate this stretch" }).click();
  const url = new URL((await asked).url());
  expect(url.searchParams.get("app")).toBe(DOMAIN);
  expect(Number(url.searchParams.get("end")) - Number(url.searchParams.get("start"))).toBeLessThan(4 * 3_600);

  const drawer = page.getByRole("dialog", { name: "What happened in this stretch" });
  await expect(drawer).toBeVisible();
  const cpu = drawer.getByRole("table", { name: /^The processes with the most CPU at / });
  await expect(cpu.getByRole("row").nth(1)).toContainText("node");
  await expect(cpu.getByRole("row").nth(1)).toContainText(DOMAIN);
  await expect(drawer.getByRole("region", { name: /^Events between / })).toContainText("unit_failed");
  await settle(page);
  await expectNoA11yViolations(page, "the timeline of an app's stretch");

  // Closing it goes back to the chart, still zoomed.
  await drawer.getByRole("button", { name: "Close" }).click();
  await expect(drawer).toBeHidden();
  await expect(dialog.getByRole("button", { name: "Reset zoom" })).toBeEnabled();
});

test("a server chart investigates the window on screen, from every source", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/?window=1h");
  const machine = page.getByRole("region", { name: "Machine" });
  const cpu = machine.locator("figure").filter({ has: page.getByRole("heading", { name: "CPU", exact: true }) });
  await expect(cpu.getByRole("img", { name: /^CPU, last hour/ })).toBeVisible();
  await cpu.getByRole("button", { name: "Expand CPU" }).click();
  const dialog = page.getByRole("dialog", { name: "CPU" });
  await dialog.getByRole("button", { name: "Investigate this stretch" }).click();

  const drawer = page.getByRole("dialog", { name: "What happened in this stretch" });
  await expect(drawer).toHaveAccessibleDescription(/on the whole server\.$/);
  await expect(drawer.getByRole("table", { name: "The busiest process of each minute" })).toBeVisible();
  await expect(drawer.getByRole("search", { name: "Filter events" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the timeline of the server's last hour");
});
