import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { DATABASES, OVERVIEW, databaseRoutes } from "../testFixtures";

const BASE = "/api/databases/databases/postgresql/example_production";

async function settingsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole("/databases/postgresql/example_production/settings");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  const about = (await screen.findByRole("heading", { level: 2, name: "This database" })).closest("section");
  if (about === null) throw new Error("no section");
  return { ...harness, backend, about };
}

describe("the settings of a database", () => {
  it("say what Noust and its engine know of it, and offer to give it to the account its application signs in as", async () => {
    const { about, user } = await settingsAt();
    expect(await within(about).findByText("PostgreSQL 16 (on the system)")).toBeInTheDocument();
    expect(within(about).getByText("Owner").nextElementSibling).toHaveTextContent("shop_app");
    expect(within(about).getByText("Port").nextElementSibling).toHaveTextContent("5432");
    // Its owner is not the account its application uses: the one repair there is, said where it is offered.
    await user.click(within(about).getByRole("button", { name: "Give it to example_production" }));
    expect(await screen.findByRole("dialog", { name: /Give the database to example_production/ })).toBeInTheDocument();
  });

  it("offers the owner repair for PostgreSQL in a container too, by what the engine is and not by its key", async () => {
    screenWidth(1440);
    const key = "postgresql@pg16";
    const base = "/api/databases/databases/postgresql%40pg16/shop_db";
    fakeBackend(
      databaseRoutes({
        [`GET ${base}/overview`]: () => json(200, { ...OVERVIEW, database: { ...DATABASES[0], name: "shop_db", engine: key, owner: "postgres", username: "shop_user" } }),
      }),
    );
    renderConsole(`/databases/${key}/shop_db/settings`);
    expect(await screen.findByRole("button", { name: "Give it to shop_user" })).toBeInTheDocument();
  });

  it("list the extensions and the move to another engine, which asks the engines it can go to", async () => {
    const { user } = await settingsAt();
    expect(await screen.findByRole("heading", { level: 2, name: "Extensions" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Move to another engine" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Move to another engine…" }));
    expect(await screen.findByRole("dialog", { name: "Move example_production to another engine" })).toBeInTheDocument();
  });

  it("have no extensions or move for an engine that has none to give, and no repair for a database already its application's", async () => {
    const { backend } = await settingsAt({
      [`GET ${BASE}/overview`]: () => json(200, { ...OVERVIEW, capabilities: ["tables"], database: { ...DATABASES[0], owner: "example_production", username: "example_production" } }),
    });
    await waitFor(() => expect(backend.callsTo(`GET ${BASE}/overview`)).toHaveLength(1));
    expect(await screen.findByText("Owner")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Extensions" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 2, name: "Move to another engine" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Give it to/ })).toBeNull();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
