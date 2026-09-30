import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { ENGINES, OVERVIEW } from "../../databases/testFixtures";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const JOB = { id: "unlink-1", type: "database", name: "Unlink", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-09-29T10:00:00", logs: [{ message: "Restarting shop.example.com" }], metadata: { domain: TAB_DOMAIN } };

async function tabAt(links: unknown[], extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/databases`]: () => json(200, { domain: TAB_DOMAIN, databases: links }),
        "GET /api/databases/engines": () => json(200, { engines: ENGINES }),
        ...extra,
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/database`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

describe("an application's Database tab", () => {
  it("offers to create or link a database when it has none", async () => {
    await tabAt([]);
    expect(await screen.findByRole("heading", { level: 2, name: "No database yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create database" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link existing" })).toBeInTheDocument();
  });

  it("shows each linked database with its variable, and unlinks one as a job that restarts the app", async () => {
    const { user, backend } = await tabAt(OVERVIEW.links, {
      [`DELETE /api/apps/${TAB_DOMAIN}/databases/postgresql/example_production`]: () => json(202, { job_id: JOB.id, status: "pending", message: "Unlinking", job: JOB }),
      "GET /api/jobs/unlink-1": () => json(200, JOB),
    });
    const card = (await screen.findByRole("link", { name: "example_production" })).closest("li");
    if (!card) throw new Error("no card");
    expect(within(card).getByText("DATABASE_URL")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(within(card).getByRole("button", { name: "Unlink" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Unlink example_production" });
    expect(within(dialog).getByText(/DATABASE_URL is removed from shop\.example\.com's environment/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Unlink database" }));
    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${TAB_DOMAIN}/databases/postgresql/example_production`)).toHaveLength(1));
    expect(await screen.findByText("Unlinking example_production")).toBeInTheDocument();
  });
});
