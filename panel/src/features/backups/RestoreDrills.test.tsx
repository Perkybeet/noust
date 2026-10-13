import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const HOUR = 3_600_000;

beforeEach(() => {
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

function ago(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

const BACKUP = {
  backup_id: "shop-example-com_20260101_000000",
  domain: "shop.example.com",
  timestamp: ago(2 * HOUR),
  size: 1_048_576,
  size_human: "1 MB",
  age: "2 hours ago",
  description: "",
  includes_env: true,
  includes_node_modules: false,
  includes_build: false,
  has_database: true,
  database_backups: [],
  tags: [],
  last_verified_at: null,
  verified_ok: null,
};

const PASSED = {
  id: 2,
  domain: "shop.example.com",
  backup_id: BACKUP.backup_id,
  trigger: "manual",
  started_at: ago(HOUR),
  finished_at: ago(HOUR - 60_000),
  ok: true,
  summary: "The newest backup restores: 1 database loaded and dropped.",
  steps: [
    { name: "archive", ok: true, detail: "The archive verifies in depth", skipped: false },
    { name: "postgresql/shop", ok: true, detail: "Loaded into a temporary database and dropped", skipped: false },
  ],
};

const FAILED = {
  id: 1,
  domain: "shop.example.com",
  backup_id: BACKUP.backup_id,
  trigger: "schedule",
  started_at: ago(30 * HOUR),
  finished_at: ago(30 * HOUR - 60_000),
  ok: false,
  summary: "The newest backup does not restore.",
  steps: [
    { name: "archive", ok: true, detail: "The archive verifies in depth", skipped: false },
    { name: "postgresql/shop", ok: false, detail: "pg_restore: error: unsupported version (1.16) in file header", skipped: false },
    { name: "mongodb/audit", ok: true, detail: "Not tried: the engine is stopped", skipped: true },
  ],
};

function drawer(drills: object[] = [PASSED, FAILED], extra: Record<string, RouteHandler> = {}, every = 7) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { apps: [{ domain: "shop.example.com", name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace" }], total: 1 }),
    "GET /api/backups": () => json(200, { backups: [BACKUP], total: 1 }),
    "GET /api/backups/storage": () =>
      json(200, { path: "/var/backups/noust", total_size: 1, total_size_human: "1 B", backup_count: 1, domains: [], misplaced: [], filesystem_total: 1e11, filesystem_free: 1e10 }),
    "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0, default_retention_count: 10 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
    "GET /api/apps/shop.example.com/restore-drills": () => json(200, { items: drills, drill_days: every }),
    ...extra,
  });
  return { backend, harness: renderConsole("/backups?domain=shop.example.com") };
}

describe("the restore tests of an application", { timeout: 60_000 }, () => {
  it("lists what the last tests found, the failed one open on the step that failed", async () => {
    drawer();

    const section = await screen.findByRole("list", { name: "Restore tests" }, { timeout: 15_000 });
    expect(within(section).getByText("The newest backup restores: 1 database loaded and dropped.")).toBeInTheDocument();
    expect(within(section).getByText("The newest backup does not restore.")).toBeInTheDocument();
    // A failure is told with the tool's own words, verbatim, and the step that was not tried says so.
    expect(within(section).getByText("pg_restore: error: unsupported version (1.16) in file header")).toBeInTheDocument();
    expect(within(section).getAllByText("Failed").length).toBeGreaterThan(0);
    expect(within(section).getByText("Not tried")).toBeInTheDocument();
    expect(screen.getByText(/A scheduled backup runs one every 7 days/)).toBeInTheDocument();
    await expectNoAxeViolations(await screen.findByRole("dialog", { name: "Backups of shop.example.com" }));
  });

  it("says when scheduled tests are off", async () => {
    drawer([PASSED], {}, 0);

    expect(await screen.findByText(/Scheduled tests are off/, undefined, { timeout: 15_000 })).toBeInTheDocument();
  });

  it("invites the first test when none has run", async () => {
    drawer([]);

    expect(await screen.findByText(/No restore test has run yet/, undefined, { timeout: 15_000 })).toBeInTheDocument();
  });

  it("runs a test as a job and says that nothing of the application is touched", async () => {
    const job = { id: "d1", type: "backup", name: "Restore drill of shop.example.com", description: "", status: "pending", progress: 0, total_steps: 100, current_step: "", created_at: "2026-10-06T10:00:00", logs: [], metadata: { domain: "shop.example.com" } };
    const { backend, harness } = drawer([PASSED], {
      "POST /api/apps/shop.example.com/restore-drills": () => json(202, { job_id: "d1", status: "pending", message: "queued", job }),
    });

    await harness.user.click(await screen.findByRole("button", { name: "Run a restore test" }, { timeout: 15_000 }));

    await waitFor(() => expect(backend.callsTo("POST /api/apps/shop.example.com/restore-drills")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps/shop.example.com/restore-drills")[0]?.body).toEqual({ backup_id: null });
    expect(await screen.findByText("Nothing of the application is touched.")).toBeInTheDocument();
  });

  it("is not shown by a server that has no restore tests", async () => {
    drawer([], { "GET /api/apps/shop.example.com/restore-drills": () => new Response(JSON.stringify({ error: "not_found", detail: "no" }), { status: 404, headers: { "content-type": "application/json" } }) });

    await screen.findByRole("dialog", { name: "Backups of shop.example.com" }, { timeout: 15_000 });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Run a restore test" })).not.toBeInTheDocument());
  });

  it("speaks Spanish", async () => {
    drawer();
    await screen.findByRole("list", { name: "Restore tests" }, { timeout: 15_000 });

    await act(() => setLocale("es"));

    expect(await screen.findByRole("button", { name: "Hacer una prueba de restauración" })).toBeInTheDocument();
    expect(screen.getByText("Programada")).toBeInTheDocument();
  });
});
