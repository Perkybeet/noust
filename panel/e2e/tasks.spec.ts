/**
 * The task centre, against the real backend, in both themes (item 114): a deploy that is started and left is
 * counted in the top bar, pinned to the foot of every page, kept through a reload, told at its end with a way to see it,
 * and has a page of its own; the list under Activity, and the panel on "All servers", which a central builds from each
 * server's answer (here faked, because the seeded machine has no fleet): a server not yet updated says so, one that
 * cannot be reached says so.
 */

import type { Page } from "@playwright/test";

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, stillness, test, toasts } from "./fixtures";

/** In place, with a real tree: its update runs the real sequence a line at a time (scripts/console_server/app_tabs.py). */
const LIVE_APP = "pedidos.example.org";
const TITLE = `Deploy ${LIVE_APP}`;

/** Waits for every job about `domain` to end, so the next test starts from a machine at rest. */
async function jobsEnded(page: Page, domain: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
        return active.jobs.some((job) => job.metadata?.domain === domain);
      },
      { timeout: 90_000, intervals: [500] },
    )
    .toBe(false);
}

function bar(page: Page) {
  return page.getByRole("banner");
}

test.describe("a deploy that is started and left", () => {
  test.setTimeout(150_000);

  test("is counted, pinned through a reload, told at its end, and has a page of its own", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/databases");
    await jobsEnded(page, LIVE_APP);

    // Started from somewhere else (a script, a webhook, another tab): the console learns of it by its stream.
    const queued = await page.request.post("/api/jobs/update", { data: { domain: LIVE_APP, force: true }, headers: await csrf(page) });
    expect(queued.ok(), await queued.text()).toBe(true);

    const icon = bar(page).getByRole("button", { name: /^Tasks: 1 running/ });
    await expect(icon).toBeVisible();
    await icon.click();
    const panel = page.getByRole("dialog", { name: "Tasks" });
    const running = panel.getByRole("region", { name: /^Yours, running/ });
    await expect(running.getByRole("link", { name: `Open ${TITLE}` })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the task centre's panel, a deploy running");

    // Pinning is a choice, and the chip follows the person to another page.
    await expect(page.getByRole("list", { name: "Pinned tasks" })).toHaveCount(0);
    await running.getByRole("button", { name: `Pin ${TITLE} to the bottom` }).click();
    const chips = page.getByRole("list", { name: "Pinned tasks" });
    await expect(chips.getByRole("link", { name: new RegExp(TITLE.replace(/\./g, "\\.")) })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.reload();
    await expect(page.getByRole("list", { name: "Pinned tasks" }).getByRole("link", { name: new RegExp(TITLE.replace(/\./g, "\\.")) })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a pinned task, after a reload");

    // It ends while the person is on another page: one notice, with a way to see it.
    const notice = toasts(page).getByText(`${TITLE}: done`);
    await expect(notice).toBeVisible({ timeout: 120_000 });
    await expect(toasts(page).getByText("You started it and left that page.")).toBeVisible();
    await expect(toasts(page).getByText(new RegExp(`^${TITLE.replace(/\./g, "\\.")}$`))).toHaveCount(0);
    await stillness(page);
    await expectNoA11yViolations(page, "the notice that a task ended");

    // The chip stays, with its result, until it is let go.
    await expect(page.getByRole("list", { name: "Pinned tasks" }).getByText("Succeeded")).toBeVisible();

    // "See" opens the task's page: its steps, who and when, and its log.
    await toasts(page).getByRole("button", { name: "See" }).click();
    await expect(page).toHaveURL(/\/activity\/tasks\/[0-9a-f]+$/);
    await expect(page.getByRole("heading", { level: 1, name: TITLE })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Steps" })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "Log" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Unpin from the bottom" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a task's page, ended");

    await page.getByRole("list", { name: "Pinned tasks" }).getByRole("button", { name: `Unpin ${TITLE}` }).click();
    await expect(page.getByRole("list", { name: "Pinned tasks" })).toHaveCount(0);
    await jobsEnded(page, LIVE_APP);
  });

  test("is not told when the person is on its page, and says its steps and its log as they happen", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `/apps/${LIVE_APP}`);
    await jobsEnded(page, LIVE_APP);
    const queued = await page.request.post("/api/jobs/update", { data: { domain: LIVE_APP, force: true }, headers: await csrf(page) });
    expect(queued.ok(), await queued.text()).toBe(true);
    const { job } = (await queued.json()) as { job: { id: string } };

    await page.goto(`/activity/tasks/${job.id}`);
    await expect(page.getByRole("heading", { level: 1, name: TITLE })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pin to the bottom" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Deploy phases" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "a task's page, running");

    // It ends here: the page shows it, and nothing else says so.
    await expect(bar(page).getByRole("button", { name: "Tasks, nothing running" })).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("Succeeded").first()).toBeVisible();
    await expect(toasts(page).getByText(`${TITLE}: done`)).toHaveCount(0);
    await jobsEnded(page, LIVE_APP);
  });
});

