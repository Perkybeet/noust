import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { ENGINES, SERVERS, databaseRoutes } from "./testFixtures";

async function dialogAt(extra: Record<string, RouteHandler> = {}, path = "/databases?new=true") {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole(path);
  const dialog = await screen.findByRole("dialog", { name: "New database" });
  return { ...harness, backend, dialog };
}

describe("New database, the one dialog that makes a database", () => {
  it("asks for a name, whether it starts empty or from a dump, and the engine as a card each with where it runs and what it holds", async () => {
    const { dialog } = await dialogAt();
    expect(within(dialog).getByLabelText("Name")).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: "Empty" })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: "From a dump" })).not.toBeChecked();
    // The default engine is chosen already: nobody is asked where it runs when there is one place.
    const postgres = await within(dialog).findByRole("radio", { name: /PostgreSQL 16/ });
    expect(postgres).toBeChecked();
    expect(within(dialog).getByText(/On the system, port 5432\. 2 databases\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /MySQL 8\.0/ })).not.toBeChecked();
    // Redis has slots and not databases to make, and the engine that is stopped is not one to make a database on.
    expect(within(dialog).queryByRole("radio", { name: /Redis/ })).toBeNull();
    expect(within(dialog).getByRole("radio", { name: /Another engine or version/ })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });

  it("makes the database on the engine chosen and opens its page", async () => {
    const { user, dialog, backend, location } = await dialogAt({
      "POST /api/databases/databases": () => json(200, { name: "reservas", engine: "mysql", size: null, tracked: true, missing: false, unverified: false, apps: [] }),
    });
    await user.click(await within(dialog).findByRole("radio", { name: /MySQL 8\.0/ }));
    await user.type(within(dialog).getByLabelText("Name"), "reservas");
    await user.click(within(dialog).getByRole("button", { name: "Create the database" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/databases")[0]?.body).toMatchObject({ engine: "mysql", name: "reservas", app: null });
    await waitFor(() => expect(location().pathname).toBe("/databases/mysql/reservas"));
  });

  it("says what is wrong with the name under the name, and sends nothing", async () => {
    const { user, dialog, backend } = await dialogAt();
    await user.click(within(dialog).getByRole("button", { name: "Create the database" }));
    expect(await within(dialog).findByText("Name the database.")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Name"), "9lives");
    await user.click(within(dialog).getByRole("button", { name: "Create the database" }));
    expect(await within(dialog).findByText(/Use letters, digits and underscores/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(0);
  });

  it("starts from a dump: the file is asked for, and a press without it says so under the file instead of doing nothing", async () => {
    const { user, dialog, backend } = await dialogAt();
    await user.click(within(dialog).getByRole("radio", { name: "From a dump" }));
    expect(within(dialog).getByLabelText("Dump file")).toBeInTheDocument();
    // A database from a dump is linked to an application afterwards, from Connect: the field is not offered here.
    expect(within(dialog).queryByText("Used by")).toBeNull();
    expect(within(dialog).getByText(/link it afterwards from its Connect page/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Name"), "reservas");
    await user.click(within(dialog).getByRole("button", { name: "Create the database" }));
    expect(await within(dialog).findByText("Choose the dump to import.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/imports")).toHaveLength(0);
    expect(backend.callsTo("POST /api/databases/databases")).toHaveLength(0);
  });

  it("makes a database for an application as one that is linked, a job its Database tab follows", async () => {
    const { user, dialog, backend, location } = await dialogAt({
      "POST /api/apps/shop.example.com/databases": () => json(202, { job_id: "link-1", status: "pending", message: "Creating", job: null }),
    });
    await user.click(within(dialog).getByRole("combobox", { name: /Used by/ }));
    await user.click(await screen.findByRole("option", { name: "shop.example.com" }));
    await user.click(within(dialog).getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps/shop.example.com/databases")).toHaveLength(1));
    await waitFor(() => expect(location().pathname).toBe("/apps/shop.example.com/database"));
  });

  it("leads to adding an engine from its last card", async () => {
    const { user, dialog } = await dialogAt();
    await user.click(await within(dialog).findByRole("radio", { name: /Another engine or version/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add an engine" }));
    expect(await screen.findByRole("dialog", { name: "Add an engine" })).toBeInTheDocument();
  });

  it("says no engine is running, and the button adds one, when there is none to make a database on", async () => {
    const { dialog } = await dialogAt({
      "GET /api/databases/servers": () => json(200, { servers: SERVERS.map((server) => ({ ...server, running: false, state: "stopped" })), problems: [] }),
      "GET /api/databases/engines": () => json(200, { engines: ENGINES.map((engine) => ({ ...engine, running: false })) }),
    });
    expect(await within(dialog).findByText("No engine is running")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add an engine" })).toBeInTheDocument();
  });
});
