import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { LIMITS, databaseRoutes } from "./testFixtures";

const ROUTE = "PUT /api/databases/engines/postgresql/limits";
const OUTCOME = { engine: "postgresql", display_name: "PostgreSQL", file: LIMITS.file, changed: ["memory_mb"], configured: { memory_mb: 1024, cpus: null, swap_mb: null, tasks: null }, warnings: [] };

async function limitsAt(extra: Record<string, RouteHandler> = {}, limitsRead = true) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole("/databases/engines/postgresql/settings");
  await screen.findByRole("heading", { level: 1, name: "PostgreSQL settings" });
  await screen.findByLabelText("max_connections");
  if (limitsRead) await screen.findByRole("textbox", { name: "Memory" });
  return { ...harness, backend };
}

describe("an engine's resource limits", { timeout: 40_000 }, () => {
  it("shows what is set, what systemd holds and whether the kernel enforces it, under the settings", async () => {
    await limitsAt({
      "GET /api/databases/engines/postgresql/limits": () =>
        json(200, { ...LIMITS, configured: { ...LIMITS.configured, memory_mb: 2048 }, effective: { ...LIMITS.effective, memory_mb: 2048, tasks: 4096 }, enforced: { ...LIMITS.enforced, tasks: false }, elsewhere: ["TasksMax"] }),
    });

    expect(await screen.findByRole("heading", { level: 2, name: "Resource limits" })).toBeInTheDocument();
    const memory = screen.getByRole("textbox", { name: "Memory" });
    expect(memory).toHaveValue("2048");
    expect(memory).toHaveAccessibleDescription(/At least 256 MB\./);
    expect(memory).toHaveAccessibleDescription(/systemd holds 2(\.0+)? GB/);
    expect(memory).toHaveAccessibleDescription(/The kernel enforces it/);
    expect(screen.getByRole("textbox", { name: "Tasks" })).toHaveAccessibleDescription(/The kernel does not enforce it/);
    expect(screen.getByText(/PostgreSQL uses 310 MB now\./)).toBeInTheDocument();
    expect(screen.getByText(/Also set outside Noust's file: TasksMax/)).toBeInTheDocument();
    expect(screen.getByText("noust db limits postgresql")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("sends what changed as numbers and an emptied limit as a removal, and says no restart was needed", async () => {
    const { user, backend } = await limitsAt({
      "GET /api/databases/engines/postgresql/limits": () => json(200, { ...LIMITS, configured: { ...LIMITS.configured, cpus: 1.5 }, effective: { ...LIMITS.effective, cpus: 1.5 } }),
      [ROUTE]: () => json(200, OUTCOME),
    });
    expect(screen.getByRole("button", { name: "Apply limits" })).toBeDisabled();

    await user.type(screen.getByRole("textbox", { name: "Memory" }), "1024");
    await user.clear(screen.getByRole("textbox", { name: "Processors" }));
    await user.click(screen.getByRole("button", { name: "Apply limits" }));

    await waitFor(() => expect(backend.callsTo(ROUTE)).toHaveLength(1));
    expect(backend.callsTo(ROUTE)[0]?.body).toEqual({ memory_mb: 1024, clear: ["cpus"], confirm: false });
    expect(await screen.findByText("The limits of PostgreSQL are applied, without a restart.")).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("GET /api/databases/engines/postgresql/limits").length).toBeGreaterThan(1));
  });

  it("refuses a value that is not a number before asking the server", async () => {
    const { user, backend } = await limitsAt({ [ROUTE]: () => json(200, OUTCOME) });

    await user.type(screen.getByRole("textbox", { name: "Memory" }), "2.5");
    await user.type(screen.getByRole("textbox", { name: "Processors" }), "0");

    expect(await screen.findByText("Enter a whole number.")).toBeInTheDocument();
    expect(screen.getByText("Enter a number above zero.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Memory" })).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Apply limits" })).toBeDisabled();
    expect(backend.callsTo(ROUTE)).toHaveLength(0);
  });

  it("asks before a memory limit below what the engine uses, in the server's own words, then confirms", async () => {
    const warning = "PostgreSQL uses 900 MB now, more than the 512 MB limit. The kernel will reclaim what it can and, when that is not enough, kill the engine's process.";
    const { user, backend } = await limitsAt({
      [ROUTE]: (call) =>
        (call.body as { confirm: boolean }).confirm
          ? json(200, OUTCOME)
          : json(400, { error: "confirmation_required", detail: "This change needs your confirmation: 1 thing to know", hint: "Confirm it to go ahead.", fields: { confirm: "x" }, output: null, warnings: [warning] }),
    });
    await user.type(screen.getByRole("textbox", { name: "Memory" }), "512");
    await user.click(screen.getByRole("button", { name: "Apply limits" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Set a limit below what PostgreSQL uses now?" });
    expect(within(dialog).getByText(warning)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Set the limit" }));

    await waitFor(() => expect(backend.callsTo(ROUTE)).toHaveLength(2));
    expect(backend.callsTo(ROUTE)[1]?.body).toEqual({ memory_mb: 512, clear: [], confirm: true });
  });

  it("shows a change that was put back with the engine's own journal", async () => {
    const journal = "FATAL:  the database system is shutting down";
    const { user } = await limitsAt({
      [ROUTE]: () => problem(500, "databaseengineerror", "PostgreSQL stopped answering under the new limits", { hint: "The previous limits were put back.", output: journal }),
    });

    await user.type(screen.getByRole("textbox", { name: "Tasks" }), "300");
    await user.click(screen.getByRole("button", { name: "Apply limits" }));

    expect(await screen.findByText("Could not change the limits of PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("The previous limits were put back.")).toBeInTheDocument();
    expect(screen.getByText(/the database system is shutting down/)).toBeInTheDocument();
  });

  it("is not there on a server that does not have the endpoint yet", async () => {
    await limitsAt({ "GET /api/databases/engines/postgresql/limits": () => json(404, { error: "not_found", detail: "Not Found", fields: {}, output: null }) }, false);

    // The section shows its loading state until the 404 comes back, then it is gone.
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Resource limits" })).not.toBeInTheDocument());
    expect(screen.queryByText("Could not read the limits of PostgreSQL")).not.toBeInTheDocument();
    expect(screen.getByLabelText("max_connections")).toBeInTheDocument();
  });

  it("says when the limits cannot be read, without losing the settings above", async () => {
    await limitsAt({ "GET /api/databases/engines/postgresql/limits": () => problem(500, "databaseengineerror", "systemd did not answer") }, false);

    expect(await screen.findByText("Could not read the limits of PostgreSQL")).toBeInTheDocument();
    expect(screen.getByLabelText("max_connections")).toBeInTheDocument();
  });
});
