import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { FakeEventSource, SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../test/fakes";
import { resetHandled } from "../../realtime/jobEnds";
import { EMPTY, taskStore } from "./store";
import { job } from "./taskFixtures";

/** This browser's session, which is how the master token's jobs are labelled: a task by it is the person's. */
const ME = "abcdef123456";

const MINE = job({ id: "mine1", actor: ME, progress: 46, current_step: "Building" });
const WAITING = job({ id: "mine2", actor: ME, status: "pending", state: "queued", progress: 0, waiting_for: "the deploy of shop.example.com", metadata: { domain: "docs.example.com" }, title: { code: "jobs.title.deploy", params: { domain: "docs.example.com" } }, name: "Deploy docs.example.com" });
const OTHERS = job({ id: "ben1", actor: "ben", type: "backup", name: "Back up shop_prod", metadata: {}, title: { code: "jobs.title.backup", params: { domain: "shop_prod" } }, progress: 35 });
const ENDED = job({ id: "gone1", actor: "ana", status: "completed", state: "succeeded", completed_at: new Date(Date.now() - 60_000).toISOString(), started_at: new Date(Date.now() - 120_000).toISOString(), metadata: { domain: "api.example.com" }, title: { code: "jobs.title.deploy", params: { domain: "api.example.com" } }, name: "Deploy api.example.com" });

let backend: FakeBackend;

function serve(routes: Record<string, RouteHandler> = {}): void {
  backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/sessions": () => json(200, { active_sessions: 1, current_session: `${ME}789`, sessions: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [MINE, WAITING, OTHERS], total: 3, active: 3 }),
    "GET /api/jobs": () => json(200, { jobs: [MINE, WAITING, OTHERS, ENDED], total: 4, active: 3 }),
    "GET /api/operations/active": () => json(200, { operations: [] }),
    ...routes,
  });
}

beforeEach(() => {
  window.localStorage.clear();
  taskStore.reset();
  resetHandled();
});

afterEach(() => {
  taskStore.update(() => EMPTY);
});

async function open(user: ReturnType<typeof renderConsole>["user"]) {
  const button = await screen.findByRole("button", { name: /^(Tasks|Tareas)[:,] / });
  await user.click(button);
  return screen.findByRole("dialog", { name: /^(Tasks|Tareas)$/ });
}

describe("the task centre's icon", () => {
  it("counts what is running, and says how many wait", async () => {
    serve();
    renderConsole("/apps");
    expect(await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" })).toBeInTheDocument();
  });

  it("says nothing is running when nothing is", async () => {
    serve({ "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }), "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }) });
    renderConsole("/apps");
    expect(await screen.findByRole("button", { name: "Tasks, nothing running" })).toBeInTheDocument();
  });
});

