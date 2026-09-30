import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { OVERVIEW, databaseRoutes } from "../testFixtures";

describe("a database's accounts", () => {
  it("lists them with their access, hides the engine's own, and shows a new one's password once", async () => {
    screenWidth(1440);
    const backend = fakeBackend(
      databaseRoutes({
        "GET /api/databases/databases/postgresql/example_production/access": () => json(200, { access: OVERVIEW.access }),
        "POST /api/databases/users": () => json(200, { username: "reports", password: "Gen-3rat3d-once", message: "created" }),
      }),
    );
    const { user } = renderConsole("/databases/postgresql/example_production/users");
    const table = await screen.findByRole("region", { name: "Accounts of example_production" });
    expect(await within(table).findByText("example_production")).toBeInTheDocument();
    expect(within(table).getByText("Read and write")).toBeInTheDocument();
    expect(within(table).queryByText("postgres")).toBeNull();
    await user.click(screen.getByRole("switch", { name: "Show 1 system account" }));
    expect(within(table).getByText("postgres")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "New user" }));
    const dialog = await screen.findByRole("dialog", { name: "New user" });
    await user.type(within(dialog).getByLabelText("Username"), "reports");
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/users")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/users")[0]?.body).toEqual({
      engine: "postgresql",
      username: "reports",
      password: null,
      database: "example_production",
      host: "localhost",
      profile: "read_write",
    });
    const shown = await screen.findByRole("dialog", { name: "reports created" });
    expect(within(shown).getByDisplayValue("Gen-3rat3d-once")).toBeInTheDocument();
  });
});
