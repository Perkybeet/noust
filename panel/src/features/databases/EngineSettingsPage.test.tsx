import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { SETTINGS, databaseRoutes } from "./testFixtures";

const ROUTE = "PUT /api/databases/engines/postgresql/settings";

const OUTCOME = { engine: "postgresql", display_name: "PostgreSQL", file: SETTINGS.file, changed: ["max_connections"], action: "restart", exposed: false, warnings: [] };

async function settingsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(databaseRoutes(extra));
  const harness = renderConsole("/databases/engines/postgresql/settings");
  await screen.findByRole("heading", { level: 1, name: "PostgreSQL settings" });
  await screen.findByLabelText("max_connections");
  return { ...harness, backend };
}

describe("an engine's settings", () => {
  it("shows each setting with what the engine uses now, the advice for this server and what changing it costs", async () => {
    await settingsAt();
    expect(screen.getByText("/etc/postgresql/16/main/conf.d/90-noust.conf")).toBeInTheDocument();
    expect(screen.getByText(/^Advice for 2(\.0)? Gi?B of memory and 2 processors$/)).toBeInTheDocument();
    const buffers = screen.getByLabelText("shared_buffers");
    expect(buffers).toHaveValue("");
    expect(buffers).toHaveAccessibleDescription(/Now 128MB\. Recommended for this server: 512MB\./);
    expect(buffers).toHaveAccessibleDescription(/Changing it restarts PostgreSQL\./);
    // What Noust's file already sets is the field's value, its unit after it.
    expect(screen.getByLabelText("log_min_duration_statement")).toHaveValue("500");
    expect(screen.getByText("ms")).toBeInTheDocument();
    // A setting Noust does not change is read only and says why.
    const port = screen.getByLabelText("port");
    expect(port).toHaveAttribute("readonly");
    expect(port).toHaveAccessibleDescription(/Noust's own client reaches the server on its default port\./);
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("sends only what changed, an emptied setting as default, and says how it was applied", async () => {
    const { user, backend } = await settingsAt({ [ROUTE]: () => json(200, OUTCOME) });
    await user.type(screen.getByLabelText("max_connections"), "150");
    await user.click(screen.getByRole("button", { name: "Use recommended for shared_buffers" }));
    expect(screen.getByLabelText("shared_buffers")).toHaveValue("512MB");
    await user.clear(screen.getByLabelText("log_min_duration_statement"));
    expect(screen.getByText("3 unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(ROUTE)).toHaveLength(1));
    expect(backend.callsTo(ROUTE)[0]?.body).toEqual({
      values: { max_connections: "150", shared_buffers: "512MB", log_min_duration_statement: "default" },
      confirm: false,
      confirm_exposure: false,
    });
    expect(await screen.findByText("Saved, and PostgreSQL restarted with the new settings.")).toBeInTheDocument();
    await waitFor(() => expect(backend.callsTo("GET /api/databases/engines/postgresql/settings").length).toBeGreaterThan(1));
  });

  it("lists every warning of a change that costs something, in the server's own words, then sends the confirmation", async () => {
    const warning = "PostgreSQL will accept connections from the network on 0.0.0.0. Anyone who can reach this server can try to sign in.";
    const second = "max_connections above 500 uses memory for every connection.";
    const { user, backend } = await settingsAt({
      [ROUTE]: (call) =>
        (call.body as { confirm: boolean }).confirm
          ? json(200, { ...OUTCOME, changed: ["listen_addresses"], exposed: true })
          : json(400, {
              error: "confirmation_required",
              detail: "This change needs your confirmation: 2 things to know",
              hint: "Confirm it to go ahead.",
              fields: { confirm: "This change needs your confirmation: 2 things to know" },
              output: null,
              warnings: [warning, second],
            }),
    });
    await user.type(screen.getByLabelText("listen_addresses"), "0.0.0.0");
    await user.click(screen.getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Change PostgreSQL knowing what it costs" });
    expect(within(dialog).getByText(warning)).toBeInTheDocument();
    expect(within(dialog).getByText(second)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Apply anyway" }));
    await waitFor(() => expect(backend.callsTo(ROUTE)).toHaveLength(2));
    expect(backend.callsTo(ROUTE)[1]?.body).toEqual({ values: { listen_addresses: "0.0.0.0" }, confirm: true, confirm_exposure: false });
    expect(await screen.findByText("PostgreSQL now listens beyond this server. Reach it through an SSH tunnel where you can.")).toBeInTheDocument();
  });

  it("shows a refused value under its field, and a failed apply with the engine's own words and the fix above them", async () => {
    const journal = "FATAL:  invalid value for parameter \"shared_buffers\": \"99999GB\"\npostgresql@16-main.service: Failed with result 'exit-code'.";
    let refuse = true;
    const { user } = await settingsAt({
      [ROUTE]: () =>
        refuse
          ? problem(400, "validation_error", "max_connections is at least 10", { fields: { max_connections: "max_connections is at least 10" } })
          : problem(500, "databaseengineerror", "PostgreSQL did not answer, so the previous settings were put back", { hint: "Read the journal below.", output: journal }),
    });
    await user.type(screen.getByLabelText("max_connections"), "5");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("max_connections is at least 10")).toBeInTheDocument();
    expect(screen.getByLabelText("max_connections")).toHaveAttribute("aria-invalid", "true");

    refuse = false;
    await user.clear(screen.getByLabelText("max_connections"));
    await user.type(screen.getByLabelText("max_connections"), "200");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Could not change the settings of PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("Read the journal below.")).toBeInTheDocument();
    expect(screen.getByText(/invalid value for parameter "shared_buffers"/)).toBeInTheDocument();
  });

  it("says why a container's engine is not configured here, in the server's words", async () => {
    screenWidth(1440);
    fakeBackend(
      databaseRoutes({
        "GET /api/databases/engines/postgresql%40proggest.postgres/settings": () =>
          problem(400, "databaseengineerror", "Noust does not configure an engine that runs in a container", { hint: "Its image decides its settings." }),
      }),
    );
    renderConsole("/databases/engines/postgresql@proggest.postgres/settings");
    expect(await screen.findByText("Noust does not configure an engine that runs in a container")).toBeInTheDocument();
    expect(screen.getByText("Its image decides its settings.")).toBeInTheDocument();
  });
});
