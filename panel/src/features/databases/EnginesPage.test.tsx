import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { CONTAINER_ENGINE, ENGINES, databaseRoutes } from "./testFixtures";

const JOB = { id: "install-1", type: "database", name: "Install PostgreSQL", description: "", status: "pending", progress: 0, total_steps: 100, current_step: "", created_at: "2026-10-03T10:00:00", logs: [], metadata: { engine: "postgresql" } };

async function enginesAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, CONTAINER_ENGINE] }),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/engines");
  await screen.findByRole("heading", { level: 1, name: "Databases" });
  const hosts = await screen.findByRole("region", { name: "Database engines" });
  await within(hosts).findByText("PostgreSQL");
  return { ...harness, backend, hosts };
}

describe("the engines tab", () => {
  it("lists the server's own engines, then the engines in containers with where they run and whose they are", async () => {
    const { hosts } = await enginesAt();
    expect(within(hosts).queryByText("proggest/postgres")).not.toBeInTheDocument();
    const section = screen.getByRole("region", { name: "In containers" });
    const containers = within(section).getByRole("region", { name: "Engines in containers" });
    const row = within(containers).getByText("proggest/postgres").closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).getByText("postgres:16-alpine")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "proggest.es" })).toHaveAttribute("href", "/apps/proggest.es");
    expect(within(row).getByText("Application's account only")).toBeInTheDocument();
    expect(within(section).getByText(/Noust found only the application's own account/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("offers settings for the server's engines, never install or settings for a container", async () => {
    const { user, location, hosts } = await enginesAt();
    await user.click(screen.getByRole("button", { name: "Actions for proggest/postgres" }));
    await screen.findByRole("menu");
    expect(screen.queryByRole("menuitem", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Uninstall" })).not.toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Restart" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());

    await user.click(within(hosts).getByRole("button", { name: "Actions for PostgreSQL" }));
    await user.click(await screen.findByRole("menuitem", { name: "Settings" }));
    await waitFor(() => expect(location().pathname).toBe("/databases/engines/postgresql/settings"));
  });

  it("asks before stopping a container that holds an application's data", async () => {
    const { user, backend } = await enginesAt({
      "POST /api/databases/engines/postgresql%40proggest.postgres/stop": () => json(200, { success: true, message: "Stopped" }),
    });
    await user.click(screen.getByRole("button", { name: "Actions for proggest/postgres" }));
    await user.click(await screen.findByRole("menuitem", { name: "Stop" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop proggest/postgres" });
    expect(within(dialog).getByText(/holds the data of proggest\.es/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/engines/postgresql%40proggest.postgres/stop")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Stop container" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/engines/postgresql%40proggest.postgres/stop")).toHaveLength(1));
  });

  it("installs from the server's catalog: a flavour, its default version, and the reason another cannot be", async () => {
    const { user, backend } = await enginesAt({
      "GET /api/databases/engines": () => json(200, { engines: ENGINES.map((engine) => (engine.name === "postgresql" ? { ...engine, installed: false, running: false, version: null } : engine)) }),
      "POST /api/databases/engines/postgresql/install": () => json(202, { job_id: JOB.id, status: "pending", message: "Installing", job: JOB }),
      "GET /api/jobs/install-1": () => json(200, JOB),
    });
    await user.click(screen.getByRole("button", { name: "Install engine" }));
    const dialog = await screen.findByRole("dialog", { name: "Install a database engine" });
    expect(await within(dialog).findByText("The choices for Ubuntu 24.04.1 LTS.")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Engine" })).toHaveTextContent("PostgreSQL");
    expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveTextContent("16, from the distribution (default)");
    // MariaDB cannot run beside MySQL: listed, disabled, with the server's reason.
    expect(within(dialog).getByText(/MariaDB cannot run beside it/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Engine" }));
    expect(await screen.findByRole("option", { name: "MariaDB (not available)" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("option", { name: "MySQL (installed)" })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("combobox", { name: "Version" }));
    await user.click(await screen.findByRole("option", { name: "17, from the upstream repository" }));
    await user.click(within(dialog).getByRole("button", { name: "Install PostgreSQL" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/engines/postgresql/install")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/engines/postgresql/install")[0]?.body).toEqual({ flavour: "postgresql", version: "17" });
    expect(await screen.findByText("Installing PostgreSQL")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Install a database engine" })).not.toBeInTheDocument();
  });

  it("opens the installation on the flavour of the row it was chosen from", async () => {
    const { user, hosts } = await enginesAt();
    await user.click(within(hosts).getByRole("button", { name: "Actions for MongoDB" }));
    await user.click(await screen.findByRole("menuitem", { name: "Install" }));
    const dialog = await screen.findByRole("dialog", { name: "Install a database engine" });
    await waitFor(() => expect(within(dialog).getByRole("combobox", { name: "Engine" })).toHaveTextContent("MongoDB"));
    expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveTextContent("7.0, from the upstream repository (default)");
  });
});