describe("the panel", () => {
  it("puts yours first, then what the server does that is not yours, then what ended lately", async () => {
    serve();
    const { user } = renderConsole("/apps");
    const panel = await open(user);
    const groups = (await within(panel).findAllByRole("region"))
      .map((group) => group.getAttribute("aria-labelledby"))
      .map((id) => (id === null ? "" : (document.getElementById(id)?.textContent ?? "")));
    expect(groups).toEqual(["Yours, running1", "Yours, queued1", "Now on the server1", "Ended lately1"]);
    expect(within(panel).getByRole("link", { name: "Open Deploy shop.example.com" })).toBeInTheDocument();
    expect(within(panel).getByRole("progressbar", { name: "Deploy shop.example.com" })).toHaveAttribute("aria-valuenow", "46");
    // What a queued job waits for is the queue's own sentence, as it came.
    expect(within(panel).getByText("the deploy of shop.example.com")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open Back up shop_prod" })).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open Deploy api.example.com" })).toBeInTheDocument();
  });

  it("says a task by its code in the console's language", async () => {
    serve();
    const { user } = renderConsole("/apps");
    await screen.findByRole("button", { name: /^Tasks[:,] / });
    const { setLocale } = await import("../../app/locale");
    await act(() => setLocale("es"));
    const panel = await open(user);
    expect(await within(panel).findByRole("link", { name: "Abrir Desplegar shop.example.com" })).toBeInTheDocument();
    expect(within(panel).getByText("Tuyas en marcha")).toBeInTheDocument();
  });

  it("moves with the stream: a job's step and progress, and an operation that starts and ends", async () => {
    serve();
    const { user } = renderConsole("/apps");
    const panel = await open(user);
    await within(panel).findByRole("link", { name: "Open Deploy shop.example.com" });
    const calls = backend.calls.length;

    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...MINE, progress: 80, current_step: "Starting" });
    });
    expect(await within(panel).findByRole("progressbar", { name: "Deploy shop.example.com" })).toHaveAttribute("aria-valuenow", "80");
    const asked = backend.calls.length;

    act(() => {
      FakeEventSource.latest().emit("operation", { id: "op-1", phase: "start", title: { code: "operations.app_restart", params: { target: "panel.example.net" } }, target: "panel.example.net", domain: "panel.example.net", actor: "marta", at: new Date().toISOString() });
    });
    expect(await within(panel).findByRole("link", { name: "Open Restart panel.example.net" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tasks: 3 running, 1 queued" })).toBeInTheDocument();

    act(() => {
      FakeEventSource.latest().emit("operation", { id: "op-1", phase: "end", title: { code: "operations.app_restart", params: { target: "panel.example.net" } }, target: "panel.example.net", domain: "panel.example.net", actor: "marta", at: new Date().toISOString(), result: "ok" });
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Tasks: 2 running, 1 queued" })).toBeInTheDocument();
    });
    expect(within(panel).getByRole("link", { name: "Open Restart panel.example.net" })).toBeInTheDocument();
    // The events moved it: an operation that starts and ends asks the server for nothing.
    expect(backend.calls).toHaveLength(asked);
    expect(calls).toBeGreaterThan(0);
  });

  it("shows a task's last lines of log when asked, from the server it is on", async () => {
    serve({ "GET /api/jobs/mine1/log": () => json(200, { content: "npm ci\nadded 812 packages", truncated: false }) });
    const { user } = renderConsole("/apps");
    const panel = await open(user);
    const rows = await within(panel).findAllByRole("button", { name: /^Log$/ });
    const first = rows[0];
    if (first === undefined) throw new Error("no row has a Log button");
    await user.click(first);
    expect(await within(panel).findByText(/added 812 packages/)).toBeInTheDocument();
  });

  it("says where a failed task stopped and what it printed, verbatim", async () => {
    const failed = job({ id: "bad1", actor: ME, status: "failed", state: "failed", error: "npm ERR! code ELIFECYCLE\nnpm ERR! errno 1", current_step: "Building", completed_at: new Date(Date.now() - 30_000).toISOString() });
    serve({ "GET /api/jobs": () => json(200, { jobs: [failed], total: 1, active: 0 }), "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }) });
    const { user } = renderConsole("/apps");
    const panel = await open(user);
    expect(await within(panel).findByText("npm ERR! code ELIFECYCLE")).toBeInTheDocument();
    expect(within(panel).getByText("Failed")).toBeInTheDocument();
  });

  it("has no accessibility violations when open", async () => {
    serve();
    const { user } = renderConsole("/apps");
    await open(user);
    await expectNoAxeViolations(document.body, { page: true });
  });
});

describe("pinning", () => {
  it("is a choice: a pinned task is a chip at the foot of every page, with its progress, until it is let go", async () => {
    serve();
    const { user, router } = renderConsole("/apps");
    const panel = await open(user);
    expect(screen.queryByRole("list", { name: "Pinned tasks" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "Pin Deploy shop.example.com to the bottom" }));
    const chips = await screen.findByRole("list", { name: "Pinned tasks" });
    expect(within(chips).getByRole("link", { name: /Deploy shop\.example\.com/ })).toBeInTheDocument();
    expect(within(chips).getByText("46 %, Building")).toBeInTheDocument();

    // It follows the person to another page, and a reload keeps it: the pin is the browser's.
    await act(() => router.navigate({ to: "/databases" }));
    expect(await screen.findByRole("list", { name: "Pinned tasks" })).toBeInTheDocument();
    expect(JSON.parse(window.localStorage.getItem("noust.tasks.pinned") ?? "[]")).toEqual(["|job:mine1"]);

    await user.click(within(screen.getByRole("list", { name: "Pinned tasks" })).getByRole("button", { name: "Unpin Deploy shop.example.com" }));
    await waitFor(() => {
      expect(screen.queryByRole("list", { name: "Pinned tasks" })).toBeNull();
    });
  });

  it("keeps the result of a pinned task that ended, until it is let go", async () => {
    serve();
    const { user } = renderConsole("/apps");
    const panel = await open(user);
    await user.click(within(panel).getByRole("button", { name: "Pin Deploy shop.example.com to the bottom" }));
    await screen.findByRole("list", { name: "Pinned tasks" });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...MINE, status: "completed", state: "succeeded", progress: 100, completed_at: new Date().toISOString() });
    });
    const chips = screen.getByRole("list", { name: "Pinned tasks" });
    await waitFor(() => {
      expect(within(chips).getByText("Succeeded")).toBeInTheDocument();
    });
  });

  it("is not made by itself: a task of yours that runs is not pinned", async () => {
    serve();
    renderConsole("/apps");
    await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" });
    expect(screen.queryByRole("list", { name: "Pinned tasks" })).toBeNull();
  });
});

