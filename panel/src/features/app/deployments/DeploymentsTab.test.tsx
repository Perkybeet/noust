import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

function deploy(id: number, status = "success", extra: Record<string, unknown> = {}) {
  return {
    id,
    domain: TAB_DOMAIN,
    status,
    triggered_by: id % 2 ? "webhook" : "cli",
    git_commit: `c0ffe${String(id).padStart(2, "0")}`,
    git_branch: "main",
    started_at: "2026-09-25T18:00:00",
    finished_at: "2026-09-25T18:00:47",
    duration_s: 47,
    error: null,
    has_log: true,
    rollback_available: false,
    ...extra,
  };
}

/** Thirteen deploys, newest first: #25 serves, #20 failed, #24 can be gone back to. */
const HISTORY = Array.from({ length: 13 }, (_, i) =>
  deploy(25 - i, i === 5 ? "failed" : "success", {
    ...(i === 0 ? { commit_message: "Redesign checkout summary" } : {}),
    ...(i === 1 ? { rollback_available: true, rollback_method: "instant", commit_message: "Add gift cards", release_id: "20260924-194023-19d3f6e" } : {}),
  }),
);

const history: RouteHandler = () => json(200, { items: HISTORY, total: HISTORY.length, next_before_id: null });

const RELEASES = {
  domain: TAB_DOMAIN,
  items: [
    { id: "20260925-184247-2a8b7c4", commit: "2a8b7c4", created_at: "2026-09-25T18:42:47+00:00", activated_at: "2026-09-25T18:43:30+00:00", status: "active", active: true, on_disk: true, waiting: false },
    { id: "20260924-194023-19d3f6e", commit: "19d3f6e", created_at: "2026-09-24T19:40:23+00:00", activated_at: null, status: "superseded", active: false, on_disk: true, waiting: false },
  ],
  total: 2,
};

const JOB = {
  id: "0a1b2c3d",
  type: "update",
  name: "Rebuild",
  description: "",
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:00:00",
  logs: [],
  metadata: { domain: TAB_DOMAIN },
};

async function tabAt(app: Record<string, unknown>, extra: Record<string, RouteHandler> = {}, width = 1440, path = `/apps/${TAB_DOMAIN}/deployments`) {
  screenWidth(width);
  const backend = fakeBackend(appRoutes(app, { "GET /api/deployments": history, ...extra }));
  const harness = renderConsole(path);
  // The list's own filters: there once the history is read. In Spanish the same landmark has another name.
  await screen.findByRole("search", { name: /^(Filter deployments|Filtrar despliegues)$/ });
  return { ...harness, backend };
}

