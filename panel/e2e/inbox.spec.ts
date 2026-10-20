/**
 * The inbox against the real backend and its seeded machine (docs/DESIGN.md 6.14, NV-16, NV-17):
 * the bell holds what needs the operator from any page, two clicks away (T13); "Needs you" is a
 * state, so a failure is still there after its toast is gone; "Recent" is what the server said
 * while the tab was open; an alert is acknowledged in its row. Both themes; on a phone it is a drawer.
 *
 * The seeded server has a failed application, a failed unit and a certificate about to expire. An
 * alert that fires and a notice on the stream are not something a seeded machine produces on
 * demand, so those two answers are given to the page; everything else is the real backend.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

/** The app the seed fails: its newest deploy failed with npm's own words. */
const FAILED = "clientes.example.com";

function bell(page: Page) {
  return page.getByRole("button", { name: /^Inbox, \d+ things? need(s)? you$/ });
}

function panel(page: Page) {
  return page.getByRole("dialog", { name: "Inbox" });
}

/** An alert rule that is firing, as the API lists it; `taken` after somebody acknowledged it. */
function firing(taken: boolean) {
  const since = Math.floor(Date.now() / 1000) - 840;
  return {
    items: [
      {
        name: "disk-high",
        enabled: true,
        severity: "warning",
        metric: "disk",
        comparator: ">",
        threshold: 90,
        unit: "percent",
        scope: "server",
        states: [{ subject: "", state: "firing", since, last_value: 93, acknowledged_by: taken ? "alice" : null, acknowledged_at: taken ? since + 60 : null }],
      },
    ],
    metrics: [],
    firing: 1,
  };
}

test("the bell holds what needs the operator, from any page, and the item is two clicks away", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  await page.evaluate(() => {
    (window as unknown as { __clicks: number }).__clicks = 0;
    document.addEventListener(
      "click",
      (event) => {
        if (event.isTrusted) (window as unknown as { __clicks: number }).__clicks += 1;
      },
      true,
    );
  });
  await bell(page).click();
  const open = panel(page);
  const row = open.getByRole("listitem").filter({ has: page.getByText(FAILED, { exact: true }) });
  await expect(row).toBeVisible();
  // The system's own words, verbatim, under what is wrong.
  await expect(row.getByText("npm ERR! code ELIFECYCLE", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the inbox");
  await row.getByRole("link", { name: `View log of the deploy of ${FAILED}` }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${FAILED.replace(/\./g, "\\.")}/deployments/\\d+$`));
  // T13: the bell, then the item.
  expect(await page.evaluate(() => (window as unknown as { __clicks: number }).__clicks)).toBeLessThanOrEqual(2);
  await expect(open).toBeHidden();
});

test("the sidebar counts the same things, beside the places that have them", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/databases");
  const main = page.getByRole("navigation", { name: "Main" });
  await expect(main.getByRole("link", { name: /^Applications \d+ failed/ })).toBeVisible();
  await expect(main.getByRole("link", { name: /^Server \d+ failed/ })).toBeVisible();
  await expect(main.getByRole("link", { name: /^Domains .*certificate/ })).toBeVisible();
});

test("an alert is acknowledged in its row, which then offers to reopen it", async ({ page, consoleServer }) => {
  let taken = false;
  await page.route("**/api/alerts/rules", (route) => (route.request().method() === "GET" ? route.fulfill({ json: firing(taken) }) : route.continue()));
  await page.route("**/api/alerts/rules/disk-high/acknowledge**", (route) => {
    taken = route.request().method() === "POST";
    return route.fulfill({ json: { ok: true } });
  });
  await signIn(page, consoleServer, "/databases");
  await bell(page).click();
  const open = panel(page);
  const acknowledge = open.getByRole("button", { name: "Acknowledge disk-high" });
  await expect(acknowledge).toBeVisible();
  const asked = page.waitForRequest((request) => request.method() === "POST" && request.url().includes("/api/alerts/rules/disk-high/acknowledge"));
  await acknowledge.click();
  await asked;
  await expect(open.getByRole("button", { name: "Reopen disk-high" })).toBeVisible();
  await expect(open.getByText("This alert is firing. alice took it.")).toBeVisible();
  // Taking it is not saying it is solved: it is still in the list.
  await expect(open.getByRole("listitem").filter({ has: page.getByText("disk-high") })).toHaveCount(1);
  await open.getByRole("button", { name: "Reopen disk-high" }).click();
  await expect(open.getByRole("button", { name: "Acknowledge disk-high" })).toBeVisible();
});

test("a failure stays in the inbox after its toast is gone, and the toast is kept under Recent", async ({ page, consoleServer }) => {
  await page.route("**/api/alerts/rules", (route) => (route.request().method() === "GET" ? route.fulfill({ json: firing(false) }) : route.continue()));
  // The stream says, once, that the alert fired; every later connection is the real one.
  let said = false;
  await page.route("**/events", (route) => {
    if (said) return route.continue();
    said = true;
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: 'retry: 3000\nevent: notice\ndata: {"text":"Alert disk-high is firing: disk at 93%","state":"failed"}\n\n',
    });
  });
  await signIn(page, consoleServer, "/databases");
  const toast = toasts(page).getByText("Alert disk-high is firing: disk at 93%");
  await expect(toast).toBeVisible();
  await page.locator('.toast button[aria-label="Dismiss notification"]').first().click();
  await expect(toast).toBeHidden();

  await bell(page).click();
  const open = panel(page);
  await expect(open.getByRole("listitem").filter({ has: page.getByText("disk-high") })).toHaveCount(1);
  await open.getByRole("radio", { name: "Recent" }).click();
  await expect(open.getByText("Alert disk-high is firing: disk at 93%")).toBeVisible();
  await expect(open.getByText("Since you opened the console")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the inbox, Recent");
});

test("on a phone the inbox is a drawer, and the bell is still in the bar", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/");
  await bell(page).click();
  const drawer = page.getByRole("dialog", { name: "Inbox" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(FAILED, { exact: true })).toBeVisible();
  const box = await drawer.boundingBox();
  expect(box?.width ?? 0).toBeLessThanOrEqual(390);
  await settle(page);
  await expectNoA11yViolations(page, "the inbox on a phone");
});
