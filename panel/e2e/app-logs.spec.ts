/**
 * An application's journal against the real backend: the logs WebSocket streams the seeded
 * unit's journal (answered by the console server's model of journalctl), `/` asks the toolbar's
 * query and the stream keeps the lines that match, and a static site says it has no process to
 * log. Both themes, with the CSP and console gates of the `problems` fixture.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";
const PHONE = { width: 390, height: 844 };

test("the journal streams in, and the toolbar's query keeps the lines that match it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  const journal = page.getByRole("region", { name: `Logs of ${DOMAIN}` });
  // The tab opens on Live, the range, and says whether the stream is connected.
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveText("Live");
  await expect(page.locator("#main").getByText("Connected", { exact: true })).toBeVisible();
  // Following: the newest lines are the ones in view.
  await expect(journal.getByText(/tienda-example-org\[\d+\]: /).last()).toBeVisible();
  const count = page.getByText(/^\d[\d,]* lines$/);
  await expect(count).toBeVisible();
  const before = Number.parseInt(((await count.textContent()) ?? "0").replace(/,/g, ""), 10);
  // The unit keeps writing while it is followed.
  await expect
    .poll(async () => Number.parseInt(((await count.textContent()) ?? "0").replace(/,/g, ""), 10), { timeout: 10_000 })
    .toBeGreaterThan(before);

  await journal.click();
  await page.keyboard.press("/");
  const search = page.getByRole("searchbox", { name: "Search the logs" });
  await expect(search).toBeFocused();
  await page.keyboard.type("ECONNREFUSED");
  // The query is written back as it was understood, and only the lines that match stay.
  await expect(page.getByText("Understood as")).toBeVisible();
  await expect(journal.getByText(/ECONNREFUSED/).first()).toBeVisible();
  await expect(journal.locator("[data-index]").filter({ hasNotText: "ECONNREFUSED" })).toHaveCount(0);
  await expect(page).toHaveURL(/q=ECONNREFUSED/);

  await settle(page);
  await expectNoA11yViolations(page, "an app's journal");
});

test("on a phone the journal wraps by default and the toolbar stays on screen", async ({ page, consoleServer }) => {
  await page.setViewportSize(PHONE);
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  const journal = page.getByRole("region", { name: `Logs of ${DOMAIN}` });
  await expect(journal.getByText(/tienda-example-org\[\d+\]: /).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Wrap lines" })).toHaveAttribute("aria-pressed", "true");

  // The fullest toolbar state: a query with its "Understood as" line above the viewer's own
  // toolbar of follow, wrap, copy and download, and none of it may push the page sideways.
  await journal.click();
  await page.keyboard.press("/");
  await expect(page.getByRole("searchbox", { name: "Search the logs" })).toBeFocused();
  await page.keyboard.type("ECONNREFUSED");
  await expect(page.getByText("Understood as")).toBeVisible();

  await settle(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expectNoA11yViolations(page, "an app's journal on a phone");
});

test("a static site says it has no process to log, and reads what its web server wrote", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/taller.example.com/logs");
  await expect(page.getByText("A static site has no process to log")).toBeVisible();
  // There is no journal to offer: the web server's logs are the site's logs.
  await expect(page.getByRole("radio", { name: "Application" })).toHaveCount(0);
  await expect(page.getByRole("radio", { name: "Web server requests" })).toBeChecked();
  // Nothing has been requested yet: where the log will appear, not an error.
  await expect(page.getByText("The web server has not written this log yet")).toBeVisible();
  await page.getByRole("radio", { name: "Web server errors" }).click();
  const errors = page.getByRole("region", { name: "Web server error log of taller.example.com" });
  await expect(errors.getByText(/missing\.html/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a static site's logs tab");
});

test("the web server's requests and errors for a site, with a token in a query string masked", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  await expect(page.locator("#main").getByText("Connected", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "Web server requests" }).click();
  // The source is in the address, and the web server's file has no past to ask for.
  await expect(page).toHaveURL(/source=access/);
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveCount(0);
  const access = page.getByRole("region", { name: `Web server access log of ${DOMAIN}` });
  await expect(access.getByText(/"POST \/api\/orders HTTP\/1\.1" 502/).first()).toBeVisible();
  // The credential in a callback's query string never reaches the page, only its shape does.
  await expect(access.getByText(/token=\*\*\* HTTP/).first()).toBeVisible();
  await expect(page.getByText(`ghp_${"t".repeat(36)}`)).toHaveCount(0);
  // A server error is the line to look for among the 200s: the viewer's chip for a 5xx finds it.
  await page.getByRole("button", { name: /^Server errors \d+$/ }).click();
  await expect(access.getByText(/"POST \/api\/orders HTTP\/1\.1" 502/).first()).toBeVisible();
  await expect(access.getByText(/ 200 5120 /)).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "an app's web server access log");

  await page.getByRole("radio", { name: "Web server errors" }).click();
  const errors = page.getByRole("region", { name: `Web server error log of ${DOMAIN}` });
  await expect(errors.getByText(/connect\(\) failed \(111: Connection refused\)/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an app's web server error log");
});
