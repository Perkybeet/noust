/**
 * Screenshots of the task centre for review (@screens, left out of the default run): the panel with a deploy
 * running, a task's log opened, the chip pinned at the foot of another page, the notice that it ended, a task's page
 * running and ended, a failed one, the panel on "All servers" and Activity's list; at 1440 and at 390, in the project's theme.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/tareas-screens npx playwright test tasks-screens
 *
 * The deploy is the real one of a seeded application, whose build streams a line at a time, so the panel is
 * photographed while it runs.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { csrf } from "./apps-cleanup";
import { expect, settle, signIn, test, toasts } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "tasks");
const LIVE_APP = "pedidos.example.org";
const TITLE = `Update ${LIVE_APP}`;
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

async function jobsEnded(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
        return active.jobs.some((job) => job.metadata?.domain === LIVE_APP);
      },
      { timeout: 90_000, intervals: [500] },
    )
    .toBe(false);
}

async function start(page: Page): Promise<string> {
  const queued = await page.request.post("/api/jobs/update", { data: { domain: LIVE_APP, force: true }, headers: await csrf(page) });
  expect(queued.ok(), await queued.text()).toBe(true);
  return ((await queued.json()) as { job: { id: string } }).job.id;
}

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${theme}-${size}.png`) });
}

for (const size of SIZES) {
  test(`@screens the task centre at ${size.name}`, async ({ page, consoleServer }, testInfo) => {
    test.setTimeout(240_000);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, consoleServer, "/databases");
    await jobsEnded(page);

    // The panel, with a deploy of yours running.
    await start(page);
    const icon = page.getByRole("banner").getByRole("button", { name: /^Tasks: 1 running/ });
    await expect(icon).toBeVisible();
    await icon.click();
    const panel = page.getByRole("dialog", { name: "Tasks" });
    await expect(panel.getByRole("region", { name: /^Yours, running/ }).getByRole("link", { name: `Open ${TITLE}` })).toBeVisible();
    await shot(page, theme, size.name, "10-panel");

    // Its last lines of log, when asked.
    await panel.getByRole("button", { name: "Log" }).first().click();
    await expect(panel.getByRole("button", { name: "Log" }).first()).toHaveAttribute("aria-expanded", "true");
    await shot(page, theme, size.name, "11-log");

    // Pinned to the foot of another page.
    await panel.getByRole("button", { name: `Pin ${TITLE} to the bottom` }).click();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("list", { name: "Pinned tasks" })).toBeVisible();
    await shot(page, theme, size.name, "12-pinned");

    // It ends while the person is elsewhere.
    await expect(toasts(page).getByText(`${TITLE}: done`)).toBeVisible({ timeout: 120_000 });
    await shot(page, theme, size.name, "13-ended");

    // Its page, ended; and a second run, followed on its page.
    await toasts(page).getByRole("button", { name: "See" }).click();
    await expect(page.getByRole("heading", { level: 1, name: TITLE })).toBeVisible();
    await shot(page, theme, size.name, "14-task-ended");
    await page.getByRole("list", { name: "Pinned tasks" }).getByRole("button", { name: `Unpin ${TITLE}` }).click();
    await jobsEnded(page);

    const id = await start(page);
    await page.goto(`/activity/tasks/${id}`);
    await expect(page.getByRole("heading", { level: 1, name: TITLE })).toBeVisible();
    await expect(page.getByRole("list", { name: "Deploy phases" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pin to the bottom" })).toBeVisible();
    await page.waitForTimeout(2500);
    await shot(page, theme, size.name, "14-task-running");
    await jobsEnded(page);

    // One that failed, made up for the picture: what the sandbox's builds do not do is fail.
    const failedJob = {
      id: "f00dbad1",
      type: "deploy",
      name: "Deploy api.example.com",
      description: "",
      status: "failed",
      state: "failed",
      progress: 60,
      total_steps: 100,
      current_step: "Building",
      created_at: new Date(Date.now() - 240_000).toISOString(),
      started_at: new Date(Date.now() - 238_000).toISOString(),
      completed_at: new Date(Date.now() - 166_000).toISOString(),
      error: "Error: Cannot find module './shipping/rates'\nnpm ERR! code ELIFECYCLE\nnpm ERR! errno 1",
      logs: [],
      metadata: { domain: "api.example.com" },
      title: { code: "jobs.title.deploy", params: { domain: "api.example.com" } },
      actor: null,
      deployment_id: 41,
    };
    await page.route("**/api/jobs/f00dbad1", (route) => route.fulfill({ json: failedJob }));
    await page.route("**/api/jobs/f00dbad1/log", (route) =>
      route.fulfill({ json: { content: "[2026-11-05 01:00:01] [INFO] Preparing deployment\n[2026-11-05 01:00:03] [INFO] Fetching the recorded source again\n[2026-11-05 01:00:05] [INFO] Building\n[2026-11-05 01:01:12] [ERROR] npm ERR! code ELIFECYCLE", truncated: false } }),
    );
    await page.goto("/activity/tasks/f00dbad1");
    await expect(page.getByRole("heading", { level: 1, name: "Deploy api.example.com" })).toBeVisible();
    await expect(page.getByText("npm ERR! code ELIFECYCLE").first()).toBeVisible();
    await shot(page, theme, size.name, "15-task-failed");

    // The list.
    await page.goto("/activity/tasks");
    await expect(page.getByRole("heading", { level: 1, name: "Tasks" })).toBeVisible();
    await expect(page.getByRole("table", { name: "Tasks, latest first" }).or(page.getByRole("list", { name: "Tasks, latest first" })).first()).toBeVisible();
    await shot(page, theme, size.name, "17-history");
  });

  test(`@screens the task centre on All servers at ${size.name}`, async ({ page, consoleServer }, testInfo) => {
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
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
            { name: "db-02", local: false, status: "ok", version: "4.2.0" },
          ],
          items: [
            { node: "central-01", local: true, status: "ok", jobs: [job("c1", "tienda.example.com", 46)], operations: [] },
            { node: "web-01", local: false, status: "ok", jobs: [job("w1", "reservas.example.com", 72)], operations: [] },
            { node: "web-02", local: false, status: "unsupported", jobs: [], operations: [] },
            { node: "db-02", local: false, status: "ok", jobs: [{ ...job("d1", "clientes.example.com", 12), type: "backup", name: "Back up the 5 databases", title: { code: "jobs.title.backup_any", params: {} }, metadata: {} }], operations: [] },
          ],
        },
      });
    });
    await signIn(page, consoleServer, "/fleet");
    await page.getByRole("banner").getByRole("button", { name: /^Tasks: / }).click();
    await expect(page.getByRole("dialog", { name: "Tasks" }).getByRole("region", { name: "web-02" })).toBeVisible();
    await shot(page, theme, size.name, "16-fleet");
  });
}