describe("the end of a task of yours", () => {
  const ended = { ...MINE, status: "completed", state: "succeeded", progress: 100, completed_at: new Date().toISOString() };

  it("is said with a way to see it when you are on another page, once, and the server's own notice is not said again", async () => {
    serve();
    renderConsole("/databases");
    await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", ended);
      FakeEventSource.latest().emit("notice", { text: "Deploy shop.example.com", state: "active" });
    });
    const toasts = await screen.findByRole("region", { name: "Notifications" });
    expect(await within(toasts).findByText("Deploy shop.example.com: done")).toBeInTheDocument();
    expect(within(toasts).getByText("You started it and left that page.")).toBeInTheDocument();
    expect(within(toasts).getByRole("button", { name: "See" })).toBeInTheDocument();
    // One toast for one end: the server's notice is the same news, said by the task centre.
    expect(within(toasts).getAllByText(/Deploy shop\.example\.com/)).toHaveLength(1);
  });

  it("is not said when you are on its page: you are looking at it", async () => {
    serve();
    renderConsole("/apps/shop.example.com");
    await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", ended);
      FakeEventSource.latest().emit("notice", { text: "Deploy shop.example.com", state: "active" });
    });
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Tasks: 1 running, 1 queued" })).toBeInTheDocument();
    });
    const toasts = screen.getByRole("region", { name: "Notifications" });
    expect(within(toasts).queryByText(/Deploy shop\.example\.com/)).toBeNull();
  });

  it("is the server's own notice, as before, for a task that is not yours", async () => {
    serve();
    renderConsole("/databases");
    await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...OTHERS, status: "completed", state: "succeeded", completed_at: new Date().toISOString() });
      FakeEventSource.latest().emit("notice", { text: "Back up shop_prod", state: "active" });
    });
    const toasts = await screen.findByRole("region", { name: "Notifications" });
    expect(await within(toasts).findByText("Back up shop_prod")).toBeInTheDocument();
    expect(within(toasts).queryByRole("button", { name: "See" })).toBeNull();
  });

  it("is said in the console's language", async () => {
    serve();
    renderConsole("/databases");
    await screen.findByRole("button", { name: "Tasks: 2 running, 1 queued" });
    const { setLocale } = await import("../../app/locale");
    await act(() => setLocale("es"));
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", ended);
    });
    const toasts = await screen.findByRole("region", { name: /Notificaciones|Notifications/ });
    expect(await within(toasts).findByText("Desplegar shop.example.com: terminado")).toBeInTheDocument();
    expect(within(toasts).getByRole("button", { name: "Ver" })).toBeInTheDocument();
  });
});

