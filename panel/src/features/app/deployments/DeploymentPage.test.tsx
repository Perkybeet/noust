import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { FakeWebSocket, fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { TAB_APP, TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const FAILED = {
  id: 20,
  domain: TAB_DOMAIN,
  status: "failed",
  triggered_by: "webhook",
  git_commit: "a94c0e2",
  git_branch: "main",
  started_at: "2026-09-25T18:00:00",
  finished_at: "2026-09-25T18:00:40",
  duration_s: 40,
  error: "npm run build exited with status 1\n./app/checkout/page.tsx:42:7\nType error: Property 'total' does not exist on type 'Order'.",
  has_log: true,
};

/** The same deployment, had it served. */
const SERVED = { ...FAILED, status: "success", error: null, release_id: "20260916-182823-a94c0e2" };

const FAILED_LOG = [
  "[2026-09-25 18:00:00] [1/9] 📥 Fetching source into a new release...",
  "[2026-09-25 18:00:02]       → HEAD is now at a94c0e2",
  "[2026-09-25 18:00:03] [2/9] 📦 Installing dependencies...",
  "[2026-09-25 18:00:15] added 812 packages, and audited 813 packages in 12s",
  "[2026-09-25 18:00:16] [3/9] 🔨 Building application...",
  "[2026-09-25 18:00:39] Type error: Property 'total' does not exist on type 'Order'.",
  "",
].join("\n");

/** A queued job as the API answers it, for the actions' 202s. */
const JOB = {
  id: "0badcafe",
  type: "update",
  name: "",
  description: "",
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:00:00",
  logs: [],
  metadata: { domain: TAB_DOMAIN },
};

async function pageAt(
  id: number | string,
  extra: Record<string, RouteHandler> = {},
  app: Record<string, unknown> = {},
) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(app, {
      "GET /api/deployments": () => json(200, { items: [FAILED], total: 1, next_before_id: null }),
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, FAILED),
      [`GET /api/deployments/${String(FAILED.id)}/log`]: () => json(200, { content: FAILED_LOG, truncated: false, missing_reason: null }),
      "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
      ...extra,
    }),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/deployments/${String(id)}`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

function phaseStates(): Record<string, string | null> {
  const list = screen.getByRole("list", { name: "Deploy phases" });
  const entries: [string, string | null][] = within(list)
    .getAllByRole("listitem")
    .map((item) => [item.dataset["phase"] ?? "", item.dataset["state"] ?? null]);
  return Object.fromEntries(entries);
}

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("a deployment's page", { timeout: 20_000 }, () => {
  it("says where a failed deploy stopped, the fix above and the error verbatim below", async () => {
    await pageAt(20);
    expect(await screen.findByRole("heading", { level: 2, name: "Deployment 20" })).toBeInTheDocument();
    expect(await screen.findByText("The deploy failed while building")).toBeInTheDocument();
    expect(screen.getByText(/The build's own output is in the log below/)).toBeInTheDocument();
    // The system's words, whole, then the end of the phase that failed, each in its own box.
    const verbatim = screen.getAllByText(/Type error: Property 'total' does not exist on type 'Order'\.$/, { selector: "pre" });
    expect(verbatim[0]?.textContent).toBe(FAILED.error);
    await waitFor(() => {
      expect(phaseStates()).toEqual({ fetch: "done", install: "done", build: "failed", activate: "unrecorded", health: "unrecorded" });
    });
    const phases = screen.getByRole("list", { name: "Deploy phases" });
    expect(within(phases).getAllByText("Not reached")).toHaveLength(2);
    expect(within(phases).getByText("13s")).toBeInTheDocument();
    // The ways on, beside the error: Diagnose always, the environment when a build or an install read it.
    expect(screen.getByRole("link", { name: "Open Diagnose" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/diagnose`);
    expect(screen.getByRole("link", { name: "Edit environment" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/environment`);
    expect(screen.getByRole("link", { name: "Show it in the log" })).toHaveAttribute("href", "#build-log");
    // The page's own way back is the header's trail, not a link of its own.
    expect(screen.queryByRole("link", { name: "All deployments" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "Deployments" }).map((link) => link.getAttribute("href"))).toContain(`/apps/${TAB_DOMAIN}/deployments`);
  });

  it("shows the end of the phase that failed, not only the system's one-line error", async () => {
    await pageAt(20, { [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, error: null }) });
    expect(await screen.findByText("The deploy failed while building")).toBeInTheDocument();
    // With no error recorded, the first line the log reads as one is the system's words.
    const boxes = await screen.findAllByText(/Type error: Property 'total' does not exist on type 'Order'\./, { selector: "pre" });
    expect(boxes.length).toBeGreaterThan(0);
    expect(boxes.some((box) => box.textContent.includes("Building application"))).toBe(true);
  });

  it("shows the excerpt without the tool's colour codes, as the log viewer reads them", async () => {
    const coloured = [
      "[2026-09-25 18:00:00] [1/9] Fetching source...",
      "[2026-09-25 18:00:05] [3/9] Building application...",
      "[2026-09-25 18:00:06] \u001b[1mNext.js 15.2.4\u001b[0m",
      "[2026-09-25 18:00:20] \u001b[31mnpm ERR!\u001b[0m code ELIFECYCLE",
      "",
    ].join("\n");
    await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, error: null }),
      [`GET /api/deployments/${String(FAILED.id)}/log`]: () => json(200, { content: coloured, truncated: false, missing_reason: null }),
    });
    expect(await screen.findByText("The deploy failed while building")).toBeInTheDocument();
    const boxes = await screen.findAllByText(/npm ERR! code ELIFECYCLE/, { selector: "pre" });
    expect(boxes.length).toBeGreaterThan(0);
    for (const box of boxes) {
      expect(box.textContent.includes("\u001b")).toBe(false);
      expect(box.textContent).not.toContain("[31m");
    }
    expect(boxes.some((box) => box.textContent.includes("Next.js 15.2.4"))).toBe(true);
  });

  it("opens the log on the line where it first went wrong", async () => {
    // The virtualizer sizes its window from offsetHeight, which jsdom reports as 0.
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    await pageAt(20);
    const log = await screen.findByRole("region", { name: `Build log of deployment 20 of ${TAB_DOMAIN}` });
    const line = await within(log).findByText("Type error: Property 'total' does not exist on type 'Order'.");
    expect(line.closest("[aria-current]")).not.toBeNull();
  });

  it("says which version still serves while this one failed, from the history", async () => {
    await pageAt(20, {
      "GET /api/deployments": () => json(200, { items: [FAILED, { ...SERVED, id: 19, git_commit: "9f2c41a" }], total: 2, next_before_id: null }),
    });
    expect(await screen.findByText(`Version #19 still serves ${TAB_DOMAIN}.`)).toBeInTheDocument();
  });

  it("says a newer version serves when the deploy that failed is not the last one", async () => {
    await pageAt(20, {
      "GET /api/deployments": () => json(200, { items: [{ ...SERVED, id: 22 }, FAILED, { ...SERVED, id: 19 }], total: 3, next_before_id: null }),
    });
    expect(await screen.findByText("Version #19 kept serving while this one failed.")).toBeInTheDocument();
  });

  it("says the app went back when the startup check refused the new version", async () => {
    const health = ["[2026-09-25 18:00:00] [1/9] Fetching source...", "[2026-09-25 18:00:20] [8/9] Checking health...", "[2026-09-25 18:00:30] Error: Nothing answered on port 3000", ""].join("\n");
    await pageAt(20, {
      "GET /api/deployments": () => json(200, { items: [FAILED, { ...SERVED, id: 19 }], total: 2, next_before_id: null }),
      [`GET /api/deployments/${String(FAILED.id)}/log`]: () => json(200, { content: health, truncated: false, missing_reason: null }),
    });
    expect(await screen.findByText("The app went back to version #19 after the startup check failed.")).toBeInTheDocument();
  });

  it("says nothing is serving yet when this was the first deploy", async () => {
    await pageAt(20);
    expect(await screen.findByText("Nothing is serving yet: this was the first deploy.")).toBeInTheDocument();
  });

  it("does not repeat the failure in a banner above the tabs", async () => {
    await pageAt(20);
    await screen.findByText("The deploy failed while building");
    expect(screen.queryByText(/^The last deploy failed/)).not.toBeInTheDocument();
  });

  it("says a cancelled deploy was cancelled, by whom, and that what served was not touched: no error, no alarm", async () => {
    const cancelled = { ...FAILED, id: 21, error: null, cancelled: true, cancelled_by: "token:ci-deploy", cancelled_at: "2026-09-25T18:00:20" };
    await pageAt(21, { "GET /api/deployments/21": () => json(200, cancelled), "GET /api/deployments/21/log": () => json(200, { content: FAILED_LOG, truncated: false, missing_reason: null }) });
    expect(await screen.findByRole("heading", { level: 2, name: "Deployment 21" })).toBeInTheDocument();
    expect(await screen.findByText("This deploy was cancelled")).toBeInTheDocument();
    expect(screen.getByText("Cancelled by token:ci-deploy: the build stopped, and what served before was not touched.")).toBeInTheDocument();
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    expect(screen.queryByText("Failed")).not.toBeInTheDocument();
    expect(screen.queryByText("The deploy failed while building")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open Diagnose" })).not.toBeInTheDocument();
    // Nothing went wrong, so nothing to retry.
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("shows the build log with its times apart from the verbatim text", async () => {
    // The virtualizer sizes its window from offsetHeight, which jsdom reports as 0.
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    await pageAt(20);
    const log = await screen.findByRole("region", { name: `Build log of deployment 20 of ${TAB_DOMAIN}` });
    expect(await within(log).findByText("added 812 packages, and audited 813 packages in 12s")).toBeInTheDocument();
    expect(within(log).getAllByText("18:00:15")).not.toHaveLength(0);
  });

  it("follows a running deploy: its job over the WebSocket, its log as it grows, until it ends", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    // Linked by the deployment's own job_id, not by matching it against the active jobs list.
    const running = { ...FAILED, id: 31, status: "running", finished_at: null, duration_s: null, error: null, job_id: "8048ab18" };
    let status = "running";
    let content = "[2026-09-25 18:00:03]       → Running: npm ci\n";
    const job = {
      id: "8048ab18",
      type: "update",
      name: `Update ${TAB_DOMAIN}`,
      description: "",
      status: "running",
      progress: 20,
      total_steps: 100,
      current_step: "Installing dependencies",
      created_at: "2026-09-25T18:00:00",
      started_at: "2026-09-25T18:00:00",
      logs: [
        { timestamp: "2026-09-25T18:00:01", level: "info", message: "Pulling latest changes", step: 20 },
        { timestamp: "2026-09-25T18:00:03", level: "info", message: "Installing dependencies", step: 60 },
      ],
      metadata: { domain: TAB_DOMAIN },
    };
    const { queryClient } = await pageAt(31, {
      "GET /api/deployments/31": () => json(200, { ...running, status }),
      "GET /api/deployments/31/log": () => json(200, { content, truncated: false, missing_reason: null }),
      "POST /api/auth/ws-ticket": () => json(200, { ticket: "t1" }),
    });

    await waitFor(() => {
      expect(FakeWebSocket.instances.some((socket) => socket.url.includes("/ws/jobs/8048ab18"))).toBe(true);
    });
    const socket = FakeWebSocket.instances.find((candidate) => candidate.url.includes("/ws/jobs/8048ab18"));
    act(() => {
      socket?.open();
      socket?.frame({ type: "connected", job });
    });
    await waitFor(() => {
      expect(phaseStates()).toMatchObject({ fetch: "done", install: "running", build: "pending" });
    });

    content += "[2026-09-25 18:00:20]       → Running: npm run build\n";
    act(() => {
      socket?.frame({
        type: "update",
        job: { ...job, logs: [...job.logs, { timestamp: "2026-09-25T18:00:20", level: "info", message: "Building", step: 80 }] },
      });
    });
    await waitFor(
      () => {
        expect(phaseStates()).toMatchObject({ install: "done", build: "running" });
      },
      { timeout: 3_000 },
    );

    status = "success";
    act(() => {
      socket?.frame({ type: "finished", job: { ...job, status: "completed" } });
    });
    await queryClient.invalidateQueries({ queryKey: ["deployments"] });
    expect(await screen.findByText("Succeeded")).toBeInTheDocument();
  });

  /** A history in which the job that was queued has a deploy of its own, next to somebody else's. */
  function afterQueueing(queued: () => boolean): RouteHandler {
    return () =>
      json(200, {
        items: queued()
          ? [
              { ...FAILED, id: 22, job_id: "someone-elses-job", status: "success", error: null },
              { ...FAILED, id: 21, job_id: "0badcafe", status: "running", error: null, finished_at: null },
              { ...FAILED, id: 20 },
            ]
          : [{ ...FAILED, id: 20 }],
        total: queued() ? 3 : 1,
        next_before_id: null,
      });
  }

  it("retries a failed deploy in one press, and opens the new deploy linked by the job's own id", async () => {
    // Not "the newest deployment": one that arrived after the click but for a different job
    // (someone else's update, or a push) must not be mistaken for this one.
    let queued = false;
    const { user, location, backend } = await pageAt(
      20,
      {
        "GET /api/deployments": afterQueueing(() => queued),
        [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () => {
          queued = true;
          return json(202, { job_id: "0badcafe", status: "pending", message: "Rebuild queued", job: { ...JOB, id: "0badcafe" } });
        },
        "GET /api/jobs/0badcafe": () => json(200, { ...JOB, id: "0badcafe", status: "running" }),
        "GET /api/deployments/21": () => json(200, { ...FAILED, id: 21, status: "running", error: null, finished_at: null, job_id: "0badcafe" }),
        "GET /api/deployments/21/log": () => json(200, { content: "", truncated: false, missing_reason: null }),
      },
      { layout: "releases" },
    );
    // The page's own button is the deploy's: nothing is asked first.
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`)[0]?.body).toEqual({ no_cache: false });
    });
    // Never the plain update the header's Update runs.
    expect(backend.calls.some((call) => call.path === "/api/jobs/update")).toBe(false);
    await waitFor(
      () => {
        expect(location().pathname).toBe(`/apps/${TAB_DOMAIN}/deployments/21`);
      },
      { timeout: 4_000 },
    );
    expect(await screen.findByRole("heading", { level: 2, name: "Deployment 21" })).toBeInTheDocument();
  });

  it("retries from scratch from the menu beside Retry", async () => {
    const { user, backend } = await pageAt(20, {
      [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () => json(202, { job_id: "0badcafe", status: "pending", message: "Rebuild queued", job: { ...JOB, id: "0badcafe" } }),
    });
    await user.click(await screen.findByRole("button", { name: "More options for Retry" }));
    await user.click(await screen.findByRole("menuitem", { name: "Retry without cache" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`)[0]?.body).toEqual({ no_cache: true });
    });
  });

  it("has Retry as a secondary: Update in the header stays the page's one primary", async () => {
    await pageAt(20);
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(retry).toHaveAttribute("data-variant", "secondary");
    const primaries = new Set(
      [...document.querySelectorAll('main [data-variant="primary"]')].map((button) => button.closest('[data-slot="split-button"]') ?? button),
    );
    expect(primaries.size).toBe(1);
  });

  it("rebuilds the commit of a deploy that served after saying what that does", async () => {
    let queued = false;
    const { user, location, backend } = await pageAt(
      20,
      {
        [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, SERVED),
        "GET /api/deployments": afterQueueing(() => queued),
        [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () => {
          queued = true;
          return json(202, { job_id: "0badcafe", status: "pending", message: "Rebuild queued", job: { ...JOB, id: "0badcafe" } });
        },
        "GET /api/jobs/0badcafe": () => json(200, { ...JOB, id: "0badcafe", status: "running" }),
        "GET /api/deployments/21": () => json(200, { ...FAILED, id: 21, status: "running", error: null, finished_at: null, job_id: "0badcafe" }),
        "GET /api/deployments/21/log": () => json(200, { content: "", truncated: false, missing_reason: null }),
      },
      { layout: "releases" },
    );
    await user.click(await screen.findByRole("button", { name: "Rebuild this commit" }));
    const dialog = await screen.findByRole("dialog", { name: "Rebuild commit a94c0e2?" });
    expect(within(dialog).getByText(/is still on disk and is not the one live, it is switched to in seconds/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Rebuild" }));
    await waitFor(
      () => {
        expect(location().pathname).toBe(`/apps/${TAB_DOMAIN}/deployments/21`);
      },
      { timeout: 4_000 },
    );
  });

  it("explains an in-place rebuild: the checkout goes to the commit and the branch is followed again next time", async () => {
    const { user } = await pageAt(20, { [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, SERVED) });
    await user.click(await screen.findByRole("button", { name: "Rebuild this commit" }));
    const dialog = await screen.findByRole("dialog", { name: "Rebuild commit a94c0e2?" });
    expect(within(dialog).getByText(/the checkout is put on a94c0e2.*The next update follows main again\./)).toBeInTheDocument();
  });

  it("keeps a failed retry's error on screen, in the job's own words", async () => {
    const { user } = await pageAt(20, {
      [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () =>
        json(202, { job_id: "cafebabe", status: "pending", message: "Rebuild queued", job: { ...JOB, id: "cafebabe" } }),
      "GET /api/jobs/cafebabe": () =>
        json(200, { ...JOB, id: "cafebabe", status: "failed", error: "Commit a94c0e2 does not exist in the repository" }),
    });
    await user.click(await screen.findByRole("button", { name: "Retry" }));
    const title = await screen.findByText("The rebuild failed", {}, { timeout: 4_000 });
    const alert = title.closest<HTMLElement>("[role='alert']");
    if (alert === null) throw new Error("the failure is not announced");
    expect(within(alert).getByText("Commit a94c0e2 does not exist in the repository")).toBeInTheDocument();
  });

  it("says a rebuild that recorded no deploy of its own finished", async () => {
    const { user } = await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, SERVED),
      [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () =>
        json(202, { job_id: "cafebabe", status: "pending", message: "Rebuild queued", job: { ...JOB, id: "cafebabe" } }),
      "GET /api/jobs/cafebabe": () => json(200, { ...JOB, id: "cafebabe", status: "completed", completed_at: "2026-09-25T19:00:01" }),
    });
    await user.click(await screen.findByRole("button", { name: "Rebuild this commit" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Rebuild commit a94c0e2?" })).getByRole("button", { name: "Rebuild" }));
    // The job is only found "completed" on jobQuery's own 3s follow poll, not before.
    expect(await screen.findByText("Rebuilt commit a94c0e2: it is live.", {}, { timeout: 4_000 })).toBeInTheDocument();
  });

  it("offers no retry or rebuild for a deploy whose source is not git", async () => {
    await pageAt(20, { [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, git_commit: null }) });
    await screen.findByText("The deploy failed while building");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Rebuild this commit" })).not.toBeInTheDocument();
  });

  it("shows the commit's message beside its hash and branch", async () => {
    await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, commit_message: "Fix checkout total rounding" }),
    });
    expect(await screen.findByText("Fix checkout total rounding")).toBeInTheDocument();
  });

  it("says nothing ran a build for it when the deploy has no job (a CLI deploy)", async () => {
    // FAILED carries no job_id: the command line ran it, nothing queued it.
    await pageAt(20);
    await screen.findByText("The deploy failed while building");
    // Its own captured log is still read and shown; only a job's WebSocket/log never is.
    expect((await screen.findAllByText(/Type error: Property 'total' does not exist on type 'Order'\./)).length).toBeGreaterThan(0);
  });

  it("goes back to this deployment through the page's one question, opened on it, when the backend says it can", async () => {
    const ABLE = { ...SERVED, rollback_available: true, rollback_unavailable_reason: null, rollback_method: "instant", commit_message: "Add gift cards" };
    const { user, backend } = await pageAt(
      20,
      {
        [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, ABLE),
        "GET /api/deployments": () => json(200, { items: [{ ...SERVED, id: 21, release_id: "20260925-184247-2a8b7c4" }, ABLE], total: 2, next_before_id: null }),
        [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rollback`]: () =>
          json(202, { job_id: "5ca1ab1e", status: "pending", message: "Rollback queued", job: { ...JOB, id: "5ca1ab1e", type: "rollback" } }),
        "GET /api/jobs/5ca1ab1e": () => json(200, { ...JOB, id: "5ca1ab1e", type: "rollback", status: "completed", completed_at: "2026-09-25T19:00:01" }),
      },
      { layout: "releases" },
    );
    await user.click(await screen.findByRole("button", { name: "Go back to this version" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` });
    // Opened on the version of this page, not on the one before the live one.
    expect(await within(dialog).findByRole("button", { name: "Go back to #20" })).toBeInTheDocument();
    expect(within(dialog).getByText(/The code and the version/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Go back to #20" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rollback`)[0]?.body).toEqual({ schema_changed_ok: false });
    });
  });

  it("says an in-place rollback restores the deployment's snapshot, after a backup of the current state", async () => {
    const ABLE = { ...SERVED, snapshot_backup: "app_20260920_101500", rollback_available: true, rollback_method: "snapshot" };
    const { user } = await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, ABLE),
      "GET /api/deployments": () => json(200, { items: [{ ...SERVED, id: 21 }, ABLE], total: 2, next_before_id: null }),
    });
    await user.click(await screen.findByRole("button", { name: "Go back to this version" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` });
    await within(dialog).findByRole("button", { name: /^Go back to #/ });
    expect(dialog).toHaveTextContent(/The app's files from backup app_20260920_101500.*A backup of how it is now is taken first/);
  });

  it("offers nothing to go back to on a deployment that cannot be, and leaves the other versions to the header", async () => {
    const OTHER = { ...SERVED, id: 19, git_commit: "9f2c41a", rollback_available: true, rollback_method: "instant" };
    const { user } = await pageAt(
      20,
      {
        [`GET /api/deployments/${String(FAILED.id)}`]: () =>
          json(200, { ...SERVED, rollback_available: false, rollback_unavailable_reason: "Deployment 20 did not finish serving anything to go back to" }),
        "GET /api/deployments": () => json(200, { items: [{ ...SERVED, id: 21 }, { ...SERVED, rollback_available: false }, OTHER], total: 3, next_before_id: null }),
      },
      { layout: "releases" },
    );
    await screen.findByRole("button", { name: "Rebuild this commit" });
    expect(screen.queryByRole("button", { name: "Go back to this version" })).not.toBeInTheDocument();
    expect(screen.queryByText(/did not finish serving anything/)).not.toBeInTheDocument();
    // Roll back is one of the header's daily verbs, on the surface rather than in the menu.
    await user.click(screen.getByRole("button", { name: "Roll back" }));
    const chooser = await screen.findByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` });
    expect(await within(chooser).findByRole("button", { name: "Go back to #19" })).toBeInTheDocument();
  });

  it("opens a deploy that serves with the address it serves on, and what to do next the first time", async () => {
    const { user } = await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, SERVED),
      "GET /api/deployments": () => json(200, { items: [SERVED], total: 1, next_before_id: null }),
      "GET /api/certs": () => json(200, { certificates: [{ domain: TAB_DOMAIN, domains: [TAB_DOMAIN], days_remaining: 60, auto_renew: true }], total: 1 }),
    });
    expect(await screen.findByText(`https://${TAB_DOMAIN}`)).toBeInTheDocument();
    expect(screen.getByText("HTTPS is ready.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your own domain" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/domains`);
    expect(screen.getByRole("link", { name: "Add a database" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/database`);
    expect(screen.getByRole("link", { name: "Schedule a backup" })).toHaveAttribute("href", "/backups");
    expect(screen.getByRole("button", { name: "Copy the address" })).toBeInTheDocument();
    await user.keyboard("{Tab}");
  });

  it("says a deploy served over HTTP has no certificate yet, and offers the next steps only the first time", async () => {
    const older = { ...SERVED, id: 10, git_commit: "1111111" };
    await pageAt(20, {
      [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, SERVED),
      "GET /api/deployments": () => json(200, { items: [SERVED, older], total: 2, next_before_id: null }),
    });
    expect(await screen.findByText(/Served over HTTP: no certificate covers this address yet\./)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Add a database" })).not.toBeInTheDocument();
  });

  it("says Health does not apply to a static site, instead of that it is missing from the log", async () => {
    await pageAt(
      20,
      {
        [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, status: "success", error: null }),
        [`GET /api/deployments/${String(FAILED.id)}/log`]: () =>
          json(200, {
            content: "[2026-09-25 18:00:00] [1/6] Fetching source code...\n[2026-09-25 18:00:03] [5/6] Activating release...\n[2026-09-25 18:00:10] Deployed\n",
            truncated: false,
            missing_reason: null,
          }),
      },
      { status: "static", app_type: "static", port: null },
    );
    await waitFor(() => {
      expect(phaseStates()).toMatchObject({ activate: "done", health: "not_applicable" });
    });
    const phases = screen.getByRole("list", { name: "Deploy phases" });
    const health = phases.querySelector<HTMLElement>('[data-phase="health"]');
    if (health === null) throw new Error("no health phase");
    expect(within(health).getByText("Not applicable")).toBeInTheDocument();
    expect(within(health).queryByText("Not in the log")).not.toBeInTheDocument();
    // Measured from the log alone: seven seconds, never an hour of time-zone offset.
    expect(within(phases).getByText("7.0s")).toBeInTheDocument();
    expect(screen.getByText(/The startup check does not apply: a static site is served as files/)).toBeInTheDocument();
  });

  it("draws the timeline only once it knows whether the app is a static site, so nothing under it is rearranged", async () => {
    screenWidth(1440);
    let answer: (response: Response) => void = () => undefined;
    const slow = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const backend = fakeBackend(
      appRoutes(
        {},
        {
          [`GET /api/apps/${TAB_DOMAIN}`]: () => slow,
          [`GET /api/deployments/${String(FAILED.id)}`]: () => json(200, { ...FAILED, status: "success", error: null }),
          [`GET /api/deployments/${String(FAILED.id)}/log`]: () =>
            json(200, { content: "[2026-09-25 18:00:00] [1/6] Fetching source code...\n[2026-09-25 18:00:10] Deployed\n", truncated: false, missing_reason: null }),
          "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
        },
      ),
    );
    renderConsole(`/apps/${TAB_DOMAIN}/deployments/${String(FAILED.id)}`);

    // The deployment is in; the app is not: a skeleton holds the place, not a timeline with a
    // health phase that the answer would take away, and a note it would add.
    await waitFor(() => {
      expect(backend.callsTo(`GET /api/deployments/${String(FAILED.id)}`)).toHaveLength(1);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 150));
    });
    expect(screen.getByText("Loading the deployment")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Deploy phases" })).not.toBeInTheDocument();
    // Nothing of the page that would read the log has started either.
    expect(backend.callsTo(`GET /api/deployments/${String(FAILED.id)}/log`)).toHaveLength(0);

    answer(json(200, { ...TAB_APP, status: "static", app_type: "static", port: null }));
    await waitFor(() => {
      expect(phaseStates()).toMatchObject({ health: "not_applicable" });
    });
    expect(screen.getByText(/The startup check does not apply: a static site is served as files/)).toBeInTheDocument();
  });

  it("says plainly when there is no such deployment", async () => {
    await pageAt(99, { "GET /api/deployments/99": () => problem(404, "not_found", "Deployment not found: 99") });
    expect(await screen.findByRole("heading", { name: "No deployment 99" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await pageAt(20);
    await screen.findByText("The deploy failed while building");
    await screen.findByRole("region", { name: `Build log of deployment 20 of ${TAB_DOMAIN}` });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("renders the phases, the failure and the build log in Spanish", async () => {
    await act(() => setLocale("es"));
    await pageAt(20);
    expect(await screen.findByRole("heading", { level: 2, name: "Despliegue 20" })).toBeInTheDocument();
    expect(await screen.findByText("El despliegue falló al compilar")).toBeInTheDocument();
    expect(screen.getByText(/La salida de la propia compilación está en el registro de abajo/)).toBeInTheDocument();
    const phases = screen.getByRole("list", { name: "Fases del despliegue" });
    expect(within(phases).getByText("Obtención")).toBeInTheDocument();
    expect(within(phases).getAllByText("No alcanzada")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Registro de compilación" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Abrir Diagnóstico" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/diagnose`);
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
