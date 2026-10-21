/**
 * One application's page against the real backend: the header with its state and live
 * address, Update (a split button) queuing a job and the header following it over the event
 * stream, the confirmations in front of Stop and Delete, and the Overview tab: what is live,
 * the last deployments, the app's health and where it answers. Runs in both themes, with
 * the CSP and console gates of the `problems` fixture.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, stillness, test, toasts } from "./fixtures";

const DOMAIN = "shop.example.net";

function header(page: Page) {
  return page.locator("main header").filter({ has: page.getByRole("heading", { level: 1 }) });
}

/** The header's state pill: the element that carries data-state. */
function pill(page: Page) {
  return header(page).locator("[data-state]").first();
}

test("the header says the app's state, type and port, links to the live site, and passes axe", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(DOMAIN);
  await expect(pill(page)).toHaveAttribute("data-state", "running");
  await expect(pill(page)).toHaveText("Running");
  await expect(header(page).getByText("Next.js", { exact: true })).toBeVisible();
  // Update is the page's one primary action, a split button with its variations in the arrow; the daily verbs are beside it, not in a menu.
  await expect(header(page).getByRole("button", { name: "Update", exact: true })).toHaveAttribute("data-variant", "primary");
  await expect(header(page).getByRole("button", { name: "More options for Update" })).toBeVisible();
  for (const verb of ["Restart", "Stop", "Roll back"]) await expect(header(page).getByRole("button", { name: verb, exact: true })).toBeVisible();

  const live = header(page).getByRole("link", { name: new RegExp(`^${DOMAIN.replace(".", "\\.")}`) });
  await expect(live).toHaveAttribute("href", `https://${DOMAIN}`);
  await expect(live).toHaveAttribute("target", "_blank");

  const tabs = page.getByRole("navigation", { name: "Application sections" });
  await expect(tabs.getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");

  await expect(page.getByText("29 days left").or(page.getByText(/^\d+ days left$/))).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an application's overview");
});

test("Update queues a job; the header follows it over the event stream to its end", async ({ page, consoleServer }) => {
  // What the stream tells the console about this app, in order. The seeded job ends within
  // milliseconds, so the "deploying" frame can be batched away before it is painted; that the
  // header draws it is pinned by the unit tests, and here the stream is shown to carry it.
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { __appStates: string[] }).__appStates = seen;
    const Native = window.EventSource;
    window.EventSource = class extends Native {
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init);
        this.addEventListener("app", (event: MessageEvent<string>) => {
          const data = JSON.parse(event.data) as { domain?: string; status?: string };
          if (data.domain === "shop.example.net" && data.status) seen.push(data.status);
        });
      }
    };
  });
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await expect(pill(page)).toHaveAttribute("data-state", "running");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/jobs/update") && request.method() === "POST");
  await header(page).getByRole("button", { name: "Update", exact: true }).click();
  expect((await queued).postDataJSON()).toEqual({ domain: DOMAIN, force: false });

  // The sandbox has no checkout to update, so the job fails; the header keeps the failure,
  // in the updater's own words, until it is dismissed.
  const failure = page.getByText(`Update of ${DOMAIN} failed`);
  await expect(failure).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: `Application not found: ${DOMAIN}` }).first()).toBeVisible();

  // The stream said deploying, then the app's state once the job ended; the header shows it.
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __appStates: string[] }).__appStates))
    .toEqual(expect.arrayContaining(["deploying", "running"]));
  const states = await page.evaluate(() => (window as unknown as { __appStates: string[] }).__appStates);
  expect(states.indexOf("deploying")).toBeLessThan(states.lastIndexOf("running"));
  await expect(pill(page)).toHaveAttribute("data-state", "running");
  await expect(header(page).getByRole("button", { name: "Update", exact: true })).not.toHaveAttribute("aria-busy");

  // The job's failure also raised the notice toast. Base UI hides a high-priority toast from
  // assistive technology until it is focused (its live region speaks for it) while leaving it
  // focusable, which axe reports as aria-hidden-focus: a design-system finding, reported, not
  // this page's. Dismissed here so axe judges the page. (A role query cannot see into it for
  // the same reason.)
  const toasts = page.locator('.toast button[aria-label="Dismiss notification"]');
  await expect(toasts).not.toHaveCount(0);
  // Stacked toasts behind the front one are transparent, so they are closed without a pointer.
  await toasts.evaluateAll((buttons) => {
    for (const button of buttons) (button as HTMLButtonElement).click();
  });
  await expect(toasts).toHaveCount(0);
  await stillness(page);
  await expectNoA11yViolations(page, "a failed update in the header");
  await page.getByRole("button", { name: "Dismiss", exact: true }).click();
  await expect(failure).toBeHidden();
});

