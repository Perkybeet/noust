import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ApiError } from "../../../api/errors";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { backupBefore, restoreCommand, schemaChangeRefusal } from "../schemaChange";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

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
    rollback_available: true,
    schema_changed: false,
    ...extra,
  };
}

const MIGRATE = {
  phase: "pre_deploy",
  run: "/app/node_modules/.bin/prisma migrate deploy",
  service: "backend",
  migrates: true,
  ok: true,
  exit_code: 0,
  timed_out: false,
  duration_s: 4,
  output: "Applying migration `20260926_add_invoices`\nAll migrations have been successfully applied.",
  automatic: null,
};

const PURGE = { ...MIGRATE, phase: "post_deploy", run: "./scripts/purge-cache.sh", migrates: false, ok: false, exit_code: 1, output: "curl: (7) Failed to connect" };

const TARGET = deploy(20, { schema_changed_between: [22] });
const CHANGED = deploy(22, { schema_changed: true, hooks: [MIGRATE, PURGE], warnings: "post_deploy hook ./scripts/purge-cache.sh exited with 1" });

const REFUSAL = {
  error: "schema_changed",
  detail: `Going back to deployment 20 of ${TAB_DOMAIN} passes deployment 22, which changed the database schema`,
  hint: "Noust puts the code back, never the database.",
  fields: null,
  output: null,
  deployments: [22],
};

const POINTS = {
  items: [
    { id: "shop-example-com_20260923_120000", created_at: "2026-09-23T12:00:00", description: "Nightly backup", size_bytes: 2048, git_commit: "c0ffe20" },
    { id: "shop-example-com_20260922_175950", created_at: "2026-09-22T17:59:50", description: "Pre-update automatic backup", size_bytes: 2048, git_commit: "c0ffe20" },
    { id: "shop-example-com_20260921_120000", created_at: "2026-09-21T12:00:00", description: "Nightly backup", size_bytes: 2048, git_commit: "c0ffe20" },
  ],
  total: 3,
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

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/deployments": () => json(200, { items: [CHANGED, TARGET], total: 2, next_before_id: null }),
    "GET /api/deployments/20": () => json(200, TARGET),
    "GET /api/deployments/22": () => json(200, CHANGED),
    "GET /api/deployments/20/log": () => json(200, { content: "", truncated: false, missing_reason: null }),
    "GET /api/deployments/22/log": () => json(200, { content: "", truncated: false, missing_reason: null }),
    "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${TAB_DOMAIN}/rollback-points`]: () => json(200, POINTS),
    ...extra,
  };
}

