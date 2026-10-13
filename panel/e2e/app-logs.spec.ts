/**
 * An application's journal against the real backend: the logs WebSocket streams the seeded
 * unit's journal (answered by the console server's model of journalctl), `/` searches it and
 * counts the matches, and a static site says it has no process to log. Both themes, with the
 * CSP and console gates of the `problems` fixture.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";
const PHONE = { width: 390, height: 844 };

test("the journal streams in, `/` searches it and counts the matches", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  const journal = page.getByRole("region", { name: `Logs of ${DOMAIN}` });
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
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
  const search = page.getByRole("searchbox", { name: "Search output" });
  await expect(search).toBeFocused();
  await page.keyboard.type("ECONNREFUSED");
  await expect(page.getByText(/^1 of \d+$/)).toBeVisible();
  await expect(journal.locator("mark").first()).toHaveText("ECONNREFUSED");

  await settle(page);
  await expectNoA11yViolations(page, "an app's journal");
});

test("on a phone the journal wraps by default and the toolbar stays on screen", async ({ page, consoleServer }) => {
  await page.setViewportSize(PHONE);
  await signIn(page, consoleServer, `/apps/${DOMAIN}/logs`);
  const journal = page.getByRole("region", { name: `Logs of ${DOMAIN}` });
  await expect(journal.getByText(/tienda-example-org\[\d+\]: /).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Wrap lines" })).toHaveAttribute("aria-pressed", "true");

  // The fullest toolbar state: an active search shows the match counter and step buttons
  // alongside follow, wrap, copy and download, and none of it may push the page sideways.
  await journal.click();
  await page.keyboard.press("/");
  await expect(page.getByRole("searchbox", { name: "Search output" })).toBeFocused();
  await page.keyboard.type("ECONNREFUSED");
  await expect(page.getByText(/^1 of \d+$/)).toBeVisible();

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
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await page.getByRole("radio", { name: "Web server requests" }).click();
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
