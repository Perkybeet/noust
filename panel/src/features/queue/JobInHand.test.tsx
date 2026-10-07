import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { FakeEventSource, fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../app/testRoutes";

function job(extra: Record<string, unknown> = {}) {
  return {
    id: "u1",
    type: "update",
    name: `Update ${TAB_DOMAIN}`,
    description: `Updating the application at ${TAB_DOMAIN}`,
    status: "pending",
    progress: 0,
    total_steps: 100,
    current_step: "",
    created_at: "2026-10-06T10:00:00Z",
    started_at: null,
    completed_at: null,
    logs: [],
    metadata: { domain: TAB_DOMAIN },
    actor: "master",
    state: "queued",
    queue_position: 2,
    waiting_for: "The update of api.example.com, which holds a build slot",
    cancellable: true,
    cancel_requested: false,
    ...extra,
  };
}

function queue(running: unknown[], queued: unknown[]) {
  return { running, queued, builds_running: running.length, max_concurrent_builds: 2, queue_limit: 25, builds_queued: queued.length };
}

async function appWith(active: unknown[], routes: Record<string, RouteHandler> = {}, app: Record<string, unknown> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(app, {
      "GET /api/jobs/active": () => json(200, { jobs: active, total: active.length, active: active.length }),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      ...routes,
    }),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}`);
  const top = (await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN })).closest("header");
  if (!top) throw new Error("no page header");
  return { ...harness, backend, top };
}

describe("the job in hand, under an application's header", () => {
  it("says where a waiting job stands, what it waits for and what was merged into it, and cancels it from there", async () => {
    const waiting = job({ metadata: { domain: TAB_DOMAIN, merged_requests: 1 } });
    const { user, backend } = await appWith([waiting], {
      "GET /api/jobs/queue": () => json(200, queue([], [waiting])),
      "POST /api/jobs/u1/cancel": () => json(200, { message: "Job cancelled", job_id: "u1", state: "cancelled" }),
    });
    const title = await screen.findByText(`Updating ${TAB_DOMAIN}`);
    expect(screen.getByText("Waiting to start.")).toBeInTheDocument();
    expect(screen.getByText("Place 2 in line")).toBeInTheDocument();
    expect(screen.getByText("The update of api.example.com, which holds a build slot")).toBeInTheDocument();
    expect(screen.getByText("1 request merged into this one")).toBeInTheDocument();
    expect(title).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: `Cancel Updating ${TAB_DOMAIN}` }));
    const dialog = await screen.findByRole("alertdialog", { name: `Take Updating ${TAB_DOMAIN} out of the queue?` });
    await user.click(within(dialog).getByRole("button", { name: "Remove from queue" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/u1/cancel")).toHaveLength(1);
    });
  });

  it("says the next in line is next", async () => {
    const waiting = job({ queue_position: 1 });
    await appWith([waiting], { "GET /api/jobs/queue": () => json(200, queue([], [waiting])) });
    expect(await screen.findByText("Next in line")).toBeInTheDocument();
  });

  it("stops a running build after saying what it leaves alone, and says it is stopping once asked", async () => {
    const building = job({ status: "running", state: "running", queue_position: null, waiting_for: null, started_at: "2026-10-06T10:00:01Z", cancellable: true });
    let asked = false;
    const { user, backend } = await appWith([building], {
      "GET /api/jobs/queue": () => json(200, queue([asked ? { ...building, cancellable: false, cancel_requested: true } : building], [])),
      "POST /api/jobs/u1/cancel": () => {
        asked = true;
        return json(200, { message: "Cancellation requested", job_id: "u1", state: "cancelling" });
      },
    });
    await user.click(await screen.findByRole("button", { name: `Cancel Updating ${TAB_DOMAIN}` }));
    const dialog = await screen.findByRole("alertdialog", { name: `Stop the build of ${TAB_DOMAIN}?` });
    expect(within(dialog).getByText(/What serves now is not touched/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop build" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/u1/cancel")).toHaveLength(1);
    });
    // The queue is read again, and says it was asked to stop.
    expect(await screen.findByText("Stopping…")).toBeInTheDocument();
  });

  it("disables Cancel once its release is being activated, saying why beside it", async () => {
    const activating = job({ status: "running", state: "running", queue_position: null, waiting_for: null, cancellable: false });
    await appWith(
      [activating],
      {
        "GET /api/jobs/queue": () => json(200, queue([activating], [])),
        // The application builds beside what serves: the one kind that can be stopped until it activates.
        "GET /api/apps": () => json(200, { total: 1, apps: [{ domain: TAB_DOMAIN, name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "releases" }] }),
      },
      { layout: "releases" },
    );
    const button = await screen.findByRole("button", { name: `Cancel Updating ${TAB_DOMAIN}` });
    await waitFor(() => {
      expect(button).toBeDisabled();
    });
    expect(screen.getByText(/Its release is being activated, which cannot be stopped halfway/)).toBeInTheDocument();
  });

  it("does not guess for a running job whose event says nothing about it, until the queue answers", async () => {
    // The `job` event carries no `cancellable`: until the queue says, the button waits, silently.
    const running = job({ status: "running", state: "running", queue_position: null, waiting_for: null, cancellable: undefined });
    await appWith([running], { "GET /api/jobs/queue": () => problem(503, "unavailable", "The queue is not answering") });
    const button = await screen.findByRole("button", { name: `Cancel Updating ${TAB_DOMAIN}` });
    expect(button).toBeDisabled();
    expect(screen.queryByText(/cannot be stopped/)).not.toBeInTheDocument();
  });

  it("follows the job's events: a place moves up the line without a new read of the page", async () => {
    const waiting = job({ queue_position: 2 });
    let position = 2;
    await appWith([waiting], { "GET /api/jobs/queue": () => json(200, queue([], [{ ...waiting, queue_position: position }])) });
    expect(await screen.findByText("Place 2 in line")).toBeInTheDocument();
    position = 1;
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...waiting, queue_position: 1 });
    });
    expect(await screen.findByText("Next in line")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    const waiting = job();
    const { top } = await appWith([waiting], { "GET /api/jobs/queue": () => json(200, queue([], [waiting])) });
    await screen.findByText("Place 2 in line");
    await expectNoAxeViolations(top.parentElement ?? top);
    await act(() => setLocale("es"));
    expect(await screen.findByText("Lugar 2 de la cola")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: `Cancelar Actualizando ${TAB_DOMAIN}` })).toBeInTheDocument();
  });
});

describe("a request for an update that is already waiting", () => {
  it("says it joined the one waiting, instead of queueing a second", async () => {
    const waiting = job({ metadata: { domain: TAB_DOMAIN, merged_requests: 1 } });
    const { user, backend } = await appWith([], {
      "GET /api/jobs/queue": () => json(200, queue([], [])),
      "POST /api/jobs/update": () => json(202, { message: "An update of this application is already waiting; this request joined it", job: waiting }),
      [`GET /api/jobs/${waiting.id}`]: () => json(200, waiting),
    });
    await user.click(await screen.findByRole("button", { name: "Update" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1);
    });
    expect(await screen.findByText(`${TAB_DOMAIN} was already waiting for an update. This request joined it: one update builds the branch as it is when its turn comes, so nothing is lost and nothing builds twice.`)).toBeInTheDocument();
  });
});

describe("a deploy that the queue had no room for", () => {
  it("says the queue is full, how long the server asked to wait, and where to cancel", async () => {
    const { user } = await appWith([], {
      "GET /api/jobs/queue": () => json(200, queue([], [])),
      "POST /api/jobs/update": () =>
        problem(429, "queue_full", "The deploy queue is full: 25 requests are waiting", {
          hint: "Builds run a few at a time... noust config set deploy.queue_limit <number>.",
          headers: { "Retry-After": "30" },
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Update" }));
    expect(await screen.findByText("The deploy queue is full: 25 requests are waiting")).toBeInTheDocument();
    expect(screen.getByText(/Try again in 30 seconds, or cancel what no longer needs to wait/)).toBeInTheDocument();
    expect(screen.queryByText(/noust config set deploy\.queue_limit/)).not.toBeInTheDocument();
  });
});
