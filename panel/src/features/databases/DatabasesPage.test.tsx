import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { CONTAINER_DATABASE, CONTAINER_ENGINE, DATABASES, DETECTED_DATABASE, ENGINES, POLICY, SERVERS, databaseRoutes } from "./testFixtures";

afterEach(() => {
  vi.unstubAllGlobals();
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
});

async function listAt(path = "/databases", extra: Record<string, RouteHandler> = {}, { rows = true } = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: /^Databases/ });
  // The rows come in a table of their own, with the filters above it, once the list is known.
  if (rows) await screen.findByRole("search", { name: "Filter databases" });
  const table = rows ? await screen.findByRole("region", { name: /^Databases/ }) : document.body;
  return { ...harness, backend, table };
}

/** The list's region as it is now: placeholders while the list is read, the rows after. */
function listRegion(): HTMLElement {
  return screen.getByRole("region", { name: /^Databases/ });
}

/** The row a database is in. */
function rowOf(table: HTMLElement, name: string): HTMLElement {
  const row = within(table).getByText(name).closest("tr");
  if (row === null) throw new Error(`no row for ${name}`);
  return row;
}

/** An application's container, as 4.1 lists it among the servers. */
const CONTAINER_SERVER = {
  ...SERVERS[0],
  key: CONTAINER_ENGINE.name,
  engine: "postgresql",
  flavour: "postgresql",
  display_name: "PostgreSQL",
  version: null,
  runs_in: "app_container",
  app: "proggest.es",
  support: null,
  end_of_life: null,
  databases: 1,
  default: false,
  default_source: null,
  actions: [],
};

describe("the databases list, opened to create one", () => {
  /** The page behind the dialog is inert while it is open, so there is no heading to wait for. */
  function createdAt(path: string) {
    screenWidth(1440);
    fakeBackend(databaseRoutes());
    return renderConsole(path);
  }

  it("opens the New database dialog when the link says so (the palette's create command), once", async () => {
    const { location, user } = createdAt("/databases?new=true");
    const dialog = await screen.findByRole("dialog", { name: "New database" });
    // The link is spent: going back, or reloading, does not ask the same question again.
    await waitFor(() => expect(location().search).toEqual({}));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(dialog).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog", { name: "New database" })).not.toBeInTheDocument();
  });

  it("keeps the filters it was given when it opens the dialog", async () => {
    const { location } = createdAt("/databases?engine=postgresql&new=true");
    await screen.findByRole("dialog", { name: "New database" });
    await waitFor(() => expect(location().search).toEqual({ engine: "postgresql" }));
  });
});

