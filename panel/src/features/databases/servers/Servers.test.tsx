import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, onNode, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { CATALOG as CATALOG_OF_ENGINES, CONTAINER_ENGINE, ENGINES, SERVERS, databaseRoutes } from "../testFixtures";
import { ACCEPTED, BUDGET, CATALOG, INSTANCE, INSTANCE_SERVER, JOB } from "../instances/instanceFixtures";

/**
 * The database servers page: the system's engines and one container of Noust's, with the instance's own routes answered.
 * The system's PostgreSQL is 14 here so that the two are not both "PostgreSQL 16": a menu is named by the server it belongs to.
 */
const SYSTEM_SERVERS = [{ ...SERVERS[0], version: "14.13" }, ...SERVERS.slice(1)];

async function serversAt(extra: Record<string, RouteHandler> = {}, servers: readonly unknown[] = [...SYSTEM_SERVERS, INSTANCE_SERVER]) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/databases/servers": () => json(200, { servers, problems: [] }),
      "GET /api/databases/instances": () => json(200, { instances: [INSTANCE], budget: BUDGET }),
      "GET /api/databases/instances/catalog": () => json(200, CATALOG),
      "GET /api/jobs/j1": () => json(200, JOB),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/servers");
  await screen.findByRole("heading", { level: 1, name: "Database servers" });
  const list = await screen.findByRole("list", { name: "Database servers" });
  return { ...harness, backend, list };
}

/** The slab of a server: the item of the list whose heading is its name. */
function slabOf(list: HTMLElement, title: string, index = 0): HTMLElement {
  const slab = within(list).getAllByRole("heading", { level: 2, name: title })[index]?.closest("li");
  if (slab === null || slab === undefined) throw new Error(`no slab for ${title}`);
  return slab;
}

async function menuOf(user: ReturnType<typeof renderConsole>["user"], name: string) {
  await user.click(await screen.findByRole("button", { name: `Actions for ${name}` }));
}

