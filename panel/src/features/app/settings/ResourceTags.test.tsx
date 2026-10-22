import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServiceList } from "../../../api/queries/services";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { findLoadedRegion } from "../../../test/regions";
import { filterDatabases, validateDatabasesSearch } from "../../databases/filters";
import { DATABASES, databaseRoutes } from "../../databases/testFixtures";
import { filterServices, validateServicesSearch } from "../../services/data";
import { onDesktop, serverRoutes } from "../../server/testing";
import { APPS_IN_TEAMS, teamsRoutes } from "../../teams/testFixtures";
import { screenWidth } from "../testRoutes";

vi.setConfig({ testTimeout: 30_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

describe("tags on databases", () => {
  const TAGGED = DATABASES.map((database) => (database.name === "example_production" ? { ...database, tags: ["prod", "customer-a"] } : { ...database, tags: [] }));

  it("filters the list by a tag, finds one by typing it, and drops a tag the server could not have made", () => {
    expect(filterDatabases(TAGGED, { tag: "prod" }, new Map()).map((database) => database.name)).toEqual(["example_production"]);
    expect(filterDatabases(TAGGED, { q: "customer-a" }, new Map()).map((database) => database.name)).toEqual(["example_production"]);
    expect(validateDatabasesSearch({ tag: "prod" })).toEqual({ tag: "prod" });
    expect(validateDatabasesSearch({ tag: "Not A Tag" })).toEqual({});
  });

  async function listAt(path = "/databases", extra: Record<string, RouteHandler> = {}) {
    screenWidth(1440);
    const backend = fakeBackend(databaseRoutes({ "GET /api/databases/databases": () => json(200, { databases: TAGGED, total: TAGGED.length }), ...extra }));
    const harness = renderConsole(path);
    await screen.findByRole("heading", { level: 1, name: "Databases" });
    // The rows come once the list is known, with the filters above them.
    await screen.findByRole("search", { name: "Filter databases" });
    const table = await screen.findByRole("region", { name: "Databases" });
    return { ...harness, backend, table };
  }

  it("shows each database's tags under its name and offers the filter only once one has a tag", async () => {
    const { table, user } = await listAt();
    const row = (await within(table).findByText("example_production")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(within(row).getByRole("list", { name: "Tags" })).getByText("customer-a")).toBeInTheDocument();

    await user.click(await screen.findByRole("combobox", { name: "Tag" }));
    await user.click(await screen.findByRole("option", { name: "prod" }));
    await waitFor(() => {
      expect(within(table).queryByText("example_staging")).not.toBeInTheDocument();
    });
    expect(within(table).getByText("example_production")).toBeInTheDocument();
  });

  it("saves the tags typed from the row's menu", async () => {
    const { backend, user, table } = await listAt("/databases", {
      "PUT /api/databases/databases/postgresql/example_production/tags": () => json(200, { engine: "postgresql", database: "example_production", tags: ["eu", "prod"], max_tags: 16 }),
    });
    await within(table).findByText("example_production");
    await user.click(screen.getByRole("button", { name: "Actions for example_production" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit tags…" }));
    const dialog = await screen.findByRole("dialog", { name: "Tags of the database example_production" });
    const field = within(dialog).getByLabelText(/Tags/);
    expect(field).toHaveValue("prod, customer-a");
    await user.clear(field);
    await user.type(field, "prod, eu");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/databases/databases/postgresql/example_production/tags")[0]?.body).toEqual({ tags: ["prod", "eu"] });
    });
  });

  it("refuses a tag the server would refuse before sending anything, and shows its refusal otherwise", async () => {
    const { backend, user, table } = await listAt("/databases", {
      "PUT /api/databases/databases/postgresql/example_production/tags": () => problem(404, "not_found", "Database not found: example_production"),
    });
    await within(table).findByText("example_production");
    await user.click(screen.getByRole("button", { name: "Actions for example_production" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit tags…" }));
    const dialog = await screen.findByRole("dialog", { name: "Tags of the database example_production" });
    await user.type(within(dialog).getByLabelText(/Tags/), ", Bad Tag");
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));
    expect(await within(dialog).findByText(/Not tags: Bad, Tag\./)).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/databases/databases/postgresql/example_production/tags")).toHaveLength(0);

    const field = within(dialog).getByLabelText(/Tags/);
    await user.clear(field);
    await user.type(field, "prod");
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));
    expect(await within(dialog).findByText("Database not found: example_production")).toBeInTheDocument();
  });
});

describe("tags on services", () => {
  const SERVICES: ServiceList["services"] = [
    {
      name: "worker",
      description: "/usr/bin/node worker.js",
      active: true,
      enabled: true,
      status: "running",
      pid: 4213,
      uptime: "Wed 2026-09-24 10:00:00 UTC",
      memory: "104857600",
      managed: true,
      active_state: "active",
      sub_state: "running",
      result: "success",
      tags: ["prod"],
    },
    {
      name: "mailer",
      description: "/usr/bin/node mailer.js",
      active: false,
      enabled: false,
      status: "stopped",
      pid: null,
      uptime: null,
      memory: null,
      managed: true,
      active_state: "inactive",
      sub_state: "dead",
      result: "success",
      tags: [],
    },
  ];

  beforeEach(onDesktop);

  it("filters by a tag, finds one by typing it, and drops a tag the server could not have made", () => {
    expect(filterServices(SERVICES, { tag: "prod" }).map((service) => service.name)).toEqual(["worker"]);
    expect(filterServices(SERVICES, { q: "prod" }).map((service) => service.name)).toEqual(["worker"]);
    expect(validateServicesSearch({ tag: "prod" })).toEqual({ tag: "prod" });
    expect(validateServicesSearch({ tag: "Not A Tag" })).toEqual({});
  });

  async function servicesAt(extra: Record<string, RouteHandler> = {}) {
    const backend = fakeBackend({
      ...signedInRoutes(),
      ...serverRoutes(),
      "GET /api/services": () => json(200, { services: SERVICES, total: SERVICES.length }),
      ...extra,
    });
    const harness = renderConsole("/server/services");
    await screen.findByRole("heading", { level: 1, name: "Server" });
    // A list replaces its skeleton: the region to look in is the loaded one.
    const table = await findLoadedRegion(/Services/);
    return { ...harness, backend, table };
  }

  it("shows each unit's tags under its name and narrows the list to one", async () => {
    const { table, user } = await servicesAt();
    const row = (await within(table).findByText("worker")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(within(row).getByRole("list", { name: "Tags" })).getByText("prod")).toBeInTheDocument();

    await user.click(await screen.findByRole("combobox", { name: "Tag" }));
    await user.click(await screen.findByRole("option", { name: "prod" }));
    await waitFor(() => {
      expect(within(table).queryByText("mailer")).not.toBeInTheDocument();
    });
  });

  it("saves the tags typed from the row's menu", async () => {
    const { backend, user, table } = await servicesAt({
      "PUT /api/services/mailer/tags": () => json(200, { service: "mailer", tags: ["nightly"], max_tags: 16 }),
    });
    await within(table).findByText("mailer");
    await user.click(screen.getByRole("button", { name: "Actions for mailer" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit tags…" }));
    const dialog = await screen.findByRole("dialog", { name: "Tags of the service mailer" });
    await user.type(within(dialog).getByLabelText(/Tags/), "nightly");
    await user.click(within(dialog).getByRole("button", { name: "Save tags" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/services/mailer/tags")[0]?.body).toEqual({ tags: ["nightly"] });
    });
  });

  it("is in Spanish", async () => {
    const { user, table } = await servicesAt();
    await within(table).findByText("mailer");
    await act(() => setLocale("es"));
    await user.click(screen.getByRole("button", { name: "Acciones de mailer" }));
    expect(await screen.findByRole("menuitem", { name: "Editar etiquetas…" })).toBeInTheDocument();
  });
});

describe("restarting what carries a tag", () => {
  const SHOP = "shop.example.com";

  function list(extra: Record<string, RouteHandler> = {}) {
    screenWidth(1440);
    const apps = APPS_IN_TEAMS.map((app) => ({ ...app, layout: "releases", tags: app.domain === SHOP || app.domain === "staging.example.com" ? ["web"] : [] }));
    const backend = fakeBackend({
      ...teamsRoutes(),
      "GET /api/apps": () => json(200, { total: apps.length, apps }),
      "GET /api/deployments": () => json(200, { items: [], total: 0 }),
      ...extra,
    });
    const harness = renderConsole("/apps?tag=web");
    return { ...harness, backend };
  }

  it("asks first, restarts them and says what was done and what was left", async () => {
    const { user, backend } = list({
      "POST /api/tags/web/restart": () =>
        json(200, {
          tag: "web",
          dry_run: false,
          restarted: 2,
          results: [
            { kind: "application", name: SHOP, outcome: "restarted", detail: "" },
            { kind: "application", name: "staging.example.com", outcome: "restarted", detail: "" },
            { kind: "database", name: "postgresql/shop", outcome: "skipped", detail: "A database is restarted with its engine." },
          ],
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Restart tagged" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Restart everything tagged web?" });
    expect(within(dialog).getByText(/2 apps carry web/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/tags/web/restart")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Restart them" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/tags/web/restart")).toHaveLength(1);
    });
    expect(await screen.findByText("Restarted 2 things tagged web")).toBeInTheDocument();
    expect(await screen.findByText("1 was left alone")).toBeInTheDocument();
  });

  it("says which ones could not be restarted, in the server's words", async () => {
    const { user } = list({
      "POST /api/tags/web/restart": () =>
        json(200, {
          tag: "web",
          dry_run: false,
          restarted: 0,
          results: [{ kind: "application", name: SHOP, outcome: "failed", detail: "shop.example.com would not restart. systemd said no" }],
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Restart tagged" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Restart them" }));

    expect(await screen.findByText("1 could not be restarted")).toBeInTheDocument();
    expect(screen.getByText(`${SHOP}: shop.example.com would not restart. systemd said no`)).toBeInTheDocument();
  });

  it("is offered only where the list is narrowed to a tag", async () => {
    screenWidth(1440);
    fakeBackend({ ...teamsRoutes(), "GET /api/apps": () => json(200, { total: 0, apps: [] }), "GET /api/deployments": () => json(200, { items: [], total: 0 }) });
    renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    expect(screen.queryByRole("button", { name: "Restart tagged" })).not.toBeInTheDocument();
  });
});