/** Seeded on releases with a remote whose main is the commit that is live (console_server.py). */
const NOTHING_NEW_APP = "catalogo.example.org";

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "noust_csrf");
  return cookie ? { "X-Noust-CSRF": cookie.value } : {};
}

test("Update with nothing new asks, in the backend's words, and rebuilds only when told", async ({ page, consoleServer, problems }) => {
  // Chromium logs the 409 that asks the question as a failed resource.
  problems.expect(/status of 409 .* \/api\/jobs\/update$/);
  await signIn(page, consoleServer, `/apps/${NOTHING_NEW_APP}`);
  const releases = (await (await page.request.get(`/api/apps/${NOTHING_NEW_APP}/releases`)).json()) as { items: { id: string; active: boolean }[] };
  const live = releases.items.find((release) => release.active)?.id ?? "";
  const dialog = page.getByRole("dialog", { name: `Nothing new to deploy to ${NOTHING_NEW_APP}` });

  // Cancelled: nothing is queued.
  await header(page).getByRole("button", { name: "Update", exact: true }).click();
  await expect(dialog.getByText("No new commits on main since 6d1e0b2, which is live")).toBeVisible();
  await expect(dialog.getByText(/Rebuilding the same commit still makes sense when the environment or the dependencies changed/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the nothing-new question");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  expect(((await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] }).jobs.some((job) => job.metadata?.domain === NOTHING_NEW_APP)).toBe(false);

  // Rebuilt anyway: the same request, forced, and the header follows the job.
  await header(page).getByRole("button", { name: "Update", exact: true }).click();
  const forced = page.waitForRequest((request) => request.url().endsWith("/api/jobs/update") && request.method() === "POST" && (request.postDataJSON() as { force?: boolean }).force === true);
  await dialog.getByRole("button", { name: "Rebuild anyway" }).click();
  expect((await forced).postDataJSON()).toEqual({ domain: NOTHING_NEW_APP, force: true });
  await expect(dialog).toBeHidden();
  await expect
    .poll(async () => {
      const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
      return active.jobs.some((job) => job.metadata?.domain === NOTHING_NEW_APP);
    }, { timeout: 60_000, intervals: [500] })
    .toBe(false);

  // The rebuild activated a release of its own; the seeded one goes back in service, so the
  // branch head is the live commit again for the next test in this worker.
  const restored = await page.request.post(`/api/apps/${NOTHING_NEW_APP}/releases/${live}/activate`, { headers: await csrf(page) });
  expect(restored.ok(), await restored.text()).toBe(true);
});

test("Stop asks first; the header follows the unit down and back up", async ({ page, consoleServer }) => {
  // Its own app: stopping changes the worker's machine for every later test.
  const domain = "tools.example.net";
  await signIn(page, consoleServer, `/apps/${domain}`);
  await expect(pill(page)).toHaveAttribute("data-state", "running");

  await header(page).getByRole("button", { name: "Stop", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: `Stop ${domain}?` });
  await expect(dialog).toBeVisible();
  await expectNoA11yViolations(page, "the stop confirmation");
  await dialog.getByRole("button", { name: "Stop application" }).click();
  await expect(dialog).toBeHidden();
  await expect(pill(page)).toHaveAttribute("data-state", "stopped");
  await expect(toasts(page).getByText(`Stopped ${domain}`)).toBeVisible();

  // Stopped, the verb is Start and Stop is gone; there is nothing to confirm.
  await expect(header(page).getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  await header(page).getByRole("button", { name: "Start", exact: true }).click();
  await expect(pill(page)).toHaveAttribute("data-state", "running");
  await expect(header(page).getByRole("button", { name: "Stop", exact: true })).toBeVisible();
});

test("Delete stays disabled until the domain is typed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await header(page).getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Delete application" }).click();

  // Deleting is a sudo-mode action: "Confirm it's you" comes first, never on top of the dialog.
  const elevate = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(elevate).toBeVisible();
  await elevate.getByLabel("Authentication code").fill(consoleServer.secondFactor());
  await elevate.getByRole("button", { name: "Confirm" }).click();
  await expect(elevate).toBeHidden();

  const dialog = page.getByRole("alertdialog", { name: `Delete ${DOMAIN}` });
  await expect(dialog).toBeVisible();
  // What cannot be made again goes only when ticked: both start unticked.
  await expect(dialog.getByRole("checkbox", { name: "Also delete its files" })).not.toBeChecked();
  await expect(dialog.getByRole("checkbox", { name: "Also delete its certificate" })).not.toBeChecked();
  const confirm = dialog.getByRole("button", { name: "Delete application" });
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("textbox").fill("shop");
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("textbox").fill(DOMAIN);
  await expect(confirm).toBeEnabled();
  await expectNoA11yViolations(page, "the delete confirmation");

  // Not confirmed: the worker's machine keeps its app.
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
  await expect(header(page).getByRole("button", { name: "More actions" })).toBeFocused();
});

