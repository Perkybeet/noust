import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { CONTAINER_DATABASE, CONTAINER_ENGINE, DATABASES, DETECTED_DATABASE, ENGINES, databaseRoutes } from "./testFixtures";

async function listAt(path = "/databases", extra: Record<string, RouteHandler> = {}, { rows = true } = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Databases" });
  const table = rows ? await screen.findByRole("region", { name: "Databases" }) : document.body;
  return { ...harness, backend, table };
}

describe("the databases list", () => {
  it("lists each database with whether it is backed up, its engine, who uses it and its newest dump", async () => {
    const { table } = await listAt();
    const production = await within(table).findByText("example_production");
    const row = production.closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Backed up")).toBeInTheDocument();
    expect(within(row).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com/database");
    expect(within(row).getByText("Verified")).toBeInTheDocument();
    const staging = within(table).getByText("example_staging").closest("tr");
    if (!staging) throw new Error("no row");
    expect(within(staging).getByText("No backups scheduled")).toBeInTheDocument();
    expect(within(staging).getByText("Not tracked")).toBeInTheDocument();
    expect(screen.getByText("3 databases")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says how many databases have no schedule, and shows them on request through the URL", async () => {
    const { user, location, table } = await listAt();
    expect(await screen.findByText("2 databases have no backup schedule")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show them" }));
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
    expect(screen.queryByText("2 databases have no backup schedule")).not.toBeInTheDocument();
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
    expect(screen.queryByText("No databases yet")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Store the account" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("User"), "root");
    await user.type(within(dialog).getByLabelText("Password"), "s3cret");
    await user.click(within(dialog).getByRole("button", { name: "Try and save" }));
    await waitFor(() => expect(backend.callsTo("PUT /api/databases/engines/mysql/credentials")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/databases/engines/mysql/credentials")[0]?.body).toEqual({ user: "root", password: "s3cret" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("leaves the engines to their own tab: only the filters and one notice above the list", async () => {
    await listAt();
    expect(await screen.findByRole("region", { name: "Databases" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Engines" })).not.toBeInTheDocument();
  });

  it("creates a database and opens it", async () => {
    const { user, backend, location } = await listAt("/databases", {
      "POST /api/databases/databases": () => json(200, { name: "acme_shop", engine: "postgresql", tables: 0, tracked: true, missing: false, apps: [] }),
      "GET /api/databases/databases/postgresql/acme_shop/overview": () => json(404, { detail: "nope", error: "not_found" }),
    });
    await user.click(screen.getByRole("button", { name: "New database" }));
    const dialog = await screen.findByRole("dialog", { name: "New database" });
    await user.click(within(dialog).getByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: /PostgreSQL/ }));
    await user.type(within(dialog).getByLabelText("Name"), "acme_shop");
    await user.click(within(dialog).getByRole("button", { name: "Create database" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/databases")[0]?.body).toEqual({ engine: "postgresql", name: "acme_shop", owner: null, encoding: null, app: null });
    await waitFor(() => expect(location().pathname).toBe("/databases/postgresql/acme_shop"));
  });

  it("says where a database in a container lives, and offers its instance as a filter", async () => {
    const { user, table, location } = await listAt("/databases", {
      "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, CONTAINER_ENGINE] }),
      "GET /api/databases/databases": () => json(200, { databases: [...DATABASES, CONTAINER_DATABASE], total: DATABASES.length + 1 }),
    });
    const row = (await within(table).findByText("proggest")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Container")).toBeInTheDocument();
    expect(within(row).getByText("proggest/postgres")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "proggest.es" })).toHaveAttribute("href", "/apps/proggest.es");
    // A host database says nothing about containers.
    const production = within(table).getByText("example_production").closest("tr");
    if (!production) throw new Error("no row");
    expect(within(production).queryByText("Container")).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL in proggest/postgres" }));
    await waitFor(() => expect(location().search).toEqual({ engine: "postgresql@proggest.postgres" }));
    await waitFor(() => expect(within(table).queryByText("example_production")).not.toBeInTheDocument());
    expect(within(table).getByText("proggest")).toBeInTheDocument();
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
    const row = (await within(table).findByText("example_staging")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByRole("link", { name: "docs.example.com" })).toHaveAttribute("href", "/apps/docs.example.com");
    expect(within(row).getByText("Detected")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Actions for example_staging" }));
    const item = await screen.findByRole("menuitem", { name: "Record that docs.example.com uses it" });
    expect(item).toHaveAccessibleDescription("Nothing in the application changes: its environment already names this database.");
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
    expect(await screen.findByText("2 databases have no backup schedule")).toBeInTheDocument();
    expect(screen.queryByText(/open to the network/)).not.toBeInTheDocument();
  });

  it("draws placeholder rows, not a finished-looking page, while the list is read", async () => {
    // Opening the tab used to leave the header over a blank area: nothing said anything was coming.
    let answer: (response: Response) => void = () => undefined;
    const { table } = await listAt("/databases", {
      "GET /api/databases/databases": () => new Promise<Response>((resolve) => (answer = resolve)),
    });
    // The table is there from the first frame, busy, with the rows' shape and the filters above it.
    expect(table.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(table.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.getByRole("search", { name: "Filter databases" })).toBeInTheDocument();
    // Nothing is claimed about a list that is not known yet.
    expect(screen.queryByText("No databases yet")).not.toBeInTheDocument();
    expect(screen.queryByText(/databases? have no backup schedule/)).not.toBeInTheDocument();
    expect(screen.queryByText("0 databases")).not.toBeInTheDocument();

    answer(json(200, { databases: DATABASES, total: DATABASES.length }));
    // The rows arrive in the same table, which stays busy no longer.
    expect(await within(table).findByText("example_production")).toBeInTheDocument();
    expect(table.querySelector("table")).not.toHaveAttribute("aria-busy");
  });

  it("holds the rows behind placeholders until the notice above them is known", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { table } = await listAt("/databases", {
      "GET /api/databases/exposure": () => new Promise<Response>((resolve) => (answer = resolve)),
    });
    expect(table.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(within(table).queryByText("example_production")).not.toBeInTheDocument();
    answer(json(200, { exposed: [], firewalled: [] }));
    expect(await within(table).findByText("example_production")).toBeInTheDocument();
  });

  it("shows the rows after a second even when the open-port check has not answered", async () => {
    // The check reads the firewall and can take seconds: the rows the operator came for do not wait for it.
    const { table } = await listAt("/databases", {
      "GET /api/databases/exposure": () => new Promise<Response>(() => undefined),
    });
    expect(within(table).queryByText("example_production")).not.toBeInTheDocument();
    expect(await within(table).findByText("example_production", undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(table.querySelector("table")).not.toHaveAttribute("aria-busy");
  });

  it("offers the first database when there is none, or the engines when none runs", async () => {
    await listAt("/databases", { "GET /api/databases/databases": () => json(200, { databases: [], total: 0 }) }, { rows: false });
    expect(await screen.findByRole("heading", { level: 2, name: "No databases yet" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Databases" })).toBeNull();
  });
});
