import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WIDGETS } from "../../components/widgets/catalog";
import { toDocument } from "../../components/widgets/model";
import type { Placed } from "../../components/widgets/model";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import type { SessionInfo } from "../../api/queries/auth";
import { NODES, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { flushPanelStore, readPanelCache } from "./panelStore";
import { overviewFixture } from "./testFixtures";

const AGO = (minutes: number): string => new Date(Date.now() - minutes * 60_000).toISOString();

const ATTENTION = [
  {
    id: "app:admin.example.com",
    subject: { kind: "app", domain: "admin.example.com" },
    title: "admin.example.com",
    severity: "fail",
    reasons: [
      { kind: "state", severity: "fail", code: "service_failed", params: {}, detail: null, when: null, deployment_id: null, actions: ["view_log", "diagnose"] },
      { kind: "deploy", severity: "fail", code: "deploy_failed", params: {}, detail: "npm ERR! code ELIFECYCLE", when: AGO(4), deployment_id: 12, actions: ["view_deployment", "diagnose"] },
    ],
  },
  {
    id: "unit:queue-worker",
    subject: { kind: "unit", name: "queue-worker" },
    title: "queue-worker",
    severity: "fail",
    reasons: [{ kind: "unit", severity: "fail", code: "unit_failed", params: {}, detail: null, when: null, deployment_id: null, actions: ["open_service"] }],
  },
  {
    id: "app:shop.example.com",
    subject: { kind: "app", domain: "shop.example.com" },
    title: "shop.example.com",
    severity: "warn",
    reasons: [{ kind: "certificate", severity: "warn", code: "certificate_expires_in", params: { days: 12, valid_until: "2026-10-11" }, detail: null, when: null, deployment_id: null, actions: ["renew_certificate"] }],
  },
];

const OVERVIEW = overviewFixture({
  attention: ATTENTION,
  attention_total: 3,
  activity: [
    { id: "job:1", type: "deploy", title: "Deploy shop.example.com", status: "completed", domain: "shop.example.com", actor: "master", at: AGO(1) },
    { id: "job:2", type: "update", title: "Update admin.example.com", status: "failed", domain: "admin.example.com", actor: "master", at: AGO(2) },
  ],
});

const APPS = {
  total: 4,
  apps: [
    { domain: "admin.example.com", name: "admin", status: "failed", active: false, enabled: true, layout: "inplace" },
    { domain: "shop.example.com", name: "shop", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "blog.example.com", name: "blog", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "old.example.com", name: "old", status: "stopped", active: false, enabled: false, layout: "inplace" },
  ],
};

const EMPTY = overviewFixture({
  apps: { running: 0, failed: 0, stopped: 0, static: 0, unmanaged: 0, error: null },
  backups: { ...overviewFixture().backups, apps: 0, scheduled: 0, with_backup_24h: 0 },
});

/** The access token is no account: the server would answer 400, so the console does not ask and the browser keeps the panels. */
const NO_ACCOUNT: RouteHandler = () => problem(400, "validation_error", "Only an account keeps panels");

/** The session of an account, which is the one that keeps its panels on the server. */
const AS_ACCOUNT: Record<string, RouteHandler> = {
  "GET /api/auth/session": () => json(200, { ...SESSION, account: { username: "ana", role: "admin" } as NonNullable<SessionInfo["account"]> }),
};

function routes(extra: Record<string, RouteHandler> = {}, overview = OVERVIEW): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    // A test that answers the server's panels is signed in as an account: only an account is asked for them.
    ...("GET /api/auth/dashboards" in extra ? AS_ACCOUNT : {}),
    "GET /api/overview": () => json(200, overview),
    "GET /api/apps": () => json(200, APPS),
    "GET /api/auth/dashboards": NO_ACCOUNT,
    "GET /api/deployments": () => json(200, { total: 0, items: [], next_before_id: null }),
    "GET /api/certs": () => json(200, { total: 0, certificates: [] }),
    "GET /api/backups": () => json(200, { total: 0, backups: [] }),
    "GET /api/jobs/active": () => json(200, { active: 0, total: 0, jobs: [] }),
    "GET /api/operations/active": () => json(200, { operations: [] }),
    "GET /api/config": () => json(200, { config: { notifications: { enabled: false } }, path: "/etc/noust/config.yaml" }),
    ...extra,
  };
}

