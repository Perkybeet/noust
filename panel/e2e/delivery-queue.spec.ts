/**
 * The deploy queue and cancelling a deployment, against the real backend: an update that waits
 * behind a build says its place and what it waits for in the queue's own words, a repeated
 * request is merged into it, a waiting job is taken out of the line, a build is stopped while it
 * runs and recorded as cancelled (never as a failure), and a full queue is said helpfully. Runs
 * in both themes, with the CSP and console gates of the `problems` fixture.
 *
 * The seeded machine (scripts/console_server.py, "sources and delivery"): catalogo.example.org is
 * on releases and installs and builds one line of npm output a second, so an update lasts about
 * eighteen seconds and can be stopped while it runs. The updates here are forced (a remote with
 * nothing new would refuse them), and none of them is left running.
 */

import type { Locator, Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";

const APP = "catalogo.example.org";

interface Entry {
  id: string;
  status: string;
  cancellable: boolean;
  cancel_requested: boolean;
  queue_position: number | null;
  waiting_for: string | null;
  metadata: { domain?: string; merged_requests?: number };
}

interface Queue {
  running: Entry[];
  queued: Entry[];
}

async function queueOf(page: Page): Promise<Queue> {
  return (await (await page.request.get("/api/jobs/queue")).json()) as Queue;
}

/** Asks for a forced update, as a push or the Update button would, and answers what was said. */
async function update(page: Page): Promise<{ message: string; id: string }> {
  const response = await page.request.post("/api/jobs/update", { data: { domain: APP, force: true }, headers: await csrf(page) });
  expect(response.status(), await response.text()).toBe(202);
  const body = (await response.json()) as { message: string; job: { id: string } };
  return { message: body.message, id: body.job.id };
}

/** Stops whatever is left of this test's updates, so the next test on this worker starts clean. */
async function drain(page: Page): Promise<void> {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    const queue = await queueOf(page);
    const mine = [...queue.queued, ...queue.running].filter((entry) => entry.metadata.domain === APP);
    if (mine.length === 0) return;
    for (const entry of mine) await page.request.post(`/api/jobs/${entry.id}/cancel`, { headers: await csrf(page) });
    await page.waitForTimeout(1_000);
  }
  await expect.poll(async () => [...(await queueOf(page)).running, ...(await queueOf(page)).queued].some((entry) => entry.metadata.domain === APP), { timeout: 60_000 }).toBe(false);
}

/** The panel of the deploy queue, on the Activity page or an application's Deployments tab. */
function panel(page: Page, title: string): Locator {
  return page.getByRole("heading", { level: 2, name: title }).locator("xpath=ancestor::section[1]");
}

