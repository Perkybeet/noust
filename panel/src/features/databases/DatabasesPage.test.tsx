import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { databaseRoutes } from "./testFixtures";

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

  it("shows the engines in one line, a version past upstream support warned about", async () => {
    await listAt();
    const strip = await screen.findByRole("navigation", { name: "Engines" });
    expect(within(strip).getByText("MongoDB")).toBeInTheDocument();
    expect(within(strip).getByText("Not installed")).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: /Upstream support for version 8\.0 ended/ })).toHaveAttribute("href", "/databases/engines");
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

  it("offers the first database when there is none, or the engines when none runs", async () => {
    await listAt("/databases", { "GET /api/databases/databases": () => json(200, { databases: [], total: 0 }) }, { rows: false });
    expect(await screen.findByRole("heading", { level: 2, name: "No databases yet" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Databases" })).toBeNull();
  });
});
