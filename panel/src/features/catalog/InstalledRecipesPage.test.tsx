import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { catalogueRoutes, installed } from "./testFixtures";

/**
 * A screen as wide as a desktop's: tables are tables, not the phone's card rows. And a layout
 * for the log viewer, which sizes its window from it.
 */
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const BEHIND = installed();
const CURRENT = installed({
  domain: "rss.example.com",
  recipe: "freshrss",
  installed_version: "1.30.1",
  latest_version: "1.30.1",
  update_available: false,
  changes: [],
});
const GONE = installed({
  domain: "old.example.com",
  recipe: "retired",
  installed_version: "0.9.0",
  latest_version: null,
  latest_revision: null,
  update_available: false,
  reason: "The recipe is no longer in the catalogue.",
  changes: [],
});

function job(patch: Record<string, unknown> = {}) {
  return {
    id: "9f3c0de1",
    type: "update",
    name: "Update vault.example.com to its recipe's latest version",
    description: "Updating vault.example.com (vaultwarden)",
    status: "running",
    progress: 10,
    total_steps: 100,
    current_step: "Pulling the images",
    created_at: "2026-10-06T10:21:13",
    logs: [{ timestamp: "2026-10-06T10:21:14", level: "INFO", message: "Copying the application first" }],
    metadata: { domain: "vault.example.com", recipe: "vaultwarden" },
    ...patch,
  };
}

function installedPage(items: unknown[], extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...catalogueRoutes(),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/recipes/installed": () => json(200, { items }),
    ...extra,
  });
  return { backend, harness: renderConsole("/catalog/installed") };
}

