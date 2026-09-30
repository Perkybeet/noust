import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { OVERVIEW, databaseRoutes } from "./testFixtures";

const JOB = { id: "drop-1", type: "database", name: "Drop example_production", description: "", status: "pending", progress: 0, total_steps: 100, current_step: "", created_at: "2026-09-29T10:00:00", logs: [], metadata: { engine: "postgresql", database: "example_production" } };

async function pageAt(path: string, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "example_production" });
  return { ...harness, backend };
}

describe("a database's page", () => {
  it("names the database, its engine, owner and application, and draws its tabs from what the engine can do", async () => {
    await pageAt("/databases/postgresql/example_production");
    expect(await screen.findByText("Online")).toBeInTheDocument();
    const tabs = screen.getByRole("navigation", { name: "Database sections" });
    expect(within(tabs).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Data", "Query", "Backups", "Users", "Connect", "Metrics"]);
    expect(screen.getByRole("button", { name: "Back up now" })).toHaveAttribute("data-variant", "primary");
    expect(await screen.findByText("Every day at 02:00")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("draws Keys instead of Data for a Redis slot, and no Query tab", async () => {
    await pageAt("/databases/postgresql/example_production", {
      "GET /api/databases/databases/postgresql/example_production/overview": () => json(200, { ...OVERVIEW, capabilities: ["dump", "keys", "metrics"] }),
    });
    const tabs = screen.getByRole("navigation", { name: "Database sections" });
    await waitFor(() => expect(within(tabs).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Keys", "Backups", "Connect", "Metrics"]));
  });

  it("drops the database only once its name is typed, taking it from its application first, as a job it follows", async () => {
    const { user, backend } = await pageAt("/databases/postgresql/example_production", {
      "DELETE /api/databases/databases/postgresql/example_production": () => json(202, { job_id: JOB.id, status: "pending", message: "Dropping", job: JOB }),
      "GET /api/jobs/drop-1": () => json(200, JOB),
    });
    await user.click(screen.getByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Drop database" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Drop example_production" });
    expect(within(dialog).getByText(/taken away from shop\.example\.com first/)).toBeInTheDocument();
    const drop = within(dialog).getByRole("button", { name: "Drop database" });
    expect(drop).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "example_production");
    await user.click(drop);
    await waitFor(() => expect(backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")).toHaveLength(1));
    const call = backend.callsTo("DELETE /api/databases/databases/postgresql/example_production")[0];
    expect(Object.fromEntries(call?.search ?? [])).toEqual({ force: "false", keep_backup: "true", unlink: "true" });
    expect(await screen.findByText("Dropping example_production")).toBeInTheDocument();
  });
});