describe("a task's page and the list", () => {
  // A screen as wide as a desktop's: tables are tables, not the phone's card rows.
  beforeEach(() => {
    vi.stubGlobal("matchMedia", (query: string) => ({ matches: true, media: query, onchange: null, addEventListener: () => undefined, removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false }));
  });

  it("the list names each task, who started it and when, and opens one", async () => {
    serve({ "GET /api/jobs/mine1": () => json(200, MINE) });
    const { user, location } = renderConsole("/activity/tasks");
    const table = await screen.findByRole("table", { name: "Tasks, latest first" });
    expect(await within(table).findByText("Deploy shop.example.com")).toBeInTheDocument();
    expect(within(table).getByText("Back up shop_prod")).toBeInTheDocument();
    expect(within(table).getAllByText("You").length).toBeGreaterThan(0);
    await user.click(within(table).getByText("Deploy shop.example.com"));
    await waitFor(() => {
      expect(location().pathname).toBe("/activity/tasks/mine1");
    });
  });

  it("opens with its steps and its state, and offers to pin and to cancel it", async () => {
    // The job's own answer does not say whether it can be stopped; the queue's does.
    serve({
      "GET /api/jobs/mine1": () => json(200, MINE),
      "GET /api/jobs/mine1/log": () => json(200, { content: "", truncated: false }),
      "GET /api/jobs/queue": () => json(200, { running: [{ ...MINE, cancellable: true }], queued: [], builds_running: 1, max_concurrent_builds: 2, queue_limit: 25, builds_queued: 0 }),
    });
    renderConsole("/activity/tasks/mine1");
    expect(await screen.findByRole("heading", { level: 1, name: "Deploy shop.example.com" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pin to the bottom" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /^Cancel / })).toBeEnabled();
    });
    expect(screen.getByRole("heading", { level: 2, name: "Steps" })).toBeInTheDocument();
  });

  it("says where a failed deploy stopped and what the tool printed, and queues it again from its page", async () => {
    const failed = job({ id: "bad1", actor: ME, status: "failed", state: "failed", error: "npm ERR! code ELIFECYCLE", deployment_id: 7, completed_at: new Date().toISOString() });
    serve({
      "GET /api/jobs/bad1": () => json(200, failed),
      "GET /api/jobs/bad1/log": () => json(200, { content: "", truncated: false }),
      "POST /api/jobs/update": () => json(202, { message: "queued", job: { ...job({ id: "again1", actor: ME }) } }),
      "GET /api/jobs/again1": () => json(200, job({ id: "again1", actor: ME })),
    });
    const { user, location } = renderConsole("/activity/tasks/bad1");
    expect(await screen.findByRole("heading", { level: 1, name: "Deploy shop.example.com" })).toBeInTheDocument();
    expect(screen.getByText("npm ERR! code ELIFECYCLE")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the deployment" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/activity/tasks/again1");
    });
    expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: "shop.example.com", force: false });
  });

  it("keeps Try again where it is for a role that cannot deploy, locked and with the reason one press away, and queues nothing", async () => {
    const failed = job({ id: "bad2", actor: ME, status: "failed", state: "failed", error: "npm ERR! code ELIFECYCLE", completed_at: new Date().toISOString() });
    serve({
      "GET /api/auth/session": () => json(200, { ...SESSION, permissions: ["self", "apps.read"], role: "viewer" }),
      "GET /api/jobs/bad2": () => json(200, failed),
      "GET /api/jobs/bad2/log": () => json(200, { content: "", truncated: false }),
    });
    renderConsole("/activity/tasks/bad2");
    expect(await screen.findByRole("heading", { level: 1, name: "Deploy shop.example.com" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Try again" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Why you cannot do this" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(0);
  });

  it("says plainly that a task is not there", async () => {
    serve({ "GET /api/jobs/ghost": () => json(404, { error: "not_found", detail: "Job not found: ghost", hint: null, fields: null, output: null }) });
    renderConsole("/activity/tasks/ghost");
    expect(await screen.findByText("That task is not here")).toBeInTheDocument();
  });
});
