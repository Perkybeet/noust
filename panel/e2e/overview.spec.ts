/**
 * The Overview against the real backend and its seeded machine: six key figures linking to
 * where they come from, what needs attention (the server's list, in its own words, with where
 * each thing is fixed), the activity timeline, the machine's charts over exactly the window
 * asked for, and the first steps of an empty server. Both themes; every test fails on a CSP
 * violation or a console error through the `problems` fixture.
 *
 * The seeded server has no history but what its collector has recorded since it started: a
 * young history, so the 24-hour window is 24 hours of axis with a few minutes of readings, the
 * case the old seed of thirty days hid.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** The app the seed fails: its newest deploy failed with npm's own words. */
const FAILED = "clientes.example.com";

test("six key figures sit at the top, each a link to where it comes from", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  const figures = page.getByRole("region", { name: "Key figures" });
  await expect(figures.getByRole("link")).toHaveCount(6);
  await expect(figures.getByRole("link", { name: /^Applications: \d+ running, \d+ failed/ })).toHaveAttribute("href", "/apps");
  await expect(figures.getByRole("link", { name: /^Deploys today/ })).toHaveAttribute("href", "/activity");
  await expect(figures.getByRole("link", { name: /^Certificates: \d+, 1 expiring/ })).toHaveAttribute("href", "/domains");
  await expect(figures.getByRole("link", { name: /^Backups in the last 24 hours/ })).toBeVisible();
  await expect(figures.getByRole("link", { name: /^Disk: \d+% free/ })).toBeVisible();
  await expect(figures.getByRole("link", { name: /^Operating system updates/ })).toBeVisible();
  // Above the fold at 1440: the figures, and the start of what needs attention.
  await page.setViewportSize({ width: 1440, height: 900 });
  const attention = page.getByRole("region", { name: "Needs attention" });
  const box = await attention.boundingBox();
  expect(box?.y ?? 9_999).toBeLessThan(900);
});

test("a failed app is under Needs attention, in its own words, with where it is fixed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  await expect(page).toHaveURL(/\/$/);
  const attention = page.getByRole("region", { name: "Needs attention" });
  const item = attention.getByRole("listitem").filter({ has: page.getByRole("link", { name: FAILED, exact: true }) }).first();
  await expect(item).toBeVisible();
  await expect(item.getByText("Last deploy failed")).toBeVisible();
  // Verbatim, not paraphrased: the first line of the error the deploy recorded.
  await expect(item.getByText("npm ERR! code ELIFECYCLE", { exact: true })).toBeVisible();
  await expect(item.getByRole("link", { name: `Diagnose ${FAILED}` })).toHaveAttribute("href", `/apps/${FAILED}/diagnose`);
  await expect(item.getByRole("link", { name: `View log of the deploy of ${FAILED}` })).toHaveAttribute(
    "href",
    new RegExp(`^/apps/${FAILED.replace(/\./g, "\\.")}/deployments/\\d+$`),
  );
  // The failed unit that is no app's, and the certificate about to expire, are there too.
  await expect(attention.getByRole("link", { name: "queue-worker", exact: true })).toHaveAttribute("href", "/services/queue-worker");
  await expect(attention.getByText(/^Certificate expires in \d+ days$/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the overview");

  await item.getByRole("link", { name: FAILED, exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${FAILED.replace(/\./g, "\\.")}$`));
});

test("what happened lately is a timeline with its own link to all of it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  const activity = page.getByRole("group", { name: "Recent activity, newest first" });
  await expect(activity.getByRole("listitem").first()).toBeVisible();
  await expect(activity.locator("time").first()).toHaveAttribute("datetime", /^\d{4}-\d{2}-\d{2}T/);
  await expect(page.getByRole("link", { name: "All activity" })).toHaveAttribute("href", "/activity");
});

test("the charts span the window asked for, whatever part of it has history, and say so", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  const machine = page.getByRole("region", { name: "Machine" });
  await expect(machine.getByRole("radio", { name: "24h" })).toHaveAttribute("aria-checked", "true");
  const cpu = machine.getByRole("img", { name: /^CPU, last 24 hours/ });
  await expect(cpu).toBeVisible();
  const span = async () => {
    const from = Number(await cpu.getAttribute("data-domain-from"));
    const to = Number(await cpu.getAttribute("data-domain-to"));
    return to - from;
  };
  // A server a few minutes old: the axis is still a whole day, and says where history begins.
  expect(await span()).toBeGreaterThanOrEqual(86_400 - 120);
  const truth = machine.locator("[data-truth]");
  await expect(truth).toHaveText(/^Showing .+, 1-minute averages\. History since .+\.$/);
  await expect(machine.getByText(/^No history before \d{2}:\d{2}$/).first()).toBeVisible();

  // Another range is another window: the URL, the axis and the sentence all follow.
  await machine.getByRole("radio", { name: "1h" }).click();
  await expect(page).toHaveURL(/\/\?window=1h$/);
  const hour = machine.getByRole("img", { name: /^CPU, last hour/ });
  await expect(hour).toBeVisible();
  await expect(truth).toHaveText(/a reading every 5 seconds/);
  const from = Number(await hour.getAttribute("data-domain-from"));
  const to = Number(await hour.getAttribute("data-domain-to"));
  expect(to - from).toBeGreaterThanOrEqual(3_600 - 10);
  expect(to - from).toBeLessThanOrEqual(3_600 + 10);

  await page.reload();
  await expect(page.getByRole("radio", { name: "1h" })).toHaveAttribute("aria-checked", "true");
  await machine.getByRole("radio", { name: "1h" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page).not.toHaveURL(/window=/);
});

test("changing the range keeps the page where it is, and Back leaves instead of stepping ranges", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  await signIn(page, consoleServer);
  await page.goto("/activity");
  await page.goto("/");
  const machine = page.getByRole("region", { name: "Machine" });
  await expect(machine.getByRole("img", { name: /^CPU, last 24 hours/ })).toBeVisible();
  const range = machine.getByRole("radio", { name: "7d" });
  await range.scrollIntoViewIfNeeded();
  await settle(page);
  const before = await page.evaluate(() => window.scrollY);
  // The premise: the charts sit below the fold, so a jump to the top would show.
  expect(before).toBeGreaterThan(100);

  await range.click();
  await expect(page).toHaveURL(/\/\?window=7d$/);
  await expect(machine.getByRole("img", { name: /^CPU, last 7 days/ })).toBeVisible();
  await settle(page);
  expect(Math.abs((await page.evaluate(() => window.scrollY)) - before)).toBeLessThan(2);

  await page.goBack();
  await expect(page).toHaveURL(/\/activity$/);
});

test("says history is recorded only while the console runs, and the command that fixes it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  const machine = page.getByRole("region", { name: "Machine" });
  await expect(machine.getByText("History is recorded only while the console runs")).toBeVisible();
  await expect(machine.getByText("noust monitor enable")).toBeVisible();
});

test("the quick actions are behind More actions", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer);
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Renew certificates" }).click();
  await expect(page).toHaveURL(/\/domains/);
});

test("on a phone the page never scrolls sideways, and state comes first", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer);
  await expect(page.getByRole("region", { name: "Key figures" }).getByRole("link")).toHaveCount(6);
  await expect(page.getByRole("region", { name: "Machine" }).getByRole("img", { name: /^CPU/ })).toBeVisible();
  await settle(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expectNoA11yViolations(page, "the overview on a phone");
});