async function open(path: string, app: Record<string, unknown>, extra: Record<string, RouteHandler>) {
  screenWidth(1440);
  const backend = fakeBackend(appRoutes(app, routes(extra)));
  const harness = renderConsole(`/apps/${TAB_DOMAIN}${path}`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

describe("schemaChangeRefusal", () => {
  it("reads the refusal of every way back, and nothing else", () => {
    const refusal = schemaChangeRefusal(new ApiError(409, "schema_changed", "passes 22", "restore", null, null, null, null, { deployments: [23, 22] }));
    expect(refusal).toEqual({ deployments: [22, 23], detail: "passes 22", hint: "restore" });
    // An answer that does not name them still opens the dialog, on the backend's own sentence.
    expect(schemaChangeRefusal(new ApiError(409, "schema_changed", "passes 4"))?.deployments).toEqual([]);
    // Any other conflict is not this one.
    expect(schemaChangeRefusal(new ApiError(409, "schemachangederror", "passes 4"))).toBeNull();
    expect(schemaChangeRefusal(new ApiError(409, "app_busy", "busy"))).toBeNull();
    expect(schemaChangeRefusal(new Error("schema_changed"))).toBeNull();
  });

  it("finds the backup taken before the first migration, and the command that restores it", () => {
    expect(backupBefore(POINTS.items, CHANGED)?.id).toBe("shop-example-com_20260922_175950");
    expect(backupBefore(POINTS.items, { started_at: "2026-09-01T00:00:00" })).toBeNull();
    expect(restoreCommand("b1", "docker-compose")).toBe("noust backup restore b1 --databases-only");
    expect(restoreCommand("b1", "nextjs")).toBe("noust backup restore b1");
  });
});

// Whole-console renders: generous under a loaded machine or a slow CI runner.
describe("going back past a schema change", { timeout: 20_000 }, () => {
  it("names the deployments and their migrations, the restore command, then goes back confirmed", async () => {
    let calls = 0;
    const { user, backend } = await open("/deployments/20", { app_type: "docker-compose", layout: "releases" }, {
      [`POST /api/apps/${TAB_DOMAIN}/deployments/20/rollback`]: (call) => {
        calls += 1;
        const body = call.body as { schema_changed_ok?: boolean } | undefined;
        if (body?.schema_changed_ok !== true) return json(409, REFUSAL);
        return json(202, { job_id: JOB.id, status: "pending", message: "queued", job: JOB });
      },
      [`GET /api/jobs/${JOB.id}`]: () => json(200, { ...JOB, status: "running" }),
    });
    await user.click(await screen.findByRole("button", { name: "Go back to this version" }));
    const first = await screen.findByRole("dialog", { name: "Go back to deployment 20?" });
    // Said before the button is pressed.
    expect(within(first).getByText(/Deployment #22 changed the database schema after this one/)).toBeInTheDocument();
    await user.click(within(first).getByRole("button", { name: "Go back" }));

    const dialog = await screen.findByRole("dialog", { name: "Going back passes a change to the database" });
    const list = within(dialog).getByRole("list", { name: "Deployments that changed the database schema" });
    expect(within(list).getByRole("link", { name: "Deployment 22" })).toBeInTheDocument();
    expect(await within(list).findByText("/app/node_modules/.bin/prisma migrate deploy")).toBeInTheDocument();
    expect(within(list).getByText("in the backend service")).toBeInTheDocument();
    // The hook that does not migrate is not a migration.
    expect(within(list).queryByText("./scripts/purge-cache.sh")).not.toBeInTheDocument();
    expect(await within(dialog).findByText("noust backup restore shop-example-com_20260922_175950 --databases-only")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Go back anyway" }));
    await waitFor(() => {
      expect(calls).toBe(2);
    });
    expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/deployments/20/rollback`).map((call) => call.body)).toEqual([
      { schema_changed_ok: false },
      { schema_changed_ok: true },
    ]);
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Going back passes a change to the database" })).not.toBeInTheDocument();
    });
  });

  it("asks the same before activating an older release, and activates it confirmed", async () => {
    const releases = {
      domain: TAB_DOMAIN,
      items: [
        { id: "20260922-180000-c0ffe22", commit: "c0ffe22", created_at: "2026-09-22T18:00:00+00:00", activated_at: "2026-09-22T18:00:40+00:00", status: "active", active: true, on_disk: true },
        { id: "20260920-180000-c0ffe20", commit: "c0ffe20", created_at: "2026-09-20T18:00:00+00:00", activated_at: null, status: "superseded", active: false, on_disk: true },
      ],
      total: 2,
    };
    const { user, backend } = await open("/deployments", { layout: "releases" }, {
      [`GET /api/apps/${TAB_DOMAIN}/releases`]: () => json(200, releases),
      [`POST /api/apps/${TAB_DOMAIN}/releases/20260920-180000-c0ffe20/activate`]: (call) =>
        call.search.get("schema_changed_ok") === "true"
          ? json(200, { domain: TAB_DOMAIN, release_id: "20260920-180000-c0ffe20", previous_id: "20260922-180000-c0ffe22", changed: true, rolled_back: true, deployment_id: 23 })
          : json(409, { ...REFUSAL, detail: "Going back to release 20260920-180000-c0ffe20 passes deployment 22, which changed the database schema" }),
    });
    const list = await screen.findByRole("list", { name: `Versions of ${TAB_DOMAIN}, newest first` });
    await user.click(within(list).getByRole("button", { name: /Go back to this version/ }));
    const confirm = await screen.findByRole("alertdialog");
    await user.click(within(confirm).getByRole("button", { name: "Go back to this version" }));
    const dialog = await screen.findByRole("dialog", { name: "Going back passes a change to the database" });
    // The same answer as every other way back: the deployments are named in the list.
    const changed = within(dialog).getByRole("list", { name: "Deployments that changed the database schema" });
    expect(within(changed).getByRole("link", { name: "Deployment 22" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Go back anyway" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${TAB_DOMAIN}/releases/20260920-180000-c0ffe20/activate`).map((call) => call.search.get("schema_changed_ok"))).toEqual([null, "true"]);
    });
  });

  it("asks the same before putting a backup's files back, and queues it confirmed", async () => {
    const { user, backend } = await open("/deployments", {}, {
      "POST /api/jobs/rollback": (call) =>
        (call.body as { schema_changed_ok: boolean }).schema_changed_ok ? json(202, { message: "queued", job: JOB }) : json(409, REFUSAL),
    });
    const points = await screen.findByRole("list", { name: `Backups of ${TAB_DOMAIN}, newest first` });
    const [newest] = within(points).getAllByRole("button", { name: /Go back to this version/ });
    if (newest === undefined) throw new Error("No backup to go back to");
    await user.click(newest);
    const confirm = await screen.findByRole("alertdialog", { name: "Go back to this backup?" });
    await user.click(within(confirm).getByRole("button", { name: "Go back to this version" }));
    const dialog = await screen.findByRole("dialog", { name: "Going back passes a change to the database" });
    expect(await within(dialog).findByText("noust backup restore shop-example-com_20260922_175950")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Go back anyway" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/rollback").map((call) => (call.body as { schema_changed_ok: boolean }).schema_changed_ok)).toEqual([false, true]);
    });
  });
});

describe("the history of hooks and schema changes", { timeout: 20_000 }, () => {
  it("marks a deploy that changed the database and one deployed with warnings", async () => {
    await open("/deployments", {}, {});
    const table = await screen.findByRole("region", { name: `Deploys of ${TAB_DOMAIN}, newest first` });
    expect(await within(table).findByText("Database changed")).toBeInTheDocument();
    expect(within(table).getByText("Deployed with warnings")).toBeInTheDocument();
  });

  it("lists what each hook ran, how it ended and its output verbatim, with the warnings", async () => {
    await open("/deployments/22", {}, {});
    const hooks = await screen.findByRole("list", { name: "Hooks deployment 22 ran" });
    expect(within(hooks).getByText("Before serving")).toBeInTheDocument();
    expect(within(hooks).getByText("Exited with 1")).toBeInTheDocument();
    expect(within(hooks).getByText(/All migrations have been successfully applied/)).toBeInTheDocument();
    expect(screen.getByText("This deploy changed the database schema: going back past it asks first, and names it.")).toBeInTheDocument();
    expect(screen.getByText("post_deploy hook ./scripts/purge-cache.sh exited with 1")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it in Spanish", async () => {
    await act(() => setLocale("es"));
    await open("/deployments/22", {}, {});
    expect(await screen.findByRole("list", { name: "Ganchos que ejecutó el despliegue 22" })).toBeInTheDocument();
    expect(screen.getAllByText("Desplegado con avisos").length).toBeGreaterThan(0);
  });
});