describe("the database servers", () => {
  it("are slabs of their own: the engine and its release, its default, its support, where it runs and how many databases it holds", async () => {
    const { list } = await serversAt();
    const postgres = slabOf(list, "PostgreSQL 14");
    expect(within(postgres).getByText("Default")).toBeInTheDocument();
    expect(within(postgres).getByText("Support until Nov 2028")).toBeInTheDocument();
    expect(within(postgres).getByText("web-01, on the system")).toBeInTheDocument();
    expect(within(postgres).getByText("2 databases")).toBeInTheDocument();
    // A container of Noust's says so, and is not the default of its engine.
    const container = slabOf(list, "PostgreSQL 16");
    expect(within(container).getByText("web-01, in a Noust container")).toBeInTheDocument();
    expect(within(container).queryByText("Default")).not.toBeInTheDocument();
    expect(within(container).getByText("No databases")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("tint the one whose support ended, say so in words, and offer to go up a version", async () => {
    const { list } = await serversAt();
    const mysql = slabOf(list, "MySQL 8.0");
    expect(mysql).toHaveAttribute("data-tone", "failed");
    expect(within(mysql).getByText("No support since Apr 2026")).toBeInTheDocument();
    expect(within(mysql).getByRole("button", { name: "Upgrade version" })).toBeInTheDocument();
    // A server that is well is plain stone and has nothing to upgrade.
    const postgres = slabOf(list, "PostgreSQL 14");
    expect(postgres).not.toHaveAttribute("data-tone");
    expect(within(postgres).queryByRole("button", { name: "Upgrade version" })).not.toBeInTheDocument();
  });

  it("say that one is stopped, with its state, and keep an engine's databases out of what they count", async () => {
    const { list } = await serversAt();
    const redis = slabOf(list, "Redis 7.0");
    expect(within(redis).getByText("Stopped")).toBeInTheDocument();
    expect(within(redis).queryByText(/databases?$/)).not.toBeInTheDocument();
  });

  it("say which flavour the default is of when MySQL and MariaDB are both there", async () => {
    const mariadb = { ...SERVERS[1], key: "mysql@maria", flavour: "mariadb", display_name: "MariaDB", version: "11.4.2", runs_in: "noust_container", name: "maria", support: "supported", end_of_life: "2029-05-01", actions: ["stop", "set_default", "remove"] };
    const { list } = await serversAt({}, [...SYSTEM_SERVERS, mariadb]);
    expect(within(slabOf(list, "MariaDB 11.4")).getByText("Default for MariaDB")).toBeInTheDocument();
    expect(within(slabOf(list, "MySQL 8.0")).getByText("Default for MySQL")).toBeInTheDocument();
    // PostgreSQL has one flavour: a bare Default.
    expect(within(slabOf(list, "PostgreSQL 14")).getByText("Default")).toBeInTheDocument();
  });

  it("name an application's own container by its stack, which is listed and offers nothing but its logs", async () => {
    const stack = { ...SERVERS[0], key: CONTAINER_ENGINE.name, version: null, runs_in: "app_container", app: "proggest.es", support: null, end_of_life: null, databases: 1, default: false, actions: [] };
    const { list, user } = await serversAt({}, [...SYSTEM_SERVERS, stack]);
    const slab = within(list).getByRole("heading", { level: 2, name: "PostgreSQL" }).closest("li");
    if (slab === null) throw new Error("no slab");
    expect(within(slab).getByRole("link", { name: "proggest.es" })).toHaveAttribute("href", "/apps/proggest.es");
    await menuOf(user, "PostgreSQL");
    expect(await screen.findByRole("menuitem", { name: "Logs" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Set as default" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Stop" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Remove|Uninstall/ })).not.toBeInTheDocument();
  });

  it("make another one the default of its engine, and the default moves to it", async () => {
    let chosen = false;
    const servers = () => [{ ...SYSTEM_SERVERS[0], default: !chosen }, ...SYSTEM_SERVERS.slice(1), { ...INSTANCE_SERVER, default: chosen, default_source: chosen ? "chosen" : null, actions: chosen ? ["stop", "clear_default"] : ["stop", "set_default", "remove"] }];
    const { user, backend, list } = await serversAt({
      "GET /api/databases/servers": () => json(200, { servers: servers(), problems: [] }),
      "PUT /api/databases/servers/postgresql%40pg16/default": () => {
        chosen = true;
        return json(200, servers()[2]);
      },
    });
    await menuOf(user, "PostgreSQL 16");
    const item = await screen.findByRole("menuitem", { name: /^Set as default/ });
    expect(item).toHaveAccessibleName("Set as default New databases of this engine are created here unless you choose another server.");
    await user.click(item);
    await waitFor(() => expect(backend.callsTo("PUT /api/databases/servers/postgresql%40pg16/default")).toHaveLength(1));
    // The row changes where the person looked: no toast says it.
    await waitFor(() => expect(within(slabOf(list, "PostgreSQL 16")).getByText("Default")).toBeInTheDocument());
    expect(within(slabOf(list, "PostgreSQL 14")).queryByText("Default")).not.toBeInTheDocument();
  });

  it("start and stop a server of the system's own, and say why when it cannot", async () => {
    const { user, backend } = await serversAt({ "POST /api/databases/engines/redis/start": () => problem(500, "databaseerror", "redis-server did not start", { hint: "Look at its logs." }) });
    await menuOf(user, "Redis 7.0");
    await user.click(await screen.findByRole("menuitem", { name: "Start" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/engines/redis/start")).toHaveLength(1));
    expect(await screen.findByText("redis-server did not start")).toBeInTheDocument();
  });

  it("removes a container of Noust's after its name is typed, and says its data goes", async () => {
    const { user, backend } = await serversAt({ "DELETE /api/databases/instances/pg16": () => json(202, ACCEPTED) });
    await menuOf(user, "PostgreSQL 16");
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove pg16?" });
    expect(within(dialog).getByText(/deleted for good/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox"), "pg16");
    await user.click(within(dialog).getByRole("button", { name: "Remove the instance" }));
    await waitFor(() => expect(backend.callsTo("DELETE /api/databases/instances/pg16")).toHaveLength(1));
    expect(backend.callsTo("DELETE /api/databases/instances/pg16")[0]?.search.get("confirm")).toBe("true");
  });

  it("keep at most the entries a menu may have, the destructive one last after a separator", async () => {
    const { user } = await serversAt();
    await menuOf(user, "PostgreSQL 16");
    const items = within(await screen.findByRole("menu")).getAllByRole("menuitem");
    expect(items.length).toBeLessThanOrEqual(9);
    expect(items.at(-1)).toHaveTextContent("Remove…");
  });
});

describe("another version", () => {
  async function open(extra: Record<string, RouteHandler> = {}) {
    const found = await serversAt({ "POST /api/databases/instances": () => json(202, ACCEPTED), ...extra });
    await found.user.click(screen.getByRole("button", { name: "Another version" }));
    const dialog = await screen.findByRole("dialog", { name: "Another version" });
    return { ...found, dialog };
  }

  async function chooseEngine(user: ReturnType<typeof renderConsole>["user"], dialog: HTMLElement, name: string) {
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name }));
  }

  it("makes a server of the newest version of an engine with a name and a memory limit chosen for you, and follows the job", async () => {
    const { user, backend, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    // Nothing else is asked: the version is the newest, and the name and the memory are the catalog's.
    expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveTextContent("18 (recommended): Support until Nov 2030");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances")[0]?.body).toEqual({ name: "postgresql-18", engine: "postgresql", version: "18", accept_untested: false, unsupported_ok: false });
    expect(await screen.findByText("Creating postgresql-18")).toBeInTheDocument();
  });

  it("says where each version stands in its project's support, beside its number", async () => {
    const { user, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("combobox", { name: "Version" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual([
      "18 (recommended): Support until Nov 2030",
      "17: Support until Nov 2029",
      "16: Support until Nov 2028",
      "15: Support until Nov 2027",
      expect.stringMatching(/^14: Support until Dec 2099, \d+ months? left$|^14: Support until Dec 2099, \d+ months left$/),
      "12: No support since Nov 2024",
    ]);
  });

  it("makes a version past its support only after the person says they know it gets no security fixes", async () => {
    const { user, backend, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("combobox", { name: "Version" }));
    await user.click(await screen.findByRole("option", { name: /^12:/ }));
    expect(await within(dialog).findByText("This version gets no security fixes")).toBeInTheDocument();
    expect(within(dialog).getByText(/lost its support on Nov 2024/)).toBeInTheDocument();
    // The button is not disabled: pressing it says what is missing, where it is missing.
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    expect(await within(dialog).findByText("Say you know it gets no security fixes, or choose another version.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(0);
    await user.click(within(dialog).getByRole("checkbox", { name: "I know it gets no security fixes" }));
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances")[0]?.body).toMatchObject({ version: "12", unsupported_ok: true });
  });

  it("opens on the engine of the server whose menu offered it", async () => {
    const { user } = await serversAt({ "POST /api/databases/instances": () => json(202, ACCEPTED) });
    await menuOf(user, "PostgreSQL 14");
    await user.click(await screen.findByRole("menuitem", { name: "Another version of PostgreSQL" }));
    const dialog = await screen.findByRole("dialog", { name: "Another version" });
    expect(await within(dialog).findByRole("combobox", { name: "Engine" })).toHaveTextContent("PostgreSQL");
  });

  it("offers the images of an engine with an extension, and sends the one chosen", async () => {
    const { user, backend, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.click(within(dialog).getByRole("combobox", { name: /^Image/ }));
    await user.click(await screen.findByRole("option", { name: /PostgreSQL with pgvector/ }));
    expect(within(dialog).getByText("Vector similarity search: the vector type and its indexes.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/^Name/), "vec");
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances")[0]?.body).toMatchObject({ name: "vec", engine: "postgresql", version: "18", variant: "pgvector" });
  });

  it("takes a SQL script for the engines that run one, a memory limit and a port, and sends them", async () => {
    const { user, backend, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.type(within(dialog).getByLabelText(/^Initialization script/), "CREATE TABLE t (id int);");
    await user.type(within(dialog).getByLabelText(/^Memory limit/), "1024");
    await user.type(within(dialog).getByLabelText(/^Port/), "5440");
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances")[0]?.body).toEqual({
      name: "postgresql-18",
      engine: "postgresql",
      version: "18",
      memory_mb: 1024,
      accept_untested: false,
      unsupported_ok: false,
      port: 5440,
      init_scripts: [{ name: "init.sql", content: "CREATE TABLE t (id int);" }],
    });
  });

  it("offers no script for an engine whose image runs none", async () => {
    const { user, dialog } = await open();
    await chooseEngine(user, dialog, "Redis");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    expect(within(dialog).queryByLabelText(/^Initialization script/)).not.toBeInTheDocument();
  });

  it("warns that an image is published for some processors only", async () => {
    const { user, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.click(within(dialog).getByRole("combobox", { name: /^Image/ }));
    await user.click(await screen.findByRole("option", { name: /PostgreSQL with PostGIS/ }));
    expect(within(dialog).getByText("Published for some processors only")).toBeInTheDocument();
    expect(within(dialog).getByText(/exists for amd64 only/)).toBeInTheDocument();
  });

  it("says a name, a memory limit or a port the server would refuse before sending anything", async () => {
    const { user, backend, dialog } = await open();
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "More options" }));
    await user.type(within(dialog).getByLabelText(/^Name/), "Bad Name");
    await user.type(within(dialog).getByLabelText(/^Memory limit/), "100");
    await user.type(within(dialog).getByLabelText(/^Port/), "80");
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    expect(await within(dialog).findByText(/Use lower-case letters, digits and single dashes/)).toBeInTheDocument();
    expect(within(dialog).getByText("This engine needs at least 256 MB.")).toBeInTheDocument();
    expect(within(dialog).getByText("A port from 1024 to 65535.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(0);
  });

  it("says which engine to choose when none is chosen, where it is missing", async () => {
    const { user, dialog } = await open();
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    expect(await within(dialog).findByText("Choose the engine to run.")).toBeInTheDocument();
  });

  it("opens the folded options when the server refuses one of them, so its refusal is where it can be read", async () => {
    const { user, dialog } = await open({
      "POST /api/databases/instances": () => problem(422, "validation_error", "Validation failed", { fields: { name: "A database server named postgresql-18 already exists." } }),
    });
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    expect(await within(dialog).findByText("A database server named postgresql-18 already exists.")).toBeVisible();
  });

  it("shows the server's refusal of a request that could not succeed, as it said it", async () => {
    const { user, dialog } = await open({
      "POST /api/databases/instances": () => problem(400, "validationerror", "Docker is not installed", { hint: "Install docker.io with its Compose plugin." }),
    });
    await chooseEngine(user, dialog, "PostgreSQL");
    await user.click(within(dialog).getByRole("button", { name: "Create database server" }));
    expect(await within(dialog).findByText("Docker is not installed")).toBeInTheDocument();
    expect(within(dialog).getByText("Install docker.io with its Compose plugin.")).toBeInTheDocument();
  });
});

describe("installing an engine of the system's own", () => {
  async function openInstall(extra: Record<string, RouteHandler> = {}) {
    const found = await serversAt(extra);
    await found.user.click(screen.getByRole("button", { name: "More actions" }));
    await found.user.click(await screen.findByRole("menuitem", { name: "Install engine" }));
    const dialog = await screen.findByRole("dialog", { name: "Install a database engine" });
    return { ...found, dialog };
  }

  it("installs from the server's catalog: a flavour, its default version, and the reason another cannot be", async () => {
    const { user, backend, dialog } = await openInstall({
      "POST /api/databases/engines/postgresql/install": () => json(202, { job_id: JOB.id, status: "pending", message: "Installing", job: JOB }),
    });
    expect(await within(dialog).findByText("The choices for Ubuntu 24.04.1 LTS, installed with apt.")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Engine" })).toHaveTextContent("PostgreSQL");
    // MariaDB cannot run beside MySQL: listed, disabled, with the server's reason.
    expect(within(dialog).getByText(/MariaDB cannot run beside it/)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("combobox", { name: "Version" }));
    await user.click(await screen.findByRole("option", { name: "17, from the upstream repository" }));
    await user.click(within(dialog).getByRole("button", { name: "Install PostgreSQL" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/engines/postgresql/install")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/engines/postgresql/install")[0]?.body).toEqual({ flavour: "postgresql", version: "17" });
    expect(await screen.findByText("Installing PostgreSQL")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Install a database engine" })).not.toBeInTheDocument();
  });

  it.each([
    ["dnf", "The choices for Ubuntu 24.04.1 LTS, installed with dnf."],
    ["none", "The choices for Ubuntu 24.04.1 LTS."],
  ])("names the package manager %s the choices are installed with, unless the server has none", async (manager, sentence) => {
    const { dialog } = await openInstall({
      "GET /api/databases/engines/catalog": () => json(200, { ...CATALOG_OF_ENGINES, apt: manager !== "none", package_manager: manager }),
    });
    expect(await within(dialog).findByText(sentence)).toBeInTheDocument();
  });
});

describe("a server older than 4.1, which has engines and no list of servers", () => {
  it("is drawn from its engines, says what it lacks, and asks the list it has", async () => {
    screenWidth(1440);
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/nodes": () => json(200, { items: [{ name: "web-2", status: "reachable", version: "4.0.0" }], total: 1 }),
      "GET /api/fleet/summary": () => json(200, { resource: "summary", generated_at: "2026-09-29T21:40:00Z", partial: false, nodes: [], items: [] }),
      ...onNode("web-2", {
        ...databaseRoutes(),
        "GET /api/openapi.json": () => json(200, { paths: { "/api/databases/engines": { get: {} }, "/api/databases/databases": { get: {} } } }),
        "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, CONTAINER_ENGINE] }),
      }),
    });
    renderConsole("/n/web-2/databases/servers");
    expect((await screen.findAllByRole("heading", { level: 2, name: "PostgreSQL 16" })).length).toBe(2);
    expect(screen.getByRole("heading", { level: 2, name: "MySQL 8.0" })).toBeInTheDocument();
    expect(screen.getByText(/runs a Noust older than 4\.1/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/nodes/web-2/api/databases/servers")).toHaveLength(0);
  });
});