describe("the installed recipes page", () => {
  it("lists what was installed from a recipe against the catalog, and what can be updated", { timeout: 20_000 }, async () => {
    const { harness } = installedPage([BEHIND, CURRENT, GONE]);
    expect(await screen.findByRole("heading", { level: 1, name: "Catalog" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Applications installed from a recipe" });

    const behind = within(table).getByRole("row", { name: /vault\.example\.com/ });
    expect(within(behind).getByText("Update available")).toBeInTheDocument();
    expect(within(behind).getByText("v1.36.0")).toBeInTheDocument();
    expect(within(behind).getByText("v1.37.4")).toBeInTheDocument();
    expect(within(behind).getByText("Pinned to the 1.37.4 image.")).toBeInTheDocument();
    expect(within(behind).getByRole("button", { name: "Update vault.example.com" })).toBeEnabled();
    expect(within(behind).getByRole("link", { name: "vault.example.com" })).toHaveAttribute("href", "/apps/vault.example.com");

    const current = within(table).getByRole("row", { name: /rss\.example\.com/ });
    expect(within(current).getByText("Up to date")).toBeInTheDocument();
    expect(within(current).queryByRole("button")).not.toBeInTheDocument();

    // A recipe the catalog no longer has cannot move, and says why in the server's words.
    const gone = within(table).getByRole("row", { name: /old\.example\.com/ });
    expect(within(gone).getByText("Cannot be updated")).toBeInTheDocument();
    expect(within(gone).getByText("The recipe is no longer in the catalogue.")).toBeInTheDocument();
    expect(within(gone).queryByRole("button")).not.toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("says what is installed from where, and the way in, when nothing is", { timeout: 20_000 }, async () => {
    const { harness } = installedPage([]);
    expect(await screen.findByText("Nothing installed from a recipe yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toHaveAttribute("href", "/catalog");
    expect(screen.getByText(/noust recipe install vaultwarden/)).toBeInTheDocument();
    await expectNoAxeViolations(harness.container);
  });

  it("shows how many applications each tab holds", { timeout: 20_000 }, async () => {
    installedPage([BEHIND, CURRENT]);
    const tabs = await screen.findByRole("navigation", { name: "Catalog sections" });
    await waitFor(() => expect(within(tabs).getByRole("link", { name: /Installed recipes/ })).toHaveTextContent("2"));
    expect(within(tabs).getByRole("link", { name: /Recipes/ })).toHaveTextContent("25");
  });

  it("updates through the one update: it says what changed and what happens when it fails, queues a job and follows it", { timeout: 30_000 }, async () => {
    let queued = false;
    let finished = false;
    const { backend, harness } = installedPage([BEHIND], {
      "GET /api/recipes/installed": () => json(200, { items: finished ? [{ ...BEHIND, installed_version: "1.37.4", update_available: false, changes: [] }] : [BEHIND] }),
      "POST /api/recipes/installed/vault.example.com/update": () => {
        queued = true;
        return json(202, { job_id: "9f3c0de1", status: "pending", message: "Update of vault.example.com queued", job: job() });
      },
      "GET /api/jobs/9f3c0de1": () => {
        if (queued) finished = true;
        return json(200, job(finished ? { status: "completed", progress: 100, current_step: "" } : {}));
      },
    });
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Update vault.example.com" }));

    const dialog = await screen.findByRole("dialog", { name: "Update vault.example.com" });
    expect(within(dialog).getByText("From v1.36.0 to v1.37.4, the version the catalog has now.")).toBeInTheDocument();
    expect(within(dialog).getByText("Pinned to the 1.37.4 image.")).toBeInTheDocument();
    // What the one update does when the new version does not start.
    expect(within(dialog).getByText("It goes back when it does not start")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Update" }));
    // Nothing is asked of a recipe that declares no new variable: the body carries none.
    await waitFor(() => expect(backend.callsTo("POST /api/recipes/installed/vault.example.com/update")).toHaveLength(1));
    expect(backend.callsTo("POST /api/recipes/installed/vault.example.com/update")[0]?.body).toEqual({ variables: {}, force: false });

    // The job is in hand where it was started, with its output a click away.
    expect(await screen.findByText(/Updating vault\.example\.com|vault\.example\.com is up to date/)).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "View output" }));
    const output = await screen.findByRole("dialog", { name: "Updating vault.example.com" });
    expect(await within(output).findByText("Copying the application first")).toBeInTheDocument();
  });

  it("asks only for a variable the new version needs and the application may lack, never resetting the others", { timeout: 30_000 }, async () => {
    const fresh = installed({ domain: "rss.example.com", recipe: "freshrss", installed_version: "1.29.0", latest_version: "1.30.1", changes: [{ version: "1.30.1", summary: "Asks for an administrator's e-mail." }] });
    const { backend, harness } = installedPage([fresh], {
      "POST /api/recipes/installed/rss.example.com/update": () => json(202, { job_id: "9f3c0de1", status: "pending", message: "Queued", job: job({ metadata: { domain: "rss.example.com" } }) }),
      "GET /api/jobs/9f3c0de1": () => json(200, job()),
    });
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Update rss.example.com" }));
    const dialog = await screen.findByRole("dialog", { name: "Update rss.example.com" });
    const field = await within(dialog).findByLabelText(/ADMIN_EMAIL/);
    expect(within(dialog).getByText("The e-mail address of the administrator account.")).toBeInTheDocument();
    // The optional ones the recipe has defaults for are not asked at all.
    expect(within(dialog).queryByLabelText(/ADMIN_USER/)).not.toBeInTheDocument();
    await user.type(field, "ops@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Update" }));
    await waitFor(() => expect(backend.callsTo("POST /api/recipes/installed/rss.example.com/update")).toHaveLength(1));
    expect(backend.callsTo("POST /api/recipes/installed/rss.example.com/update")[0]?.body).toEqual({ variables: { ADMIN_EMAIL: "ops@example.com" }, force: false });
  });

  it("keeps the dialog open and shows a refusal verbatim", { timeout: 20_000 }, async () => {
    const { harness } = installedPage([BEHIND], {
      "POST /api/recipes/installed/vault.example.com/update": () =>
        problem(400, "recipeerror", "ADMIN_TOKEN is required and has no value", { hint: "Give it with --var ADMIN_TOKEN=..." }),
    });
    const { user } = harness;
    await user.click(await screen.findByRole("button", { name: "Update vault.example.com" }));
    const dialog = await screen.findByRole("dialog", { name: "Update vault.example.com" });
    await user.click(within(dialog).getByRole("button", { name: "Update" }));
    expect(await within(dialog).findByText("vault.example.com was not updated")).toBeInTheDocument();
    expect(within(dialog).getByText("ADMIN_TOKEN is required and has no value")).toBeInTheDocument();
    expect(within(dialog).getByText("Give it with --var ADMIN_TOKEN=...")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Update vault.example.com" })).toBeInTheDocument();
  });
});