async function home(extra?: Record<string, RouteHandler>, path = "/", overview?: ReturnType<typeof overviewFixture>, title = "This server") {
  const backend = fakeBackend(routes(extra, overview));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: title });
  return { ...harness, backend };
}

/** The names of the widgets on the page, in the order they are drawn. */
async function widgetsOnPage(): Promise<string[]> {
  await waitFor(() => expect(screen.getAllByRole("heading", { level: 2 }).length).toBeGreaterThan(0));
  return screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
}

/** The header of the page: its title, what it follows and its actions, apart from what the widgets draw. */
function header(): HTMLElement {
  const found = document.querySelector<HTMLElement>("[data-slot=header]");
  if (found === null) throw new Error("no header");
  return found;
}

function document1(layout: Placed[]): Record<string, unknown> {
  return toDocument(layout) as unknown as Record<string, unknown>;
}

function placed(type: string, size: Placed["size"]): Placed {
  return { id: type, type, size, settings: {} };
}

/** What the server answers for an account that kept some panels. */
function kept(...panels: { id: string; name: string; scope?: string; layout: Placed[] }[]): RouteHandler {
  return () =>
    json(200, {
      dashboards: panels.map((panel, position) => ({ id: panel.id, name: panel.name, scope: panel.scope ?? "server", position, layout: document1(panel.layout), created_at: 1, updated_at: 1 })),
      limits: { dashboards: 20, widgets: 40, columns: 12, document_bytes: 32768, settings_bytes: 2048 },
    });
}

