import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { DATABASES, OVERVIEW, SERVERS, databaseRoutes } from "./testFixtures";

const BASE = "/api/databases/databases/postgresql/example_production";
const JOB = { id: "drop-1", type: "database", name: "Drop example_production", description: "", status: "pending", progress: 0, total_steps: 100, current_step: "", created_at: "2026-09-29T10:00:00", logs: [], metadata: { engine: "postgresql", database: "example_production" } };

/** The same machine with PostgreSQL stopped: what the list of engines says of it, and nothing the engine itself could answer. */
const STOPPED = SERVERS.map((server) => (server.key === "postgresql" ? { ...server, running: false, state: "stopped", actions: ["start"] } : server));
const STOPPED_ROUTES: Record<string, RouteHandler> = {
  "GET /api/databases/servers": () => json(200, { servers: STOPPED, problems: [] }),
  "GET /api/databases/databases": () => json(200, { databases: DATABASES.map((item) => (item.engine === "postgresql" ? { ...item, size: null, unverified: true } : item)), total: DATABASES.length }),
  [`GET ${BASE}/overview`]: () => json(500, { detail: "PostgreSQL is not running", error: "databaseengineerror" }),
};

async function pageAt(path: string, extra: Record<string, RouteHandler> = {}, title = "example_production") {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: title });
  return { ...harness, backend };
}

