import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { createQueryClient } from "../../../app/App";
import { expectNoAxeViolations } from "../../../test/axe";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import { ENGINES } from "../testFixtures";
import type { CreateAppBody } from "../../new-app/wizard";
import { DatabaseStep, NO_DATABASE, withDatabase, withDatabaseStep } from "./DatabaseStep";
import type { DatabaseChoice } from "./DatabaseStep";

function Harness() {
  const [value, setValue] = useState<DatabaseChoice>(NO_DATABASE);
  return <DatabaseStep domain="shop.example.com" value={value} onChange={setValue} />;
}

describe("the wizard's Database step", () => {
  it("sits before Deploy, only when asked", () => {
    expect(withDatabaseStep(["source", "address", "variables", "deploy"], true)).toEqual(["source", "address", "variables", "database", "deploy"]);
    expect(withDatabaseStep(["source", "deploy"], false)).toEqual(["source", "deploy"]);
  });

  it("goes with the create request, so the first build already has it", () => {
    const body: CreateAppBody = {
      domain: "shop.example.com",
      source: "https://github.com/you/shop",
      app_type: "nextjs",
      webserver: "nginx",
      ssl: true,
      include_www: false,
      env_vars: { DATABASE_URL: "x", DB_HOST: "db", NEXTAUTH_SECRET: "s" },
      skip_database: false,
    };
    expect(withDatabase(body, NO_DATABASE)).toBe(body);
    // What the database writes is its own: a placeholder the Variables step asked for is left out,
    // since the server refuses a variable given twice.
    expect(withDatabase(body, { engine: "postgresql", extraVars: false })).toEqual({
      ...body,
      env_vars: { DB_HOST: "db", NEXTAUTH_SECRET: "s" },
      database: { engine: "postgresql", extra_vars: false },
    });
    expect(withDatabase(body, { engine: "postgresql", extraVars: true }).env_vars).toEqual({ NEXTAUTH_SECRET: "s" });
    expect(withDatabase(body, { engine: "redis", extraVars: false })).toEqual({
      ...body,
      env_vars: { DATABASE_URL: "x", DB_HOST: "db", NEXTAUTH_SECRET: "s" },
      database: { engine: "redis", extra_vars: false },
    });
  });

  it("offers none or a new database on each running engine, and says what will be written", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/databases/engines": () => json(200, { engines: ENGINES }),
      "GET /api/databases/provisioning/plan": () =>
        json(200, { engine: "postgresql", display_name: "PostgreSQL", installed: true, running: true, database: "shop_example_com_db", username: "shop_example_com", env_vars: ["DATABASE_URL"], url: "postgresql://shop_example_com:********@localhost:5432/shop_example_com_db", database_exists: false }),
    });
    const { container } = render(
      <QueryClientProvider client={createQueryClient()}>
        <Harness />
      </QueryClientProvider>,
    );
    const none = await screen.findByRole("radio", { name: "No database" });
    expect(none).toBeChecked();
    expect(screen.getByRole("radio", { name: /New PostgreSQL database/ })).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Redis/ })).toBeNull();
    expect(screen.getByText(/Not running here: Redis, MongoDB/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("radio", { name: /New PostgreSQL database/ }));
    await waitFor(() => expect(screen.getByText("shop_example_com_db")).toBeInTheDocument());
    expect(screen.getByText("Created before the first build")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
