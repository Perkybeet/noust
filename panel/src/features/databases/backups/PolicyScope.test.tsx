import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { POLICY, databaseRoutes } from "../testFixtures";

const ALL = "GET /api/databases/backup-policies/postgresql/*";
const PUT_ALL = "PUT /api/databases/backup-policies/postgresql/*";
const PUT_ONE = "PUT /api/databases/backup-policies/postgresql/example_production";

const EVERYTHING = {
  ...POLICY,
  database: "*",
  scope: "all",
  exclusions: { databases: ["scratch"], schemas: ["audit"], tables: ["logs"] },
  destinations: [],
};

async function backupsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/backup-destinations": () => json(200, { destinations: [] }),
      "GET /api/databases/backups/remote": () => json(200, { destination: "x", engine: "postgresql", database: "example_production", dumps: [], total: 0 }),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production/backups");
  await screen.findByRole("heading", { level: 2, name: "Every PostgreSQL database" });
  return { ...harness, backend };
}

describe("the policy for every database of an engine", () => {
  it("is offered beside the database's own, and says no policy covers them when none does", async () => {
    await backupsAt();
    expect(await screen.findByText(/No schedule covers every PostgreSQL database/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back up every database" })).toBeInTheDocument();
  });

  it("is made with the databases, schemas and tables to leave out, and saved under the engine's every-database name", { timeout: 20_000 }, async () => {
    const { user, backend } = await backupsAt({
      [PUT_ALL]: (call) => json(200, { ...EVERYTHING, ...(call.body as object) }),
    });
    await user.click(await screen.findByRole("button", { name: "Back up every database" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of every PostgreSQL database" });
    expect(within(drawer).getByText(/including the ones created later/)).toBeInTheDocument();
    await user.type(within(drawer).getByLabelText(/^Databases/), "scratch, cache");
    await user.type(within(drawer).getByLabelText(/^Schemas/), "audit");
    await user.type(within(drawer).getByLabelText(/^Tables/), "logs{enter}sessions");
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo(PUT_ALL)).toHaveLength(1));
    expect(backend.callsTo(PUT_ALL)[0]?.body).toMatchObject({
      exclude_databases: ["scratch", "cache"],
      exclude_schemas: ["audit"],
      exclude_tables: ["logs", "sessions"],
      schedule: "*-*-* 02:00:00",
    });
  });

  it("says a name the server would refuse before sending it", async () => {
    const { user, backend } = await backupsAt();
    await user.click(await screen.findByRole("button", { name: "Back up every database" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of every PostgreSQL database" });
    await user.type(within(drawer).getByLabelText(/^Databases/), "--exclude-schema ../etc ok");
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    expect(await within(drawer).findByText("Not a valid name: --exclude-schema, ../etc.")).toBeInTheDocument();
    expect(backend.callsTo(PUT_ALL)).toHaveLength(0);
  });

  it("puts what the server refuses under the list it is about", async () => {
    const { user } = await backupsAt({
      [PUT_ALL]: () => problem(422, "validation_error", "bad", { fields: { exclude_tables: "'x' is not a valid name for exclude_tables" } }),
    });
    await user.click(await screen.findByRole("button", { name: "Back up every database" }));
    const drawer = await screen.findByRole("dialog", { name: "Backups of every PostgreSQL database" });
    await user.type(within(drawer).getByLabelText(/^Tables/), "x");
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    expect(await within(drawer).findByText("'x' is not a valid name for exclude_tables")).toBeInTheDocument();
  });

  it("shows what it covers and leaves out, and backs up a database that has no schedule of its own", async () => {
    await backupsAt({
      "GET /api/databases/backup-policies/postgresql/example_production": () => json(200, { engine: "postgresql", database: "example_production", configured: false, enabled: false, scope: "database", exclusions: {}, destinations: [], timer: {} }),
      [ALL]: () => json(200, EVERYTHING),
    });
    expect(await screen.findByText("Every PostgreSQL database, example_production included")).toBeInTheDocument();
    expect(screen.getByText("databases scratch; schemas audit; tables logs")).toBeInTheDocument();
    // The database's own card does not say nothing backs it up: the engine's policy does.
    expect(await screen.findByText(/The schedule for every PostgreSQL database backs this one up/)).toBeInTheDocument();
  });

  it("leaves out a database it excludes, and says so", async () => {
    await backupsAt({
      [ALL]: () => json(200, { ...EVERYTHING, exclusions: { databases: ["example_production"] } }),
    });
    expect(await screen.findByText("Every PostgreSQL database, but not example_production")).toBeInTheDocument();
  });

  it("is edited from the database's own policy too: schemas and tables, never whole databases", async () => {
    const { user, backend } = await backupsAt({
      [PUT_ONE]: (call) => json(200, { ...POLICY, ...(call.body as object) }),
    });
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    expect(within(drawer).queryByLabelText(/^Databases/)).not.toBeInTheDocument();
    await user.type(within(drawer).getByLabelText(/^Tables/), "public.sessions");
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(backend.callsTo(PUT_ONE)).toHaveLength(1));
    expect(backend.callsTo(PUT_ONE)[0]?.body).toMatchObject({ exclude_databases: [], exclude_schemas: [], exclude_tables: ["public.sessions"] });
  });
});