function rowOf(table: HTMLElement, id: number): HTMLElement {
  const row = within(table).getByRole("button", { name: `Deployment ${String(id)}` }).closest("tr");
  if (row === null) throw new Error(`no row for deployment ${String(id)}`);
  return row;
}

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("the deployments tab", { timeout: 20_000 }, () => {
  it("lists every deploy newest first in one list, each linking to its page, the one that serves marked Live", async () => {
    await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 25" });
    expect(within(table).getAllByRole("row")).toHaveLength(14);
    expect(within(table).getByRole("button", { name: "Deployment 25" })).toBeInTheDocument();
    // Live in words and a shape, once; the other successes are Succeeded.
    expect(within(rowOf(table, 25)).getByText("Live")).toHaveAttribute("data-state", "running");
    expect(within(table).getAllByText("Live")).toHaveLength(1);
    expect(within(rowOf(table, 24)).getByText("Succeeded")).toBeInTheDocument();
    // A failure says which version kept serving through it.
    expect(within(rowOf(table, 20)).getByText("Failed")).toBeInTheDocument();
    expect(within(rowOf(table, 20)).getByText("Version #19 kept serving")).toBeInTheDocument();
    // The commit's own subject line, beside its hash, truncated but reachable in full on hover.
    expect(within(table).getByTitle("Redesign checkout summary")).toBeInTheDocument();
    expect(screen.getByText("13 deployments")).toBeInTheDocument();
    expect(screen.getByText("The history keeps the last 20 deployments.")).toBeInTheDocument();
  });

  it("has no second list of versions beside it", async () => {
    await tabAt({ layout: "releases" }, { [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, RELEASES) });
    await screen.findByRole("button", { name: "Deployment 25" });
    expect(screen.queryByRole("list", { name: `Versions of ${TAB_DOMAIN}, newest first` })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Versions" })).not.toBeInTheDocument();
  });

  it("draws a deploy an operator cancelled as cancelled, not as a failure", async () => {
    const rows = [deploy(3, "success"), deploy(2, "failed", { cancelled: true, cancelled_by: "master", error: null }), deploy(1, "failed", { error: "Type error" })];
    await tabAt({}, { "GET /api/deployments": () => json(200, { items: rows, total: 3, next_before_id: null }) });
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 3" });
    const cancelled = rowOf(table, 2);
    expect(within(cancelled).getByText("Cancelled")).toBeInTheDocument();
    expect(within(cancelled).queryByText("Failed")).not.toBeInTheDocument();
    // A real failure beside it is still one.
    expect(within(table).getAllByText("Failed")).toHaveLength(1);
  });

  it("draws each deploy as a card on a phone, its state above its number", async () => {
    await tabAt({}, {}, 390);
    const list = await screen.findByRole("list", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    const cards = await within(list).findAllByRole("listitem");
    expect(cards).toHaveLength(13);
    expect(within(cards[0] ?? list).getByRole("button", { name: "Deployment 25" })).toBeInTheDocument();
  });

  it("opens a deploy's page from its row, not only from its number", async () => {
    const { user, location } = await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await user.click(within(rowOf(table, 22)).getByText("Succeeded"));
    await waitFor(() => {
      expect(location().pathname).toBe(`/apps/${TAB_DOMAIN}/deployments/22`);
    });
  });

  it("says what the history is for when there is none yet", async () => {
    screenWidth(1440);
    fakeBackend(appRoutes({}, { "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }) }));
    renderConsole(`/apps/${TAB_DOMAIN}/deployments`);
    expect(await screen.findByRole("heading", { name: "No deploys recorded yet" })).toBeInTheDocument();
    expect(screen.queryByRole("search")).not.toBeInTheDocument();
  });
});

describe("filtering the deployments", { timeout: 20_000 }, () => {
  it("narrows by the text of a commit's message or its hash, and says how many of how many", async () => {
    const { user, location } = await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 25" });
    await user.type(screen.getByRole("searchbox", { name: "Search deployments" }), "gift");
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(2);
    });
    expect(within(table).getByRole("button", { name: "Deployment 24" })).toBeInTheDocument();
    expect(screen.getByText("1 of 13 deployments")).toBeInTheDocument();
    // It is in the address, so it can be shared.
    expect(location().search).toMatchObject({ q: "gift" });
  });

  it("narrows by state, and clears", async () => {
    const { user } = await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 25" });
    await user.click(screen.getByRole("combobox", { name: "State" }));
    await user.click(await screen.findByRole("option", { name: "Failed" }));
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(2);
    });
    expect(within(table).getByRole("button", { name: "Deployment 20" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(14);
    });
  });

  it("opens already filtered from an address", async () => {
    await tabAt({}, {}, 1440, `/apps/${TAB_DOMAIN}/deployments?state=live`);
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(2);
    });
    expect(within(table).getByRole("button", { name: "Deployment 25" })).toBeInTheDocument();
  });
});

describe("what a deployment's row can do", { timeout: 20_000 }, () => {
  it("opens its log, and offers going back to it only when the backend says it can", async () => {
    const { user, location } = await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 24" });

    await user.click(within(table).getByRole("button", { name: "Actions for #24" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["View log", "Roll back to this", "Rebuild this commit", "Copy commit"]);
    await user.keyboard("{Escape}");

    // #23 cannot be gone back to: no such entry, not one that fails when pressed.
    await user.click(within(table).getByRole("button", { name: "Actions for #23" }));
    const other = await screen.findByRole("menu");
    expect(within(other).queryByRole("menuitem", { name: "Roll back to this" })).not.toBeInTheDocument();
    await user.click(within(other).getByRole("menuitem", { name: "View log" }));
    await waitFor(() => {
      expect(location().pathname).toBe(`/apps/${TAB_DOMAIN}/deployments/23`);
    });
  });

  it("asks the page's one rollback question, opened on that deployment", async () => {
    const { user } = await tabAt({});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await user.click(await within(table).findByRole("button", { name: "Actions for #24" }));
    await user.click(await screen.findByRole("menuitem", { name: "Roll back to this" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` });
    expect(await within(dialog).findByRole("button", { name: "Go back to #24" })).toBeInTheDocument();
  });

  it("retries a failed deploy from its menu: its commit is built again, and nothing is asked first", async () => {
    const { user, backend } = await tabAt({}, { [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB }) });
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await user.click(await within(table).findByRole("button", { name: "Actions for #20" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["View log", "Retry", "Copy commit"]);
    await user.click(within(menu).getByRole("menuitem", { name: "Retry" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rebuild`)[0]?.body).toEqual({ no_cache: false });
    });
  });

  it("offers Update, not Retry, for a failed deploy that recorded no commit", async () => {
    const rows = [deploy(2, "success"), deploy(1, "failed", { git_commit: null, git_branch: null, error: "boom" })];
    const { user } = await tabAt({}, { "GET /api/deployments": () => json(200, { items: rows, total: 2, next_before_id: null }) });
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await user.click(await within(table).findByRole("button", { name: "Actions for #1" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["View log", "Update"]);
  });

  it("says what rebuilding a deploy's commit does before it does it", async () => {
    const { user, backend } = await tabAt({}, { [`POST /api/apps/${TAB_DOMAIN}/deployments/23/rebuild`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB }) });
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await user.click(await within(table).findByRole("button", { name: "Actions for #23" }));
    await user.click(await screen.findByRole("menuitem", { name: "Rebuild this commit" }));
    const dialog = await screen.findByRole("dialog", { name: "Rebuild commit c0ffe23?" });
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/23/rebuild`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Rebuild" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/23/rebuild`)).toHaveLength(1);
    });
  });
});