test("a request behind a build waits, says what for, takes a repeat into itself, and each can be cancelled", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/activity");
  try {
    // One build runs; the same request again waits behind it; a third joins the waiting one.
    await update(page);
    const second = await update(page);
    const third = await update(page);
    expect(third.id).toBe(second.id);
    expect(third.message).toMatch(/already waiting; this request joined it/);

    // The queue, as a page: the place in line, what each waits for, what was merged.
    const queue = panel(page, "Deploy queue");
    await expect(queue).toBeVisible();
    await expect(queue.getByText(/builds running/)).toBeVisible();
    const rows = queue.getByRole("row");
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(1)).toContainText("Updating catalogo.example.org");
    await expect(rows.nth(1)).toContainText("In progress");
    await expect(rows.nth(2)).toContainText("Queued");
    await expect(rows.nth(2)).toContainText("Next in line");
    await expect(rows.nth(2)).toContainText(/Waiting for the update of catalogo\.example\.org that is running \(job [0-9a-f]+\)/);
    await expect(rows.nth(2)).toContainText("1 request merged into this one");
    await settle(page);
    await expectNoA11yViolations(page, "the deploy queue");

    // The application's own page: the same line above its history, and the job in hand under its header.
    await page.goto(`/apps/${APP}/deployments`);
    const own = panel(page, `Queue of ${APP}`);
    await expect(own.getByRole("row")).toHaveCount(3);
    const inHand = page.getByRole("button", { name: `Cancel Updating ${APP}` });
    await expect(inHand.first()).toBeEnabled();

    // The waiting one is taken out of the line, after being told it has done nothing yet.
    await own.getByRole("row").nth(2).getByRole("button", { name: `Cancel Updating ${APP}` }).click();
    const waiting = page.getByRole("alertdialog", { name: `Take Updating ${APP} out of the queue?` });
    await expect(waiting.getByText(/It has not started\. Cancelling removes it from the line and nothing on the server changes/)).toBeVisible();
    await waiting.getByRole("button", { name: "Remove from queue" }).click();
    await expect(waiting).toBeHidden();
    await expect(own.getByRole("row")).toHaveCount(2);

    // The build is stopped while it runs: what it leaves alone is said first.
    await own.getByRole("row").nth(1).getByRole("button", { name: `Cancel Updating ${APP}` }).click();
    const running = page.getByRole("alertdialog", { name: `Stop the build of ${APP}?` });
    await expect(running.getByText(/What serves now is not touched, and the next request in the queue starts/)).toBeVisible();
    await running.getByRole("button", { name: "Stop build" }).click();
    await expect(running).toBeHidden();

    // Gone from the queue once it has wound down; recorded as cancelled, not as a failure.
    await expect(own).toBeHidden({ timeout: 30_000 });
    const history = page.getByRole("region", { name: `Deploys of ${APP}, newest first` });
    const newest = history.getByRole("row").nth(1);
    await expect(newest.getByText("Cancelled")).toBeVisible();
    await expect(newest.getByText("Failed")).toHaveCount(0);
    const rowsOfApi = (await (await page.request.get(`/api/deployments?domain=${APP}&limit=1`)).json()) as { items: { cancelled: boolean; status: string }[] };
    expect(rowsOfApi.items[0]).toMatchObject({ cancelled: true, status: "failed" });
    // A deploy that was cancelled is not a reason to say the app is broken.
    await expect(page.getByText(/The last deploy failed/)).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, "a cancelled deploy in the history");

    // Its own page says so, in words, with no error under it.
    await newest.getByRole("button", { name: /^Deployment \d+$/ }).click();
    await expect(page.getByText("This deploy was cancelled")).toBeVisible();
    await expect(page.getByText(/the build stopped, and what served before was not touched/)).toBeVisible();
    await expect(page.getByText("The deploy failed while building")).toHaveCount(0);
  } finally {
    await drain(page);
  }
});

test("the job in hand under the header cancels a build from where the operator is", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${APP}`);
  try {
    await update(page);
    const cancel = page.getByRole("button", { name: `Cancel Updating ${APP}` });
    await expect(cancel).toBeEnabled({ timeout: 20_000 });
    await settle(page);
    await expectNoA11yViolations(page, "the job in hand with its Cancel");
    await cancel.click();
    await page.getByRole("alertdialog", { name: `Stop the build of ${APP}?` }).getByRole("button", { name: "Stop build" }).click();
    // It says it is stopping while it winds down, and the job in hand goes with it.
    await expect(cancel).toBeHidden({ timeout: 30_000 });
    await expect.poll(async () => (await queueOf(page)).running.length, { timeout: 30_000 }).toBe(0);
  } finally {
    await drain(page);
  }
});

test("a full queue is said with the wait the server asked for, and where to look", async ({ page, consoleServer, problems }) => {
  // Chromium logs the 429 that says the queue is full as a failed resource.
  problems.expect(/status of 429 .* \/api\/jobs\/update$/);
  await page.route("**/api/jobs/update", (route) =>
    route.fulfill({
      status: 429,
      headers: { "Retry-After": "30" },
      json: {
        error: "queue_full",
        detail: "The deploy queue is full: 25 requests are waiting",
        hint: "Builds run a few at a time and the rest wait their turn. Wait for the queue to shrink, cancel what is not needed ('noust deploy queue' lists it), or raise the limit: noust config set deploy.queue_limit <number>.",
        fields: null,
        output: null,
      },
    }),
  );
  await signIn(page, consoleServer, `/apps/${APP}`);
  await page.getByRole("button", { name: "Update", exact: true }).click();
  const toast = toasts(page);
  await expect(toast.getByText(`Update of ${APP} could not be queued`).or(toast.getByText(/could not be queued/))).toBeVisible();
  await expect(toast.getByText("The deploy queue is full: 25 requests are waiting")).toBeVisible();
  await expect(toast.getByText(/Try again in 30 seconds, or cancel what no longer needs to wait/)).toBeVisible();
  // The console words the fix for itself: the terminal commands the server names are not here.
  await expect(toast.getByText(/noust config set/)).toHaveCount(0);
});
