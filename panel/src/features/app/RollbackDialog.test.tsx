import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { TAB_DOMAIN, appRoutes, screenWidth } from "./testRoutes";

function deploy(id: number, extra: Record<string, unknown> = {}) {
  return {
    id,
    domain: TAB_DOMAIN,
    status: "success",
    triggered_by: "webhook",
    git_commit: `c0ffe${String(id).padStart(2, "0")}`,
    git_branch: "main",
    started_at: `2026-09-${String(id).padStart(2, "0")}T18:00:00`,
    finished_at: `2026-09-${String(id).padStart(2, "0")}T18:00:47`,
    duration_s: 47,
    error: null,
    has_log: true,
    commit_message: `Change ${String(id)}`,
    rollback_available: false,
    ...extra,
  };
}

/** #25 serves; #24 and #23 can be gone back to. */
const HISTORY = [
  deploy(25),
  deploy(24, { rollback_available: true, rollback_method: "instant", commit_message: "Add gift cards" }),
  deploy(23, { rollback_available: true, rollback_method: "instant" }),
];

const ROLLOUT = {
  domain: TAB_DOMAIN,
  history_keep: null,
  history_keep_server: 20,
  history_keep_effective: 20,
  stop_timeout: null,
  stop_signal: null,
  stop_supported: true,
  pause_on_rollback: true,
  paused: null,
  held: 0,
  automatic: [],
  freeze: null,
};

const JOB = {
  id: "5ca1ab1e",
  type: "rollback",
  name: "",
  description: "",
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:00:00",
  logs: [],
  metadata: { domain: TAB_DOMAIN },
};

