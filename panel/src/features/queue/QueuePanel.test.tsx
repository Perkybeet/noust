import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

function at(rows: readonly HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (!row) throw new Error(`No row ${String(index)}`);
  return row;
}

const SHOP = "shop.example.com";
const API = "api.example.com";

function job(id: string, extra: Record<string, unknown>) {
  return {
    id,
    type: "update",
    name: `Update ${SHOP}`,
    description: `Updating the application at ${SHOP}`,
    status: "running",
    progress: 40,
    total_steps: 100,
    current_step: "npm ci",
    created_at: "2026-10-06T10:00:00Z",
    started_at: "2026-10-06T10:00:01Z",
    completed_at: null,
    result: null,
    error: null,
    logs: [],
    metadata: { domain: SHOP },
    actor: "master",
    state: "running",
    queue_position: null,
    waiting_for: null,
    cancellable: true,
    cancel_requested: false,
    ...extra,
  };
}

const RUNNING = job("r1", {});
const NEXT = job("q1", {
  name: `Update ${API}`,
  status: "pending",
  state: "queued",
  started_at: null,
  queue_position: 1,
  waiting_for: "The update of api.example.com is not finished",
  metadata: { domain: API, merged_requests: 2 },
});
const LATER = job("q2", {
  type: "backup",
  name: `Backup ${SHOP}`,
  status: "pending",
  state: "queued",
  started_at: null,
  queue_position: 2,
  waiting_for: "Another job that changes the whole server runs first",
});

function queue(running: unknown[], queued: unknown[]) {
  return { running, queued, builds_running: running.length, max_concurrent_builds: 2, queue_limit: 25, builds_queued: queued.length };
}

const RELEASE_APPS = {
  total: 2,
  apps: [
    { domain: SHOP, name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "releases" },
    { domain: API, name: "api", app_type: "python", status: "running", active: true, enabled: true, port: 8000, layout: "inplace" },
  ],
};

async function activityWith(routes: Record<string, RouteHandler>, path = "/activity") {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, RELEASE_APPS),
    "GET /api/audit": () => json(200, { items: [], next_before: null }),
    ...routes,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Activity" });
  const heading = await screen.findByRole("heading", { level: 2, name: "Deploy queue" });
  const panel = heading.closest("section");
  if (!panel) throw new Error("no queue panel");
  return { ...harness, backend, panel };
}