test("Roll back asks one question that names what it will do, for an app kept in a single folder", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await header(page).getByRole("button", { name: "Roll back", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: `Roll back ${DOMAIN}` });
  await expect(dialog.getByRole("button", { name: /^Go back to / })).toBeVisible();
  await expect(dialog.getByText("What goes back", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "the rollback dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("the overview tab says what is live, lists the last deployments, and reads the app's health", async ({ page, consoleServer }) => {
  const domain = "clientes.example.com";
  await signIn(page, consoleServer, `/apps/${domain}`);
  // Its unit is down: no version is live, and the overview says the last one that served.
  await expect(page.getByRole("region", { name: "Last version that served" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Live now" })).toHaveCount(0);
  const deployments = page.getByRole("list", { name: `Last deployments of ${domain}` });
  await expect(deployments.getByRole("link").first()).toHaveAttribute("href", new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`));
  // Its unit is the one systemd gave up on: said above the tabs, with the diagnosis one click away.
  await expect(pill(page)).toHaveAttribute("data-state", "failed");
  await expect(page.getByText("The service has stopped with an error")).toBeVisible();

  // Nothing of how it runs is repeated here: that is Settings'. Health says how it is doing, and where to change its limits.
  await expect(page.getByRole("region", { name: "How it runs" })).toHaveCount(0);
  const health = page.getByRole("region", { name: "Health" });
  await expect(health).toBeVisible();
  await expect(health.getByRole("link", { name: "Edit limits" })).toHaveAttribute("href", `/apps/${domain}/settings/resources`);
  await expect(page.getByRole("list", { name: `Where ${domain} answers and what deploys it` })).toBeVisible();

  // The banner is the page's, not the tab's: it stays on every tab.
  await page.getByRole("navigation", { name: "Application sections" }).getByRole("link", { name: "Environment" }).click();
  await expect(page.getByText("The service has stopped with an error")).toBeVisible();
  await page.goBack();

  await deployments.getByRole("link").first().click();
  await expect(page).toHaveURL(/\/apps\/clientes\.example\.com\/deployments\/\d+$/);
});

test("on a phone the header keeps every daily verb in view, beside More actions", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, `/apps/${DOMAIN}`);
  await expect(header(page).getByRole("button", { name: "Update", exact: true })).toBeInViewport();
  for (const verb of ["Restart", "Stop", "Roll back"]) await expect(header(page).getByRole("button", { name: verb, exact: true })).toBeInViewport();
  await header(page).getByRole("button", { name: "More actions" }).click();
  // What is left in the menu is what is rare.
  await expect(page.getByRole("menuitem", { name: "Diagnose" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "Restart" })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await settle(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expectNoA11yViolations(page, "an application on a phone");
});