describe("an app with releases", { timeout: 20_000 }, () => {
  it("says a deploy whose version was pruned has nothing to go back to", async () => {
    const rows = [deploy(3, "success", { release_id: "20260925-184247-2a8b7c4" }), deploy(2, "success", { release_id: "20260916-182823-d08e4f7" })];
    await tabAt({ layout: "releases" }, { "GET /api/deployments": () => json(200, { items: rows, total: 2, next_before_id: null }), [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, RELEASES) });
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    await within(table).findByRole("button", { name: "Deployment 3" });
    await waitFor(() => {
      expect(within(rowOf(table, 2)).getByText("Removed from disk")).toBeInTheDocument();
    });
    expect(within(rowOf(table, 3)).queryByText("Removed from disk")).not.toBeInTheDocument();
  });

  it("marks a build that waits at the top, and makes it serve after asking, with nothing built again", async () => {
    const waiting = { id: "20260926-101500-7e7e7e7", commit: "7e7e7e7", created_at: "2026-09-26T10:15:00+00:00", activated_at: null, status: "built", active: false, on_disk: true, waiting: true };
    const { user, backend } = await tabAt(
      { layout: "releases" },
      {
        [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, { ...RELEASES, items: [waiting, ...RELEASES.items], total: 3 }),
        [`POST /api/apps/${TAB_DOMAIN}/releases/${waiting.id}/promote`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: { ...JOB, type: "promote" } }),
      },
    );
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    const built = (await within(table).findByText("Built, not serving")).closest("tr");
    if (built === null) throw new Error("no row for the build that waits");
    expect(within(built).getByText("7e7e7e7")).toBeInTheDocument();
    // Before every deploy that happened: it is the newest thing.
    expect(within(table).getAllByRole("row")[1]).toBe(built);

    await user.click(within(built).getByRole("button", { name: "Actions for Built, not serving" }));
    await user.click(await screen.findByRole("menuitem", { name: "Go live" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Go live" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/${waiting.id}/promote`)[0]?.body).toEqual({ schema_changed_ok: false, frozen_ok: false });
    });
  });
});

describe("an app kept in a single folder", { timeout: 20_000 }, () => {
  const POINTS = {
    items: [{ id: "shop-example-com_20260923_182035", created_at: "2026-09-23T18:20:35", description: "Nightly backup", size_bytes: 2048, git_commit: "9f2c41a" }],
    total: 1,
  };

  it("offers its backups under the list, and queues the rollback as a job after the page's one question", async () => {
    const { user, backend } = await tabAt(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/rollback-points`]: () => json(200, POINTS),
        "GET /api/deployments": () => json(200, { items: [deploy(1, "success")], total: 1, next_before_id: null }),
        "POST /api/jobs/rollback": () => json(202, { message: "Rollback job created", job: { ...JOB, type: "rollback" } }),
      },
    );
    const points = await screen.findByRole("list", { name: `Backups of ${TAB_DOMAIN}, newest first` });
    await user.click(within(points).getByRole("button", { name: /^Go back/ }));
    const dialog = await screen.findByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` });
    // Opened on the backup that was asked for, with what that does.
    expect(await within(dialog).findByRole("button", { name: "Go back to this backup" })).toBeInTheDocument();
    expect(within(dialog).getByText(/A backup of how it is now is taken first/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Go back to this backup" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/rollback")[0]?.body).toEqual({ domain: TAB_DOMAIN, backup_id: "shop-example-com_20260923_182035", schema_changed_ok: false });
    });
  });
});

describe("the deployments tab, read and heard", { timeout: 20_000 }, () => {
  it("has no accessibility violations", async () => {
    await tabAt({ layout: "releases" }, { [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, RELEASES) });
    await screen.findByRole("button", { name: "Deployment 25" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("lists the history in Spanish", async () => {
    await act(() => setLocale("es"));
    await tabAt({ layout: "releases" }, { [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, RELEASES) });
    const table = await screen.findByRole("region", { name: `Despliegues de ${TAB_DOMAIN}, los más recientes primero` });
    await within(table).findByRole("button", { name: "Despliegue 25" });
    expect(screen.getByText("13 despliegues")).toBeInTheDocument();
    expect(within(table).getByText("En producción")).toBeInTheDocument();
    expect(within(table).getByText("La versión #19 siguió sirviendo")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
