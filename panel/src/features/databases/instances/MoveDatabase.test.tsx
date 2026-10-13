import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { CONTAINER_ENGINE, ENGINES, databaseRoutes } from "../testFixtures";

const PLAN = {
  source: "postgresql",
  target: "postgresql@proggest.postgres",
  database: "example_production",
  engine: "postgresql",
  source_version: "16.4",
  target_version: "17.2",
  size: "48 MB",
  exists_on_target: false,
  apps: ["docs.example.com"],
  accounts: ["example_user", "reporter"],
  new_passwords: ["reporter"],
  policies: [],
  blockers: [],
  warnings: ["Noust does not know the password of reporter: the target gets a new one, which Noust keeps."],
};

const JOB = { id: "mv1", type: "database", name: "Move", description: "", status: "running", progress: 10, total_steps: 100, current_step: "Dumping example_production", created_at: "2026-10-06T10:00:00", logs: [], metadata: { engine: "postgresql", database: "example_production" } };
const ACCEPTED = { job_id: "mv1", status: "pending", message: "queued", job: JOB };

async function pageAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/databases/engines": () => json(200, { engines: [...ENGINES, { ...CONTAINER_ENGINE, engine_type: "postgresql" }] }),
      "POST /api/databases/moves/plan": () => json(200, PLAN),
      "GET /api/jobs/mv1": () => json(200, JOB),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production");
  await screen.findByRole("heading", { level: 1, name: "example_production" }, { timeout: 15_000 });
  return { ...harness, backend };
}

async function openMove(user: ReturnType<typeof renderConsole>["user"]) {
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Move to another engine…" }));
  return screen.findByRole("dialog", { name: "Move example_production to another engine" });
}

describe("moving a database to another engine", { timeout: 60_000 }, () => {
  it("offers the other running engines of its kind and shows what the move would do", async () => {
    const { user } = await pageAt();

    const dialog = await openMove(user);

    expect(await within(dialog).findByText("docs.example.com")).toBeInTheDocument();
    expect(within(dialog).getByText("example_user, reporter")).toBeInTheDocument();
    expect(within(dialog).getByText(/the target gets a new one, which Noust keeps/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move it" })).toBeEnabled();
    await expectNoAxeViolations(dialog);
  });

  it("queues the move with the target, and the replace and drop choices it was given", async () => {
    const { backend, user } = await pageAt({ "POST /api/databases/moves": () => json(202, ACCEPTED) });

    const dialog = await openMove(user);
    await within(dialog).findByText("docs.example.com");
    await user.click(within(dialog).getByRole("checkbox", { name: /Drop it from here afterwards/ }));
    await user.click(within(dialog).getByRole("button", { name: "Move it" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/moves")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/moves")[0]?.body).toEqual({
      source: "postgresql",
      database: "example_production",
      target: "postgresql@proggest.postgres",
      replace: false,
      drop_source: true,
    });
    expect(await screen.findByText(/Moving example_production to another engine/)).toBeInTheDocument();
  });

  it("says what stops it, and does not offer to go on", async () => {
    const { user } = await pageAt({
      "POST /api/databases/moves/plan": () => json(200, { ...PLAN, blockers: ["postgresql@proggest.postgres already has a database called example_production"] }),
    });

    const dialog = await openMove(user);

    expect(await within(dialog).findByText("This move cannot go ahead")).toBeInTheDocument();
    expect(within(dialog).getByText(/already has a database called example_production/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move it" })).toBeDisabled();
  });

  it("says when there is no other engine to move it to", async () => {
    const { user } = await pageAt({ "GET /api/databases/engines": () => json(200, { engines: ENGINES }) });

    const dialog = await openMove(user);

    expect(await within(dialog).findByText("There is no other engine to move it to")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move it" })).toBeDisabled();
  });
});
