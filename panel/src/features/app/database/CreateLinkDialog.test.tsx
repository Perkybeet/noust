import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { CHOICES, planFor } from "../../databases/provisioning/testFixtures";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const JOB = { id: "provision-1", type: "database", name: "Provision", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-10-06T10:00:00", logs: [], metadata: { domain: TAB_DOMAIN } };

async function dialogAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/databases`]: () => json(200, { domain: TAB_DOMAIN, databases: [] }),
        "GET /api/databases/provisioning/choices": () => json(200, { choices: CHOICES }),
        "GET /api/databases/provisioning/plan": (call) => json(200, planFor(call.search.get("engine") ?? "postgresql", { version: call.search.get("version") })),
        [`POST /api/apps/${TAB_DOMAIN}/databases`]: () => json(202, { job_id: JOB.id, status: "pending", message: "Creating", job: JOB }),
        "GET /api/jobs/provision-1": () => json(200, JOB),
        ...extra,
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/database`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  await harness.user.click(await screen.findByRole("button", { name: "Create database" }));
  const dialog = await screen.findByRole("dialog", { name: "Create a database" });
  return { ...harness, backend, dialog };
}

describe("creating a database for an application", () => {
  it("starts on the engine that is installed, with the version that runs, and says what it will write", async () => {
    const { dialog, backend } = await dialogAt();
    expect(await within(dialog).findByRole("combobox", { name: "Engine" })).toHaveTextContent("PostgreSQL");
    expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveTextContent("The version that runs (16.4)");
    expect(await within(dialog).findByText(/postgresql:\/\/.*\*{8}/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/databases/provisioning/plan")[0]?.search.get("engine")).toBe("postgresql");
    await expectNoAxeViolations(dialog);
  });

  it("offers only what runs for an installed engine, and lists what cannot be used with the server's reason", async () => {
    const { user, dialog } = await dialogAt();
    await user.click(await within(dialog).findByRole("combobox", { name: "Version" }));
    const listbox = await screen.findByRole("listbox");
    expect(within(listbox).getByRole("option", { name: /^16/ })).not.toHaveAttribute("aria-disabled", "true");
    expect(within(listbox).getByRole("option", { name: /^17/ })).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");
    expect(within(dialog).getByText("MariaDB: MySQL is installed, and MariaDB cannot run beside it.")).toBeInTheDocument();
  });

  it("asks to install an engine that is not installed before creating on it", async () => {
    const { user, dialog, backend } = await dialogAt();
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: /MongoDB/ }));
    await waitFor(() => expect(backend.callsTo("GET /api/databases/provisioning/plan").some((call) => call.search.get("engine") === "mongodb")).toBe(true));
    expect(within(dialog).getByRole("combobox", { name: "Version" })).toHaveTextContent("Default (8.0)");
    const install = within(dialog).getByRole("checkbox", { name: /Install MongoDB first/ });
    expect(install).not.toBeChecked();

    await user.click(within(dialog).getByRole("button", { name: "Create and link" }));
    expect(await within(dialog).findByText(/Tick it to install MongoDB/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/databases`)).toHaveLength(0);

    await user.click(install);
    await user.click(within(dialog).getByRole("combobox", { name: "Version" }));
    await user.click(await screen.findByRole("option", { name: /^7\.0/ }));
    await user.click(within(dialog).getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/databases`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/databases`)[0]?.body).toMatchObject({ engine: "mongodb", version: "7.0", install: true, restart: true });
  });

  it("asks for no version when the default stands, and shows the server's refusal verbatim", async () => {
    const { user, dialog, backend } = await dialogAt({
      [`POST /api/apps/${TAB_DOMAIN}/databases`]: () =>
        problem(400, "validation_error", "PostgreSQL 17 was asked for, but this server runs PostgreSQL 16.4", { hint: "Choose the version that runs." }),
    });
    await within(dialog).findByText(/postgresql:\/\/.*\*{8}/);
    await user.click(within(dialog).getByRole("button", { name: "Create and link" }));
    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/databases`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/databases`)[0]?.body).toMatchObject({ engine: "postgresql", version: null, install: false });
    expect(await within(dialog).findByText("PostgreSQL 17 was asked for, but this server runs PostgreSQL 16.4")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose the version that runs.")).toBeInTheDocument();
  });

  it("says so when no engine can hold a database here", async () => {
    const { dialog } = await dialogAt({
      "GET /api/databases/provisioning/choices": () => json(200, { choices: CHOICES.filter((choice) => choice.flavour === "mariadb") }),
    });
    expect(await within(dialog).findByText("No engine can hold a database here")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create and link" })).toBeDisabled();
  });
});