describe("the databases list", () => {
  it("lists each database with the server it runs on, who uses it, its size and whether it is backed up", async () => {
    const { table } = await listAt();
    const row = rowOf(await within(table).findByText("example_production").then(() => table), "example_production");
    expect(within(row).getByText(/PostgreSQL 16/)).toBeInTheDocument();
    // A server of the system says nothing of where it runs: the page is about this machine.
    expect(within(row).queryByText(/web-01/)).not.toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com/database");
    expect(within(row).getByText("Verified")).toBeInTheDocument();
    const shop = rowOf(table, "shop_wp");
    expect(within(shop).getByText(/MySQL 8\.0/)).toBeInTheDocument();
    expect(within(shop).getByText("No backup")).toBeInTheDocument();
    expect(screen.getByText("3 databases")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is two parts of one page: the databases and the database servers they run on", async () => {
    await listAt();
    expect(await screen.findByRole("heading", { level: 2, name: "Your databases" })).toBeInTheDocument();
    const servers = await screen.findByRole("heading", { level: 2, name: "Database servers" });
    const part = servers.closest("section");
    if (part === null) throw new Error("no section");
    // The default of its engine is said, and MySQL says its support is over, in words.
    expect(await within(part).findByRole("heading", { level: 3, name: "PostgreSQL 16" })).toBeInTheDocument();
    expect(within(part).getAllByText("Default")).toHaveLength(3);
    expect(within(part).getByText(/No support since Apr 2026/)).toBeInTheDocument();
    expect(within(part).getByRole("button", { name: "Upgrade version" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has the side column: its two sections and one view per engine, with the databases each holds", async () => {
    // The double bar is the frame's at 1440 px and wider (app/navLayout.ts): the window says it, as the frame's own tests do.
    Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
    const { user, location } = await listAt();
    const column = await screen.findByRole("navigation", { name: /^Databases/ });
    expect(within(column).getByRole("link", { name: /Your databases/ })).toHaveAttribute("aria-current", "page");
    expect(within(column).getByRole("link", { name: /Database servers/ })).toHaveAttribute("href", "/databases/servers");
    const postgres = await within(column).findByRole("link", { name: /PostgreSQL/ });
    expect(postgres).toHaveTextContent("2");
    // Redis holds none and says it: "0", not nothing.
    expect(within(column).getByRole("link", { name: /Redis/ })).toHaveTextContent("0");
    await user.click(postgres);
    await waitFor(() => expect(location().search).toEqual({ engine: "postgresql" }));
    await waitFor(() => expect(postgres).toHaveAttribute("aria-current", "page"));
    expect(within(column).getByRole("link", { name: /Your databases/ })).not.toHaveAttribute("aria-current");
    // It narrows the databases and the servers both.
    const table = listRegion();
    expect(within(table).queryByText("shop_wp")).not.toBeInTheDocument();
    expect(within(table).getByText("example_production")).toBeInTheDocument();
    const part = screen.getByRole("heading", { level: 2, name: "Database servers" }).closest("section");
    if (part === null) throw new Error("no section");
    expect(within(part).queryByRole("heading", { level: 3, name: /MySQL/ })).not.toBeInTheDocument();
  });

  it("counts the databases with no schedule in a quick filter, not in a banner, and shows them through the URL", async () => {
    const { user, location, table } = await listAt();
    // Each row already says "No backup": nothing above the table repeats it.
    expect(await screen.findByRole("radio", { name: "No schedule (2)" })).toBeInTheDocument();
    expect(screen.queryByText(/have no backup schedule/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: "No schedule (2)" }));
    await waitFor(() => expect(location().search).toEqual({ backups: "attention" }));
    expect(within(table).queryByText("example_production")).not.toBeInTheDocument();
    expect(within(table).getByText("shop_wp")).toBeInTheDocument();
  });

  it("says a port open to the network before anything else", async () => {
    await listAt("/databases", {
      "GET /api/databases/exposure": () =>
        json(200, { exposed: [{ engine: "postgresql", port: 5432, address: "0.0.0.0", process: "postgres", source: "engine", container: null, image: null, advice: "Set listen_addresses." }] }),
    });
    expect(await screen.findByText("A database port is open to the network")).toBeInTheDocument();
    expect(screen.queryByText(/have no backup schedule/)).not.toBeInTheDocument();
  });

  it("says an engine it cannot read before anything else, with the engine's words, and stores an account for it", async () => {
    const denied = "ERROR 1045 (28000): Access denied for user 'root'@'localhost' (using password: NO)";
    const { user, backend } = await listAt(
      "/databases",
      {
        "GET /api/databases/databases": () =>
          json(200, {
            databases: [],
            total: 0,
            problems: [
              {
                engine: "mysql",
                display_name: "MySQL/MariaDB",
                message: "MySQL/MariaDB does not let Noust sign in, so its databases cannot be listed",
                hint: "Store the account Noust signs in with.",
                output: denied,
                access: true,
              },
            ],
          }),
        "PUT /api/databases/engines/mysql/credentials": () => json(200, { success: true, message: "ok" }),
      },
      { rows: false },
    );
    expect(await screen.findByText("Noust cannot read MySQL/MariaDB")).toBeInTheDocument();
    expect(screen.getByText(denied)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Store the account" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("User"), "root");
    await user.type(within(dialog).getByLabelText("Password"), "s3cret");
    await user.click(within(dialog).getByRole("button", { name: "Try and save" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/databases/engines/mysql/credentials")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/databases/engines/mysql/credentials")[0]?.body).toEqual({ user: "root", password: "s3cret" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("creates a database on the engine's default server and opens it", async () => {
    const { user, backend, location } = await listAt("/databases", {
      "POST /api/databases/databases": () => json(200, { name: "acme_shop", engine: "postgresql", tables: 0, tracked: true, missing: false, apps: [] }),
      "GET /api/databases/databases/postgresql/acme_shop/overview": () => json(404, { detail: "nope", error: "not_found" }),
    });
    await user.click(screen.getByRole("button", { name: "New database" }));
    const dialog = await screen.findByRole("dialog", { name: "New database" });
    await user.click(within(dialog).getByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL" }));
    // One server of the engine: nobody is asked where it runs.
    expect(within(dialog).queryByRole("combobox", { name: "Database server" })).not.toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Name"), "acme_shop");
    await user.click(within(dialog).getByRole("button", { name: "Create database" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/databases")[0]?.body).toEqual({ engine: "postgresql", name: "acme_shop", owner: null, encoding: null, app: null });
    await waitFor(() => expect(location().pathname).toBe("/databases/postgresql/acme_shop"));
  });

  it("asks which server only when the engine has more than one, with the default chosen already", async () => {
    const second = { ...SERVERS[0], key: "postgresql@pg17", version: "17", runs_in: "noust_container", name: "pg17", default: false, default_source: null, databases: 0, actions: ["stop", "upgrade", "remove"] };
    const { user, backend } = await listAt("/databases", {
      "GET /api/databases/servers": () => json(200, { servers: [...SERVERS, second], problems: [] }),
      "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, { ...ENGINES[0], name: "postgresql@pg17", kind: "container", managed: "pg17", pinned_version: "17" }] }),
      "POST /api/databases/databases": () => json(200, { name: "acme_shop", engine: "postgresql@pg17", tables: 0, tracked: true, missing: false, apps: [] }),
      "GET /api/databases/databases/postgresql@pg17/acme_shop/overview": () => json(404, { detail: "nope", error: "not_found" }),
    });
    await user.click(screen.getByRole("button", { name: "New database" }));
    const dialog = await screen.findByRole("dialog", { name: "New database" });
    await user.click(within(dialog).getByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL" }));
    const server = await within(dialog).findByRole("combobox", { name: "Database server" });
    expect(server).toHaveTextContent("PostgreSQL 16 (default)");
    await user.click(server);
    await user.click(await screen.findByRole("option", { name: "PostgreSQL 17" }));
    await user.type(within(dialog).getByLabelText("Name"), "acme_shop");
    await user.click(within(dialog).getByRole("button", { name: "Create database" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/databases")[0]?.body).toMatchObject({ engine: "postgresql@pg17", name: "acme_shop" });
  });

  it("says where a database in an application's container runs, and keeps an address of before as a filter", async () => {
    const { table } = await listAt("/databases?engine=postgresql%40proggest.postgres", {
      "GET /api/databases/servers": () => json(200, { servers: [...SERVERS, CONTAINER_SERVER], problems: [] }),
      "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, CONTAINER_ENGINE] }),
      "GET /api/databases/databases": () => json(200, { databases: [...DATABASES, CONTAINER_DATABASE], total: DATABASES.length + 1 }),
    });
    const row = rowOf(table, "proggest");
    expect(within(row).getByText(/proggest\.es/)).toBeInTheDocument();
    expect(within(table).queryByText("example_production")).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("marks an application found using a database as detected, and records the link without touching it", async () => {
    let recorded = false;
    const { user, backend, table } = await listAt("/databases", {
      "GET /api/databases/databases": () =>
        json(200, {
          databases: [DATABASES[0], recorded ? { ...DETECTED_DATABASE, apps: ["docs.example.com"], detected_apps: [] } : DETECTED_DATABASE, DATABASES[2]],
          total: 3,
        }),
      "POST /api/databases/databases/postgresql/example_staging/links/detected": () => {
        recorded = true;
        return json(200, { domain: "docs.example.com", engine: "postgresql", database: "example_staging", username: "example_staging", env_var: "DATABASE_URL" });
      },
    });
    const row = rowOf(table, await within(table).findByText("example_staging").then(() => "example_staging"));
    expect(within(row).getByRole("link", { name: "docs.example.com" })).toHaveAttribute("href", "/apps/docs.example.com");
    expect(within(row).getByText("Detected")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Actions for example_staging" }));
    const item = await screen.findByRole("menuitem", { name: /^Record that docs.example.com uses it/ });
    expect(item).toHaveAccessibleName("Record that docs.example.com uses it Nothing in the application changes: its environment already names this database.");
    await expectNoAxeViolations(document.body);
    await user.click(item);
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases/postgresql/example_staging/links/detected")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/databases/postgresql/example_staging/links/detected")[0]?.body).toEqual({ app: "docs.example.com" });
    // The list is read again: the application is now a recorded link to its Database tab.
    await waitFor(() => expect(within(table).getByRole("link", { name: "docs.example.com" })).toHaveAttribute("href", "/apps/docs.example.com/database"));
    expect(within(table).queryByText("Detected")).not.toBeInTheDocument();
  });

  it("counts only the ports really open: one the firewall closes is not an alarm", async () => {
    await listAt("/databases", {
      "GET /api/databases/exposure": () =>
        json(200, {
          exposed: [],
          firewalled: [{ engine: "postgresql", port: 5433, address: "0.0.0.0", source: "docker", container: "proggest-postgres-1", firewalled: true, closed_by: "DOCKER-USER" }],
        }),
    });
    expect(await screen.findByRole("radio", { name: "No schedule (2)" })).toBeInTheDocument();
    expect(screen.queryByText(/open to the network/)).not.toBeInTheDocument();
  });

  it("draws placeholder rows, not a finished-looking page, while the list is read", async () => {
    // Opening the page used to leave the header over a blank area: nothing said anything was coming.
    let answer: (response: Response) => void = () => undefined;
    await listAt(
      "/databases",
      { "GET /api/databases/databases": () => new Promise<Response>((resolve) => (answer = resolve)) },
      { rows: false },
    );
    // Placeholder rows from the first frame, busy, in the list's place.
    const placeholder = await screen.findByRole("region", { name: /^Databases/ });
    expect(placeholder.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(placeholder.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    // Nothing is claimed about a list that is not known yet, and nothing is drawn above the
    // placeholders that the notice would push down when it came.
    expect(screen.queryByRole("search", { name: "Filter databases" })).not.toBeInTheDocument();
    expect(screen.queryByText("No databases yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /No schedule/ })).not.toBeInTheDocument();
    expect(screen.queryByText("0 databases")).not.toBeInTheDocument();

    answer(json(200, { databases: DATABASES, total: DATABASES.length }));
    // The filters and the rows arrive together, and nothing is busy any more.
    expect(await screen.findByRole("search", { name: "Filter databases" })).toBeInTheDocument();
    expect(await within(listRegion()).findByText("example_production")).toBeInTheDocument();
    expect(listRegion().querySelector("table")).not.toHaveAttribute("aria-busy");
  });

  it("holds the rows behind placeholders until the notice above them is known", async () => {
    let answer: (response: Response) => void = () => undefined;
    await listAt(
      "/databases",
      { "GET /api/databases/exposure": () => new Promise<Response>((resolve) => (answer = resolve)) },
      { rows: false },
    );
    const placeholder = await screen.findByRole("region", { name: /^Databases/ });
    expect(placeholder.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(within(placeholder).queryByText("example_production")).not.toBeInTheDocument();
    answer(json(200, { exposed: [], firewalled: [] }));
    expect(await screen.findByText("example_production")).toBeInTheDocument();
    expect(listRegion().querySelector("table")).not.toHaveAttribute("aria-busy");
  });

  it("shows the rows after a second even when the open-port check has not answered", async () => {
    // The check reads the firewall and can take seconds: the rows the operator came for do not wait for it.
    await listAt("/databases", { "GET /api/databases/exposure": () => new Promise<Response>(() => undefined) }, { rows: false });
    await screen.findByRole("region", { name: /^Databases/ });
    expect(screen.queryByText("example_production")).not.toBeInTheDocument();
    expect(await screen.findByText("example_production", undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(listRegion().querySelector("table")).not.toHaveAttribute("aria-busy");
  });

  it("offers the first database when there is none", async () => {
    await listAt("/databases", { "GET /api/databases/databases": () => json(200, { databases: [], total: 0 }) }, { rows: false });
    expect(await screen.findByRole("heading", { level: 2, name: "No databases yet" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /^Databases/ })).toBeNull();
    // The servers are still there: this is where they run.
    expect(await screen.findByRole("heading", { level: 3, name: "PostgreSQL 16" })).toBeInTheDocument();
  });
});

describe("what a row offers at its end", () => {
  it("adds a database Noust does not hold to Noust, and the row changes: it is not a kind of database", async () => {
    let added = false;
    const { user, backend, table } = await listAt("/databases", {
      "GET /api/databases/databases": () =>
        json(200, {
          databases: DATABASES.map((database) => (database.name === "example_staging" && added ? { ...database, tracked: true, noust_state: "added", actions: [] } : database)),
          total: DATABASES.length,
        }),
      "POST /api/databases/databases/postgresql/example_staging/adopt": () => {
        added = true;
        return json(200, { ...DATABASES[1], tracked: true, noust_state: "added", actions: [] });
      },
    });
    const row = rowOf(table, "example_staging");
    // No "not tracked" mark on the name, and no other kind of database: the action is the whole difference.
    expect(within(row).queryByText("Not tracked")).not.toBeInTheDocument();
    // Where its backup would be: a database Noust does not hold has none to warn about, and the way to get one is the button.
    expect(within(row).queryByText("No backup")).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Add to Noust" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases/postgresql/example_staging/adopt")).toHaveLength(1));
    await waitFor(() => expect(within(rowOf(listRegion(), "example_staging")).queryByRole("button", { name: "Add to Noust" })).not.toBeInTheDocument());
    // Said where the person looked: the row changed, and nothing else was announced.
    expect(screen.queryByRole("status", { name: /added/i })).not.toBeInTheDocument();
  });

  it("keeps the backup state of a database Noust does not hold when a schedule for every database of its engine covers it", async () => {
    const everyPostgres = { ...POLICY, database: "*", scope: "all" as const, last_dump: null };
    const { table } = await listAt("/databases", {
      "GET /api/databases/backup-policies": () => json(200, { policies: [POLICY, everyPostgres], total: 2, unprotected: [] }),
    });
    // It is backed up, whether or not it is added: the way to add it is for the database nothing covers.
    const row = rowOf(table, "example_staging");
    expect(await within(row).findByText("Backed up")).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Add to Noust" })).not.toBeInTheDocument();
  });

  it("offers no button to add on a server that cannot do it one by one, and keeps the bulk action in the page's menu", async () => {
    // What a server of 4.0 says of a database: no `noust_state` and no `actions`.
    const older = DATABASES.map((database) => {
      const rest: Record<string, unknown> = { ...database };
      delete rest["actions"];
      delete rest["noust_state"];
      return rest;
    });
    const { user, table } = await listAt("/databases", { "GET /api/databases/databases": () => json(200, { databases: older, total: older.length }) });
    expect(within(rowOf(table, "example_staging")).queryByRole("button", { name: "Add to Noust" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: /Add 1 database to Noust/ })).toBeInTheDocument();
  });

  it("copies the connection an application uses, in one press, once the password is shown in sudo mode", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    const { user, backend, table } = await listAt("/databases", {
      "GET /api/databases/databases/postgresql/example_production/connect": () =>
        json(200, {
          engine: "postgresql",
          database: "example_production",
          username: "example_production",
          password_known: true,
          port: 5432,
          listen: null,
          apps: [{ domain: "shop.example.com", engine: "postgresql", database: "example_production", username: "example_production", env_var: "DATABASE_URL", extra_vars: false, url: "postgresql://example_production:********@localhost:5432/example_production", exists: true }],
          tunnel: { server: "<server>", ssh_port: 22, ssh_user: "root", local_port: 15432, remote_port: 5432, command: "ssh", url: "postgresql://x", clients: {} },
          exposed: [],
        }),
      "POST /api/apps/shop.example.com/databases/postgresql/example_production/url": () =>
        json(200, { domain: "shop.example.com", url: "postgresql://example_production:Pr0d-demo@localhost:5432/example_production" }),
    });
    // After the console is drawn: its own user-event installs a clipboard of its own, and this one replaces it.
    vi.stubGlobal("isSecureContext", true);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await user.click(within(rowOf(table, "example_production")).getByRole("button", { name: "Copy the connection of example_production" }));
    await waitFor(() => expect(backend.calls.map((call) => `${call.method} ${call.path}`)).toContain("POST /api/apps/shop.example.com/databases/postgresql/example_production/url"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("postgresql://example_production:Pr0d-demo@localhost:5432/example_production"));
    expect(backend.callsTo("POST /api/apps/shop.example.com/databases/postgresql/example_production/url")).toHaveLength(1);
  });

  it("opens the Connect tab for a database no application uses, which is where it says how to reach it", async () => {
    const { user, location, table } = await listAt("/databases", {
      "GET /api/databases/databases/mysql/shop_wp/overview": () => json(404, { detail: "nope", error: "not_found" }),
    });
    const row = rowOf(table, "shop_wp");
    await user.click(within(row).getByRole("button", { name: "Connection of shop_wp" }));
    await waitFor(() => expect(location().pathname).toBe("/databases/mysql/shop_wp/connect"));
  });
});