describe("a database's page and its column", () => {
  it("heads its overview with its name and state and puts its sections in the column, in three groups", async () => {
    await pageAt("/databases/postgresql/example_production");
    expect((await screen.findAllByText("Running")).length).toBeGreaterThan(0);
    const column = await screen.findByRole("navigation", { name: "Database sections" });
    expect(within(column).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Data", "Query", "Metrics", "Access", "Connect", "Backups", "Settings", "Delete"]);
    for (const heading of ["View", "Manage", "Danger"]) expect(within(column).getByText(heading)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back up now" })).toHaveAttribute("data-variant", "primary");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("gives a key-value store Keys and no Query, as its engine says it can", async () => {
    await pageAt("/databases/postgresql/example_production", {
      [`GET ${BASE}/overview`]: () => json(200, { ...OVERVIEW, capabilities: ["dump", "keys", "metrics", "users", "profiles"] }),
    });
    const column = await screen.findByRole("navigation", { name: "Database sections" });
    await waitFor(() => expect(within(column).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Keys", "Metrics", "Access", "Connect", "Backups", "Settings", "Delete"]));
    expect(within(column).queryByRole("link", { name: "Query" })).toBeNull();
  });

  it("does not offer a query typed into the address for an engine that has none: it says so and leads back", async () => {
    const { backend } = await pageAt("/databases/postgresql/example_production/query", {
      [`GET ${BASE}/overview`]: () => json(200, { ...OVERVIEW, capabilities: ["dump", "keys", "metrics"] }),
    }, "Query");
    expect(await screen.findByText("Query is not something this engine has")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the overview" })).toHaveAttribute("href", "/databases/postgresql/example_production");
    expect(screen.queryByRole("textbox", { name: /Statement/ })).toBeNull();
    expect(backend.callsTo("POST /api/databases/query")).toHaveLength(0);
  });

  it("says a stopped engine is stopped, offers to start it first, and asks it nothing", async () => {
    const { user, backend } = await pageAt("/databases/postgresql/example_production", {
      ...STOPPED_ROUTES,
      "POST /api/databases/engines/postgresql/start": () => json(200, { success: true, message: "started" }),
    });
    const slab = await screen.findByLabelText("Engine stopped");
    expect(within(slab).getByText(/PostgreSQL 16 \(on the system\) is stopped/)).toBeInTheDocument();
    // What it knows is still said; nothing is a skeleton that does not end, and the engine was never asked.
    expect(screen.getAllByText("Stopped").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Back up now" })).toBeNull();
    expect(backend.callsTo(`GET ${BASE}/overview`)).toHaveLength(0);
    await user.click(within(slab).getByRole("button", { name: "Start the engine" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/engines/postgresql/start")).toHaveLength(1));
  });

  it("draws the stopped engine in place of a section that needs it, and the section is not asked", async () => {
    const { backend } = await pageAt("/databases/postgresql/example_production/query", STOPPED_ROUTES, "Query");
    expect(await screen.findByLabelText("Engine stopped")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /Statement/ })).toBeNull();
    expect(backend.callsTo("POST /api/databases/query")).toHaveLength(0);
    // The sections that do not need the engine are still there: its backups and its settings.
    expect(within(await screen.findByRole("navigation", { name: "Database sections" })).getByRole("link", { name: "Backups" })).toBeInTheDocument();
  });

  it("ends in a banner with Try again, and not in a skeleton, when the engine's answer is an error that is not a 404", async () => {
    const { user, backend } = await pageAt("/databases/postgresql/example_production", {
      [`GET ${BASE}/overview`]: () => json(500, { detail: "could not read it", error: "internal" }),
    });
    expect(await screen.findByText("Could not load example_production")).toBeInTheDocument();
    expect(screen.getAllByText("Not checked").length).toBeGreaterThan(0);
    const before = backend.callsTo(`GET ${BASE}/overview`).length;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(backend.callsTo(`GET ${BASE}/overview`).length).toBeGreaterThan(before));
  });

  it("says a database that does not exist is not there, and leads to the list", async () => {
    screenWidth(1440);
    fakeBackend(databaseRoutes({ [`GET ${BASE}/overview`]: () => json(404, { detail: "no such database", error: "databasenotfounderror" }) }));
    renderConsole("/databases/postgresql/example_production");
    expect(await screen.findByText("No such database")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All databases" })).toHaveAttribute("href", "/databases");
  });

  it("opens the Access of a database from the address of its Users", async () => {
    screenWidth(1440);
    fakeBackend(databaseRoutes({ [`GET ${BASE}/access`]: () => json(200, { access: OVERVIEW.access }) }));
    const { location } = renderConsole("/databases/postgresql/example_production/users");
    await waitFor(() => expect(location().pathname).toBe("/databases/postgresql/example_production/access"));
    expect(await screen.findByRole("heading", { level: 1, name: "Access" })).toBeInTheDocument();
  });
});

describe("what a role that may not change a database is shown", () => {
  const VIEWER = { ...SESSION, role: "viewer", permissions: ["self", "databases.read", "backups.read"] };

  it("keeps the page's own action, disabled, with its reason one press away, and draws no action that would be refused", async () => {
    screenWidth(1440);
    fakeBackend(databaseRoutes({ ...signedInRoutes(VIEWER) }));
    const { user } = renderConsole("/databases/postgresql/example_production/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    expect(await screen.findByText(/can see this page and not change it/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Give it to/ })).toBeNull();
    await user.click(within(screen.getByRole("navigation", { name: "Database sections" })).getByRole("link", { name: "Overview" }));
    await screen.findByRole("heading", { level: 1, name: "example_production" });
    const action = (await screen.findAllByRole("button", { name: "Back up now" }))[0];
    expect(action).toBeDisabled();
  });
});

describe("deleting a database", () => {
  it("drops it only once its name is typed, telling first what it takes from its application, as a job it follows", async () => {
    const { user, backend } = await pageAt("/databases/postgresql/example_production/delete", {
      "DELETE /api/databases/databases/postgresql/example_production": () => json(202, { job_id: JOB.id, status: "pending", message: "Dropping", job: JOB }),
      "GET /api/jobs/drop-1": () => json(200, JOB),
    }, "Delete the database");
    // A fact, not a choice: the server refuses to drop what an application uses unless it is taken from it.
    expect(await screen.findByText(/It is taken away from shop\.example\.com, which restart without it/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Drop example_production" });
    expect(within(dialog).getByText(/A last dump is taken first/)).toBeInTheDocument();
    const drop = within(dialog).getByRole("button", { name: "Drop database" });
    expect(drop).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "example_production");
    await user.click(drop);
    await waitFor(() => expect(backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")).toHaveLength(1));
    const call = backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")[0];
    expect(Object.fromEntries(call?.search ?? [])).toEqual({ force: "false", keep_backup: "true", unlink: "true" });
  });

  it("takes no last dump when the person says so, and says there is no way back", async () => {
    const { user, backend } = await pageAt("/databases/postgresql/example_production/delete", {
      "DELETE /api/databases/databases/postgresql/example_production": () => json(202, { job_id: JOB.id, status: "pending", message: "Dropping", job: JOB }),
      "GET /api/jobs/drop-1": () => json(200, JOB),
    }, "Delete the database");
    await user.click(await screen.findByRole("checkbox", { name: "Take a last dump first" }));
    expect(screen.getByText("Without it there is no way back.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Drop example_production" });
    expect(within(dialog).getByText(/No dump is taken: there is no way back/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox"), "example_production");
    await user.click(within(dialog).getByRole("button", { name: "Drop database" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")).toHaveLength(1));
    expect(Object.fromEntries(backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")[0]?.search ?? [])).toMatchObject({ keep_backup: "false" });
  });

  it("forgets, and does not delete, a database the engine no longer has, after one question", async () => {
    const gone = { ...DATABASES[0], missing: true, size: null };
    const { user, backend } = await pageAt("/databases/postgresql/example_production/delete", {
      [`GET ${BASE}/overview`]: () => json(200, { ...OVERVIEW, database: gone }),
      "GET /api/databases/databases": () => json(200, { databases: [gone, ...DATABASES.slice(1)], total: DATABASES.length }),
      "POST /api/databases/databases/postgresql/example_production/forget": () => json(200, { forgotten: true }),
    }, "Forget the database");
    expect(await screen.findByText(/PostgreSQL no longer has it/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Forget it" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Forget example_production?" });
    await user.click(within(dialog).getByRole("button", { name: "Forget it" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases/postgresql/example_production/forget")).toHaveLength(1));
  });
});