test.describe("the list", () => {
  test("is Activity's Tasks: each task with who started it, where and when, and a way to open it", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/activity");
    // The second column of Activity has it, beside the views.
    await page.getByRole("navigation", { name: /^Sections/ }).getByRole("link", { name: "Tasks" }).click();
    await expect(page).toHaveURL(/\/activity\/tasks$/);
    await expect(page.getByRole("heading", { level: 1, name: "Tasks" })).toBeVisible();
    const table = page.getByRole("table", { name: "Tasks, latest first" });
    await expect(table.getByRole("row").nth(1)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "Activity > Tasks");

    // A row opens the task's own page.
    await table.getByRole("link", { name: /^Open / }).first().click();
    await expect(page).toHaveURL(/\/activity\/tasks\/[0-9a-f]+$/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});

test.describe("on All servers", () => {
  test("the panel is grouped by server: one not yet updated says it will count its tasks, one that cannot be reached says so", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    const job = (id: string, domain: string, progress: number) => ({
      id,
      type: "deploy",
      name: `Deploy ${domain}`,
      description: "",
      status: "running",
      state: "running",
      progress,
      total_steps: 100,
      current_step: "Building",
      created_at: new Date(Date.now() - 60_000).toISOString(),
      started_at: new Date(Date.now() - 55_000).toISOString(),
      logs: [],
      metadata: { domain },
      title: { code: "jobs.title.deploy", params: { domain } },
      actor: "ana",
    });
    await page.route("**/api/fleet/jobs/active", async (route) => {
      await route.fulfill({
        json: {
          resource: "jobs_active",
          generated_at: new Date().toISOString(),
          partial: true,
          nodes: [
            { name: "central-01", local: true, status: "ok", version: "4.2.0" },
            { name: "web-01", local: false, status: "ok", version: "4.2.0" },
            { name: "web-02", local: false, status: "unsupported", version: "4.1.0" },
            { name: "db-02", local: false, status: "unreachable", version: null },
          ],
          items: [
            { node: "central-01", local: true, status: "ok", jobs: [job("c1", "tienda.example.com", 46)], operations: [] },
            { node: "web-01", local: false, status: "ok", jobs: [job("w1", "reservas.example.com", 72)], operations: [] },
            { node: "web-02", local: false, status: "unsupported", jobs: [], operations: [] },
            { node: "db-02", local: false, status: "unreachable", jobs: [], operations: [] },
          ],
        },
      });
    });
    await signIn(page, consoleServer, "/fleet");
    const icon = bar(page).getByRole("button", { name: /^Tasks: / });
    await expect(icon).toBeVisible();
    await icon.click();
    const panel = page.getByRole("dialog", { name: "Tasks" });
    await expect(panel.getByRole("region", { name: "web-01" }).getByText("Deploy reservas.example.com")).toBeVisible();
    await expect(panel.getByRole("region", { name: "web-02" })).toContainText("It has Noust 4.1.0: it will count its tasks here when it is updated to");
    await expect(panel.getByRole("region", { name: "web-02" }).getByRole("link", { name: "Update it" })).toBeVisible();
    await expect(panel.getByRole("region", { name: "db-02" }).getByText("No connection: it cannot be asked.")).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the task centre's panel on All servers");
  });
});