describe("the deploy queue on the Activity page", () => {
  it("lists what builds and what waits, in order, with the place in line and what each waits for", async () => {
    const { panel } = await activityWith({ "GET /api/jobs/queue": () => json(200, queue([RUNNING], [NEXT, LATER])) });
    const rows = within(panel).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(at(rows, 0)).getByText("Updating shop.example.com")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByText("In progress")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByText(/Running since/)).toBeInTheDocument();
    // Waiting: the state, the place, and what it waits for in the queue's own words.
    expect(within(at(rows, 1)).getByText("Queued")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("Next in line")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("The update of api.example.com is not finished")).toBeInTheDocument();
    expect(within(at(rows, 2)).getByText("Place 2 in line")).toBeInTheDocument();
    expect(within(at(rows, 2)).getByText("Another job that changes the whole server runs first")).toBeInTheDocument();
    // The limits that decide it.
    expect(within(panel).getByText("1 of 2 builds running")).toBeInTheDocument();
    expect(within(panel).getByText("2 of 25 waiting")).toBeInTheDocument();
  });

  it("says a request merged into a waiting one, so nothing is queued twice", async () => {
    const { panel } = await activityWith({ "GET /api/jobs/queue": () => json(200, queue([RUNNING], [NEXT])) });
    expect(within(panel).getByText("2 requests merged into this one")).toBeInTheDocument();
  });

  it("takes a waiting job out of the line after one question that says what it does", async () => {
    const { panel, user, backend } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([RUNNING], [NEXT])),
      "POST /api/jobs/q1/cancel": () => json(200, { message: "Job cancelled", job_id: "q1", state: "cancelled" }),
    });
    await user.click(within(panel).getByRole("button", { name: "Cancel Updating api.example.com" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Take Updating api.example.com out of the queue?" });
    expect(within(dialog).getByText(/It has not started\. Cancelling removes it from the line and nothing on the server changes/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/jobs/q1/cancel")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Remove from queue" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/q1/cancel")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("stops a build that has not started to activate its release, saying what that leaves alone", async () => {
    const { panel, user, backend } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([RUNNING], [])),
      "POST /api/jobs/r1/cancel": () => json(200, { message: "Cancellation requested", job_id: "r1", state: "cancelling" }),
    });
    await user.click(within(panel).getByRole("button", { name: "Cancel Updating shop.example.com" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop the build of shop.example.com?" });
    expect(within(dialog).getByText(/What serves now is not touched, and the next request in the queue starts/)).toBeInTheDocument();
    expect(within(dialog).getByText(/until its release starts to be activated; after that it finishes by itself/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop build" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/r1/cancel")).toHaveLength(1);
    });
  });

  it("disables Cancel once the release is being activated, and says why on the page", async () => {
    const { panel } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([job("r1", { cancellable: false })], [])),
    });
    const button = within(panel).getByRole("button", { name: "Cancel Updating shop.example.com" });
    expect(button).toBeDisabled();
    const reason = within(panel).getByText(/Its release is being activated, which cannot be stopped halfway/);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  });

  it("says a build in the folder that serves, and a job that changes the system, cannot be stopped", async () => {
    const inPlace = job("r2", { name: `Update ${API}`, cancellable: false, metadata: { domain: API } });
    const restore = job("r3", { type: "restore", name: `Restore ${SHOP}`, cancellable: false });
    const { panel } = await activityWith({ "GET /api/jobs/queue": () => json(200, queue([inPlace, restore], [])) });
    // The application's layout is read from the list of applications, which arrives on its own.
    expect(await within(panel).findByText(/builds in the folder that serves, or in Docker for a Compose stack/)).toBeInTheDocument();
    expect(within(panel).getByText(/halfway through changing the system and cannot be stopped safely/)).toBeInTheDocument();
    for (const button of within(panel).getAllByRole("button", { name: /^Cancel / })) expect(button).toBeDisabled();
  });

  it("says a stop was asked and the build is winding down", async () => {
    const { panel } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([job("r1", { cancellable: false, cancel_requested: true })], [])),
    });
    expect(within(panel).getAllByText("Stopping").length).toBeGreaterThan(0);
    expect(within(panel).getByText(/It was asked to stop and is winding down/)).toBeInTheDocument();
    const button = within(panel).getByRole("button", { name: "Cancel Updating shop.example.com" });
    expect(button).toHaveAttribute("aria-disabled", "true");
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("keeps the question open with the server's own words when the release started to activate meanwhile", async () => {
    const { panel, user } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([RUNNING], [])),
      "POST /api/jobs/r1/cancel": () =>
        problem(409, "cancel_refused", "The release of shop.example.com is being activated", {
          hint: "It finishes by itself: roll back afterwards if it is not what you want.",
        }),
    });
    await user.click(within(panel).getByRole("button", { name: "Cancel Updating shop.example.com" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Stop build" }));
    expect(await within(dialog).findByText("The release of shop.example.com is being activated")).toBeInTheDocument();
    expect(within(dialog).getByText("It finishes by itself: roll back afterwards if it is not what you want.")).toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
  });

  it("opens a job's log from its row", async () => {
    const { panel, user } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([RUNNING], [])),
      "GET /api/jobs/r1/log": () => json(200, { content: "[12:00:00] [INFO] npm ci", truncated: false }),
    });
    await user.click(within(panel).getByRole("button", { name: "View log of Updating shop.example.com" }));
    expect(await screen.findByRole("dialog", { name: `Update ${SHOP}` })).toBeInTheDocument();
  });

  it("draws nothing while nothing builds or waits", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/audit": () => json(200, { items: [], next_before: null }) });
    renderConsole("/activity");
    await screen.findByRole("heading", { level: 1, name: "Activity" });
    await screen.findByRole("region", { name: /Activity/ });
    expect(screen.queryByRole("heading", { name: "Deploy queue" })).not.toBeInTheDocument();
  });

  it("says when the queue cannot be read, and reads it again on request", async () => {
    let answers = 0;
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/audit": () => json(200, { items: [], next_before: null }),
      "GET /api/jobs/queue": () => {
        answers += 1;
        return answers === 1 ? problem(500, "internal_error", "The queue is unreadable") : json(200, queue([RUNNING], []));
      },
    });
    const { user } = renderConsole("/activity");
    await screen.findByRole("heading", { level: 1, name: "Activity" });
    expect(await screen.findByText("Could not read the deploy queue", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText("The queue is unreadable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy queue" })).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    const { panel } = await activityWith({
      "GET /api/jobs/queue": () => json(200, queue([RUNNING, job("r9", { cancellable: false, type: "restore", name: `Restore ${SHOP}` })], [NEXT, LATER])),
    });
    await expectNoAxeViolations(panel);
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Cola de despliegues" })).toBeInTheDocument();
    expect(within(panel).getByText("Siguiente en la cola")).toBeInTheDocument();
    expect(within(panel).getByText("2 de 2 compilaciones en curso")).toBeInTheDocument();
    await expectNoAxeViolations(panel);
  });
});

describe("the queue of one application", () => {
  it("shows only what is of the application, above its deploy history", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/apps": () => json(200, RELEASE_APPS),
      [`GET /api/apps/${SHOP}`]: () => json(200, RELEASE_APPS.apps[0]),
      "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
      "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
      "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
      "GET /api/deployments": () => json(200, { items: [], total: 0 }),
      [`GET /api/apps/${SHOP}/releases`]: () => json(200, { domain: SHOP, items: [], total: 0 }),
      "GET /api/jobs/queue": () => json(200, queue([RUNNING], [NEXT, LATER])),
    });
    renderConsole(`/apps/${SHOP}/deployments`);
    const heading = await screen.findByRole("heading", { level: 2, name: `Queue of ${SHOP}` });
    const panel = heading.closest("section");
    if (!panel) throw new Error("no queue panel");
    // Its own update and its own backup; the other application's update is not its business.
    expect(within(panel).getByText("Updating shop.example.com")).toBeInTheDocument();
    expect(within(panel).getByText("Backing up shop.example.com")).toBeInTheDocument();
    expect(within(panel).queryByText("Updating api.example.com")).not.toBeInTheDocument();
    expect(within(panel).queryByText(/builds running/)).not.toBeInTheDocument();
  });
});