describe("Home, the panel of a server", () => {
  it("draws the panel that comes with the console: its widgets, in their order, under the name of the panel", async () => {
    await home();
    expect(await widgetsOnPage()).toEqual([
      "Needs attention",
      "Server status",
      "Resources",
      "Work in progress",
      "Certificates",
      "Backups",
      "Applications",
      "Quick actions",
      "Recent deployments",
      "Recent activity",
    ]);
    // It says what it follows, in words.
    expect(screen.getByText(/Follows the chosen server/)).toBeInTheDocument();
  });

  it("lists what needs attention a line each, with the system's own words after the sentence", async () => {
    await home();
    const heading = await screen.findByRole("heading", { level: 2, name: "Needs attention" });
    const card = heading.closest("article");
    if (card === null) throw new Error("no widget");
    expect(await within(card).findByText("admin.example.com")).toBeInTheDocument();
    expect(within(card).getByText("npm ERR! code ELIFECYCLE")).toHaveClass("mono");
    expect(within(card).getByText("Certificate expires in 12 days")).toBeInTheDocument();
  });

  it("adds an application nothing answers on, which only the console knows", async () => {
    await home({ "GET /api/apps": () => json(200, { total: 1, apps: [{ domain: "api.example.com", name: "api", status: "no_answer", active: true, enabled: true, layout: "inplace" }] }) });
    expect(await screen.findByText("The service runs, but nothing answers on its port")).toBeInTheDocument();
  });

  it("has one primary action, the header's, and a way to customize", async () => {
    await home();
    await widgetsOnPage();
    expect(within(header()).getAllByRole("link", { name: "New application" })).toHaveLength(1);
    expect(within(header()).getByRole("link", { name: "Customize" })).toBeInTheDocument();
  });

  it("welcomes an empty server with its first steps instead of a board of nothing", async () => {
    await home(undefined, "/", EMPTY);
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy your first app" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Needs attention" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Customize" })).not.toBeInTheDocument();
    expect(within(header()).getAllByRole("link", { name: "New application" })).toHaveLength(1);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has no accessibility violations", async () => {
    await home();
    await widgetsOnPage();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("shows a role of a team the applications' widgets and none of the machine's", async () => {
    const team = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read", "backups.read", "databases.read"] };
    await home({ "GET /api/auth/session": () => json(200, team), "GET /api/overview": () => problem(403, "forbidden", "No") });
    const names = await widgetsOnPage();
    expect(names).toEqual(expect.arrayContaining(["Applications", "Work in progress", "Certificates", "Backups", "Recent deployments"]));
    for (const machine of ["Needs attention", "Server status", "Resources", "Recent activity"]) expect(names).not.toContain(machine);
  });

  it("speaks Spanish, with the names of the glossary", async () => {
    await act(() => setLocale("es"));
    fakeBackend(routes());
    renderConsole("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Este servidor" })).toBeInTheDocument();
    const names = await widgetsOnPage();
    expect(names).toEqual(expect.arrayContaining(["Requiere atención", "Estado del servidor", "Tareas en curso", "Aplicaciones", "Actividad reciente", "Acciones rápidas"]));
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Home, waiting for the panels", () => {
  it("keeps the room of the panel with skeletons, in a region that says what it reads, until the server has said", async () => {
    let answer: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    fakeBackend(routes({ "GET /api/auth/dashboards": () => pending }));
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "This server" });
    const region = await screen.findByText("Loading Home…");
    expect(region.closest("[aria-busy=true]")).not.toBeNull();
    expect(screen.queryByRole("heading", { level: 2, name: "Needs attention" })).not.toBeInTheDocument();
    answer(json(200, { dashboards: [], limits: { dashboards: 20, widgets: 40, columns: 12, document_bytes: 32768, settings_bytes: 2048 } }));
    expect(await screen.findByRole("heading", { level: 2, name: "Needs attention" })).toBeInTheDocument();
  });

  it("draws a panel at once from what this browser holds, without waiting for the server", async () => {
    window.localStorage.setItem("noust.panels", JSON.stringify({ version: 1, settled: true, panels: [{ id: "server", name: "This server", scope: "server", layout: document1([placed("apps", "wide")]), dirty: false }] }));
    fakeBackend(routes({ "GET /api/auth/dashboards": () => new Promise<Response>(() => undefined) }));
    renderConsole("/");
    expect(await screen.findByRole("heading", { level: 2, name: "Applications" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Needs attention" })).not.toBeInTheDocument();
  });
});

describe("Home, the panels an account keeps", () => {
  it("draws the layout the server kept for the account in place of the one that comes with the console", async () => {
    await home({ "GET /api/auth/dashboards": kept({ id: "server", name: "This server", layout: [placed("apps", "wide"), placed("backups", "medium")] }) });
    expect(await widgetsOnPage()).toEqual(["Applications", "Backups"]);
  });

  it("draws one of its own panels at its address, and the way back to the ones that come with the console", async () => {
    await home({ "GET /api/auth/dashboards": kept({ id: "data", name: "Data", layout: [placed("backups", "wide")] }) }, "/?panel=data", undefined, "Data");
    expect(await widgetsOnPage()).toEqual(["Backups"]);
  });

  it("asks the server for nothing about panels when the panel is an access token's", async () => {
    const { backend } = await home();
    await widgetsOnPage();
    // It would answer 400, which a browser logs as an error on every page that draws Home.
    expect(backend.callsTo("GET /api/auth/dashboards")).toHaveLength(0);
    expect(backend.callsTo("PUT /api/auth/dashboards/server")).toHaveLength(0);
  });
});

describe("Home, customized", () => {
  it("gives each widget its handle, its size and its remove button, and the header Reset and Done in place of Customize", async () => {
    const { user } = await home();
    await widgetsOnPage();
    await user.click(within(header()).getByRole("link", { name: "Customize" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Customize “This server”" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move Needs attention" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Size and position of Needs attention: Wide$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Needs attention" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reset" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Done" })).toBeInTheDocument();
    expect(within(header()).queryByRole("link", { name: "New application" })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("keeps what is removed, in this browser for the access token, and draws it so after a reload", async () => {
    const { user, unmount } = await home(undefined, "/?customise=true", undefined, "Customize “This server”");
    await user.click(await screen.findByRole("button", { name: "Remove Resources" }));
    await waitFor(() => expect(screen.queryByRole("heading", { level: 2, name: "Resources" })).not.toBeInTheDocument());
    flushPanelStore();
    const stored = JSON.parse(window.localStorage.getItem("noust.panels") ?? "null") as { panels: { id: string; layout: { widgets: { type: string }[] } }[] };
    expect(stored.panels.map((panel) => panel.id)).toEqual(["server"]);
    expect(stored.panels[0]?.layout.widgets.map((widget) => widget.type)).not.toContain("resources");
    unmount();
    fakeBackend(routes());
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "This server" });
    const names = await widgetsOnPage();
    expect(names).toContain("Applications");
    expect(names).not.toContain("Resources");
  });

  it("tells the server a moment after the last change, in the document the server judges, for an account", async () => {
    const { user, backend } = await home({ "GET /api/auth/dashboards": kept(), "PUT /api/auth/dashboards/server": (call) => json(200, { id: "server", name: "This server", scope: "server", position: 0, layout: (call.body as { layout: unknown }).layout, created_at: 1, updated_at: 2 }) }, "/?customise=true", undefined, "Customize “This server”");
    await user.click(await screen.findByRole("button", { name: "Remove Resources" }));
    await user.click(await screen.findByRole("button", { name: "Remove Work in progress" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/auth/dashboards/server")).toHaveLength(1));
    const body = backend.callsTo("PUT /api/auth/dashboards/server")[0]?.body as { name: string; scope: string; layout: { version: number; widgets: { id: string; type: string; x: number; w: number; settings: Record<string, unknown> }[] } };
    expect(body.name).toBe("This server");
    expect(body.scope).toBe("server");
    expect(body.layout.version).toBe(1);
    const types = body.layout.widgets.map((widget) => widget.type);
    expect(types).not.toContain("resources");
    expect(types).not.toContain("work");
    expect(types).toContain("attention");
    for (const widget of body.layout.widgets) expect(widget.x + widget.w).toBeLessThanOrEqual(12);
    expect(new Set(body.layout.widgets.map((widget) => widget.id)).size).toBe(body.layout.widgets.length);
  });

  it("puts a panel that came with the console back as it came: the server forgets what it kept", async () => {
    const { user, backend } = await home({ "GET /api/auth/dashboards": kept({ id: "server", name: "This server", layout: [placed("apps", "wide")] }), "DELETE /api/auth/dashboards/server": () => json(200, { dashboards: [], limits: {} }) }, "/?customise=true", undefined, "Customize “This server”");
    expect(await widgetsOnPage()).toEqual(["Applications"]);
    await user.click(screen.getByRole("button", { name: "Reset" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/auth/dashboards/server")).toHaveLength(1));
    expect(await screen.findByRole("heading", { level: 2, name: "Needs attention" })).toBeInTheDocument();
    expect(readPanelCache().panels).toEqual([]);
  });

  it("sends again what the server has not heard of, the next time Home opens", async () => {
    window.localStorage.setItem("noust.panels", JSON.stringify({ version: 1, settled: true, panels: [{ id: "server", name: "This server", scope: "server", layout: document1([placed("apps", "wide")]), dirty: true }] }));
    const backend = fakeBackend(routes({ "GET /api/auth/dashboards": kept(), "PUT /api/auth/dashboards/server": (call) => json(200, { id: "server", name: "This server", scope: "server", position: 0, layout: (call.body as { layout: unknown }).layout, created_at: 1, updated_at: 2 }) }));
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "This server" });
    await waitFor(() => expect(backend.callsTo("PUT /api/auth/dashboards/server")).toHaveLength(1));
    expect(readPanelCache().panels[0]?.layout).toEqual(document1([placed("apps", "wide")]));
    await waitFor(() => expect(readPanelCache().panels[0]?.dirty).toBe(false));
  });

  it("says when the server could not keep a change, and keeps it here", async () => {
    const { user } = await home({ "GET /api/auth/dashboards": kept(), "PUT /api/auth/dashboards/server": () => problem(500, "internal_error", "The store is locked") }, "/?customise=true", undefined, "Customize “This server”");
    await user.click(await screen.findByRole("button", { name: "Remove Resources" }));
    expect(await screen.findByText("Could not save This server")).toBeInTheDocument();
    expect(readPanelCache().panels[0]?.dirty).toBe(true);
  });
});

describe("Home, a panel of one's own", () => {
  const MINE = kept({ id: "data", name: "Data and backups", layout: [placed("backups", "wide"), placed("apps", "medium")] });

  it("has a menu to rename it and to delete it, which the panels that come with the console do not have", async () => {
    const { user } = await home({ "GET /api/auth/dashboards": MINE }, "/?panel=data", undefined, "Data and backups");
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: "Rename panel" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Delete panel" })).toBeInTheDocument();
  });

  it("is renamed, and the server is told", async () => {
    const { user, backend } = await home(
      { "GET /api/auth/dashboards": MINE, "PUT /api/auth/dashboards/data": (call) => json(200, { id: "data", name: (call.body as { name: string }).name, scope: "server", position: 0, layout: (call.body as { layout: unknown }).layout, created_at: 1, updated_at: 2 }) },
      "/?panel=data",
      undefined,
      "Data and backups",
    );
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rename panel" }));
    const dialog = await screen.findByRole("dialog", { name: "Rename the panel" });
    const field = within(dialog).getByRole("textbox", { name: "Name" });
    await user.clear(field);
    await user.type(field, "Safe things");
    await user.click(within(dialog).getByRole("button", { name: "Rename panel" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Safe things" })).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("PUT /api/auth/dashboards/data")).toHaveLength(1));
    expect((backend.callsTo("PUT /api/auth/dashboards/data")[0]?.body as { name: string }).name).toBe("Safe things");
  });

  it("goes back, when reset, to the console's panel for what it follows", async () => {
    const { user } = await home({ "GET /api/auth/dashboards": MINE }, "/?panel=data&customise=true", undefined, "Customize “Data and backups”");
    expect(await widgetsOnPage()).toEqual(["Backups", "Applications"]);
    await user.click(screen.getByRole("button", { name: "Reset" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Needs attention" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Customize “Data and backups”" })).toBeInTheDocument();
  });

  it("is deleted after a question, and its person lands on the panel that comes with the console", async () => {
    const { user, backend, location } = await home(
      { "GET /api/auth/dashboards": MINE, "DELETE /api/auth/dashboards/data": () => json(200, { dashboards: [], limits: {} }) },
      "/?panel=data",
      undefined,
      "Data and backups",
    );
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete panel" }));
    const question = await screen.findByRole("alertdialog", { name: "Delete “Data and backups”?" });
    await user.click(within(question).getByRole("button", { name: "Delete panel" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/auth/dashboards/data")).toHaveLength(1));
    await waitFor(() => expect(location().search).toEqual({}));
    expect(await screen.findByRole("heading", { level: 1, name: "This server" })).toBeInTheDocument();
  });
});

describe("Home, a new panel", () => {
  it("is made from its name, what it follows and how it begins, and opens customized", async () => {
    const { user, backend, location } = await home(
      {
        "GET /api/nodes": () => json(200, { items: NODES }),
        "GET /api/auth/dashboards": kept(),
        "PUT /api/auth/dashboards/my-panel": (call) => json(200, { id: "my-panel", name: "My panel", scope: "fleet", position: 0, layout: (call.body as { layout: unknown }).layout, created_at: 1, updated_at: 1 }),
      },
      "/?panel=new",
      undefined,
      "New panel",
    );
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("My panel");
    await user.click(await screen.findByRole("radio", { name: /The whole fleet/ }));
    await user.click(screen.getByRole("radio", { name: "Empty" }));
    await user.click(screen.getByRole("button", { name: "Create and customize" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/auth/dashboards/my-panel")).toHaveLength(1));
    const body = backend.callsTo("PUT /api/auth/dashboards/my-panel")[0]?.body as { scope: string; layout: { widgets: unknown[] } };
    expect(body.scope).toBe("fleet");
    expect(body.layout.widgets).toEqual([]);
    await waitFor(() => expect(location().pathname).toBe("/fleet"));
    expect(location().search).toEqual({ panel: "my-panel", customise: true });
  });

  it("is not made when the account keeps as many as it may: it says so and makes nothing", async () => {
    const full: RouteHandler = () =>
      json(200, {
        dashboards: [{ id: "only", name: "Only", scope: "server", position: 0, layout: document1([placed("apps", "wide")]), created_at: 1, updated_at: 1 }],
        limits: { dashboards: 1, widgets: 40, columns: 12, document_bytes: 32768, settings_bytes: 2048 },
      });
    const { user, backend } = await home({ "GET /api/auth/dashboards": full }, "/?panel=new", undefined, "New panel");
    await waitFor(() => expect(readPanelCache().panels).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Create and customize" }));
    expect(await screen.findByText("An account keeps 1 panels at most. Delete one to make another.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/auth/dashboards/my-panel")).toHaveLength(0);
  });

  it("does not make one with no name: it says so beside the name and puts the keyboard there", async () => {
    const { user } = await home(undefined, "/?panel=new", undefined, "New panel");
    const field = screen.getByRole("textbox", { name: "Name" });
    await user.clear(field);
    await user.click(screen.getByRole("button", { name: "Create and customize" }));
    expect(await screen.findByText("Give the panel a name.")).toBeInTheDocument();
    expect(field).toHaveFocus();
  });

  it("has no accessibility violations", async () => {
    await home(undefined, "/?panel=new", undefined, "New panel");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Home, the fleet's panel", () => {
  const FLEET = {
    nodes: [],
    servers: [],
    refusals: [],
    items: [
      { name: "central-01", node: "central-01", local: true, reachability: "ok", overview: OVERVIEW },
      { name: "web-2", node: "web-2", local: false, reachability: "ok", overview: overviewFixture() },
    ],
  };

  it("draws the panel of the fleet at its address, from the central's answers and not from this server's own", async () => {
    const { backend } = await home(
      {
        "GET /api/nodes": () => json(200, { items: NODES }),
        "GET /api/fleet/summary": () => json(200, FLEET),
        "GET /api/views/apps": () => json(200, { nodes: [], servers: [], refusals: [], items: [{ node: "central-01", domain: "shop.example.com", status: "running" }] }),
        "GET /api/fleet/jobs/active": () => json(200, { nodes: [], servers: [], refusals: [], items: [] }),
        "GET /api/fleet/updates": () => json(200, { nodes: [], servers: [], refusals: [], items: [] }),
      },
      "/fleet",
      undefined,
      "Fleet",
    );
    const names = await widgetsOnPage();
    expect(names).toEqual(expect.arrayContaining(["Needs attention", "Servers of the fleet", "Applications", "System updates", "Work in progress", "Recent activity"]));
    expect(await screen.findByText("The whole fleet: 2 servers")).toBeInTheDocument();
    // The applications are the fleet's answer, not this server's own list: the one read of the Overview is the inbox's, in the frame.
    expect(backend.callsTo("GET /api/apps")).toHaveLength(0);
    expect(backend.callsTo("GET /api/overview").length).toBeLessThanOrEqual(1);
  });
});

describe("The panels, widget by widget", () => {
  it("only draws widgets the catalog has: a stored panel with a type from the future does not break Home", async () => {
    const future: Placed = { id: "from-the-future", type: "from-the-future", size: "small", settings: {} };
    await home({ "GET /api/auth/dashboards": kept({ id: "server", name: "This server", layout: [future, placed("apps", "wide")] }) });
    expect(await widgetsOnPage()).toEqual(["Applications"]);
    expect(WIDGETS.map((widget) => widget.type as string)).not.toContain("from-the-future");
  });
});