async function openDialog(items: unknown[], extra: Record<string, RouteHandler> = {}, app: Record<string, unknown> = { layout: "releases" }) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(app, {
      "GET /api/deployments": () => json(200, { items, total: items.length, next_before_id: null }),
      [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () => json(200, ROLLOUT),
      [`GET /api/apps/${TAB_DOMAIN}/rollback-points`]: () => json(200, { items: [], total: 0 }),
      ...extra,
    }),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  await harness.user.click(await screen.findByRole("button", { name: /^(Roll back|Volver atrás)$/ }));
  const dialog = await screen.findByRole("alertdialog", { name: /^(Roll back|Volver atrás) / });
  return { ...harness, backend, dialog };
}

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("the question of going back", { timeout: 20_000 }, () => {
  it("opens on the version before the one that serves, and says what goes back and what stays", async () => {
    const { dialog } = await openDialog(HISTORY);
    expect(await within(dialog).findByRole("button", { name: "Go back to #24" })).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "Version to go back to" })).toHaveTextContent("#24 Add gift cards");
    expect(within(dialog).getByText("What goes back")).toBeInTheDocument();
    // From data: the version and its commit, and the one that keeps serving if the startup check refuses it.
    expect(dialog).toHaveTextContent("The code and the version: #24 (c0ffe24). It starts behind a startup check; if the check fails, #25 keeps serving.");
    expect(within(dialog).getByText("What stays as it is")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Environment variables as they are now, the database (Noust puts code back, never data), domains and certificates.");
  });

  it("is a question with one answer: Cancel first, then the action", async () => {
    const { dialog } = await openDialog(HISTORY);
    const buttons = within(dialog).getAllByRole("button");
    expect(buttons.at(-2)).toHaveAccessibleName("Cancel");
    expect(buttons.at(-1)).toHaveAccessibleName("Go back to #24");
    expect(buttons.at(-1)).toHaveAttribute("data-variant", "primary");
  });

  it("says that automatic deploys pause, with the way to the setting, while they are not paused already", async () => {
    const { dialog } = await openDialog(HISTORY);
    expect(await within(dialog).findByText("Automatic deploys")).toBeInTheDocument();
    expect(within(dialog).getByText(/They pause until you resume them\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Automatic deploys settings" })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/settings/deploys`);
  });

  it("does not say it when the app is set to keep deploying after a rollback, or they are paused already", async () => {
    const off = await openDialog(HISTORY, { [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () => json(200, { ...ROLLOUT, pause_on_rollback: false }) });
    await within(off.dialog).findByRole("button", { name: "Go back to #24" });
    expect(within(off.dialog).queryByText("Automatic deploys")).not.toBeInTheDocument();
  });

  it("does not say it when they are paused already", async () => {
    const { dialog } = await openDialog(HISTORY, {
      [`GET /api/apps/${TAB_DOMAIN}/rollout`]: () => json(200, { ...ROLLOUT, paused: { at: "2026-10-13T10:00:00+00:00", by: "yago", reason: null } }),
    });
    await within(dialog).findByRole("button", { name: "Go back to #24" });
    expect(within(dialog).queryByText("Automatic deploys")).not.toBeInTheDocument();
  });

  it("changes what it says with the version chosen", async () => {
    const { user, dialog } = await openDialog(HISTORY);
    await user.click(await within(dialog).findByRole("combobox", { name: "Version to go back to" }));
    await user.click(await screen.findByRole("option", { name: /^#23/ }));
    expect(within(dialog).getByRole("button", { name: "Go back to #23" })).toBeInTheDocument();
    expect(dialog).toHaveTextContent("The code and the version: #23 (c0ffe23)");
  });

  it("says what a rebuild does when the version is built again where the app runs", async () => {
    const { dialog } = await openDialog([deploy(25), deploy(24, { rollback_available: true, rollback_method: "rebuild" })], {}, { layout: "inplace" });
    await within(dialog).findByRole("button", { name: "Go back to #24" });
    expect(dialog).toHaveTextContent("Commit c0ffe24 is built again where the app runs, behind a startup check.");
  });

  it("says that a snapshot is restored after a backup of how the app is now", async () => {
    const { dialog } = await openDialog([deploy(25), deploy(24, { rollback_available: true, rollback_method: "snapshot", snapshot_backup: "app_20260920_101500" })], {}, { layout: "inplace" });
    await within(dialog).findByRole("button", { name: "Go back to #24" });
    expect(dialog).toHaveTextContent("The app's files from backup app_20260920_101500, which holds what #24 (c0ffe24) produced. A backup of how it is now is taken first");
  });

  it("goes back by the deployment's own endpoint, and the header follows the job", async () => {
    const { user, dialog, backend } = await openDialog(HISTORY, {
      [`POST /api/apps/${TAB_DOMAIN}/deployments/24/rollback`]: () => json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB }),
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });
    await user.click(await within(dialog).findByRole("button", { name: "Go back to #24" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/24/rollback`)[0]?.body).toEqual({ schema_changed_ok: false });
    });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` })).not.toBeInTheDocument();
    });
    expect(await screen.findByText(`Rolling ${TAB_DOMAIN} back`)).toBeInTheDocument();
  });

  it("keeps the question open with the system's own words when the rollback is refused", async () => {
    const { user, dialog } = await openDialog(HISTORY, {
      [`POST /api/apps/${TAB_DOMAIN}/deployments/24/rollback`]: () => problem(409, "rollback_unavailable", "Release 20260924-194023-19d3f6e is no longer on disk", { hint: "Rebuild its commit instead" }),
    });
    await user.click(await within(dialog).findByRole("button", { name: "Go back to #24" }));
    expect(await within(dialog).findByText("Release 20260924-194023-19d3f6e is no longer on disk")).toBeInTheDocument();
    expect(within(dialog).getByText("Rebuild its commit instead")).toBeInTheDocument();
  });

  it("says there is nothing to go back to, and where backups are, for an app kept in one folder", async () => {
    const { dialog } = await openDialog([deploy(25)], {}, { layout: "inplace" });
    expect(await within(dialog).findByText(/There is nothing to go back to yet\. A backup of this app can be restored from Backups\./)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Backups" })).toHaveAttribute("href", "/backups");
    expect(within(dialog).getByRole("button", { name: "Go back" })).toBeDisabled();
  });

  it("says there is nothing to go back to yet for an app with releases", async () => {
    const { dialog } = await openDialog([deploy(25)]);
    expect(await within(dialog).findByText(/There is nothing to go back to yet\. Each update keeps the version before it/)).toBeInTheDocument();
  });

  it("lists an in-place app's backups after its deployments, and goes back to one as a job", async () => {
    const { user, dialog, backend } = await openDialog(
      [deploy(25)],
      {
        [`GET /api/apps/${TAB_DOMAIN}/rollback-points`]: () =>
          json(200, { items: [{ id: "shop-example-com_20260923_182035", created_at: "2026-09-23T18:20:35", description: "Nightly backup", size_bytes: 2048, git_commit: "9f2c41a" }], total: 1 }),
        "POST /api/jobs/rollback": () => json(202, { message: "queued", job: JOB }),
      },
      { layout: "inplace" },
    );
    await within(dialog).findByRole("combobox", { name: "Version to go back to" });
    expect(dialog).toHaveTextContent("The app's files from backup shop-example-com_20260923_182035, then the app is rebuilt and restarted.");
    await user.click(within(dialog).getByRole("button", { name: "Go back to this backup" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/rollback")[0]?.body).toEqual({ domain: TAB_DOMAIN, backup_id: "shop-example-com_20260923_182035", schema_changed_ok: false });
    });
  });

  it("has no accessibility violations, and Escape leaves it without asking anything", async () => {
    const { user, dialog, backend } = await openDialog(HISTORY);
    await within(dialog).findByRole("button", { name: "Go back to #24" });
    await expectNoAxeViolations(dialog);
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog", { name: `Roll back ${TAB_DOMAIN}` })).not.toBeInTheDocument();
    });
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/24/rollback`)).toHaveLength(0);
  });
});

describe("the question of going back, in Spanish", { timeout: 20_000 }, () => {
  it("is said in Spanish", async () => {
    await act(() => setLocale("es"));
    const { dialog } = await openDialog(HISTORY);
    expect(await within(dialog).findByRole("button", { name: "Volver a #24" })).toBeInTheDocument();
    expect(within(dialog).getByText("Qué vuelve")).toBeInTheDocument();
    expect(within(dialog).getByText("Qué se queda como está")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("El código y la versión: #24 (c0ffe24)");
    await expectNoAxeViolations(dialog);
  });
});
