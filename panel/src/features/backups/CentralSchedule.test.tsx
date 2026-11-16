import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { nasSession } from "../../test/nas";
import { taskTitle } from "./CentralSchedule";
import { useT } from "../../i18n";
import { renderHook } from "@testing-library/react";

// A screen as wide as a desktop's: tables are tables, not the phone's card rows.
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

const TASKS = [
  {
    key: "backup:vault.casa.example.org",
    kind: "backup",
    subject: "vault.casa.example.org",
    calendar: "daily",
    next_run: "2099-01-01 02:00:00",
    last_run: "2026-11-12 02:00:00",
    last_status: "ok",
    last_detail: "",
    missed: 2,
    problem: null,
  },
  {
    key: "cleanup",
    kind: "cleanup",
    subject: "",
    calendar: "weekly",
    next_run: "2099-01-04 04:00:00",
    last_run: "2026-11-05 04:00:00",
    last_status: "failed",
    last_detail: "docker: permission denied",
    missed: 0,
    problem: null,
  },
  { key: "image_check", kind: "image_check", subject: "", calendar: "someday", next_run: null, last_run: null, last_status: null, last_detail: "", missed: 0, problem: "the calendar 'someday' is not one Noust reads" },
];

function schedules(session = nasSession(), tasks: unknown[] = TASKS) {
  return fakeBackend({
    ...signedInRoutes(session),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () => json(200, { path: "/var/backups/noust", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
    "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0, default_retention_count: 10 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
    "GET /api/central/schedule": () => json(200, tasks),
  });
}

describe("what Noust runs by itself, where the machine has no timers (item 188)", () => {
  it("lists each task with when it runs next and when it last ran, and says what came due while Noust was off and ran once", { timeout: 60_000 }, async () => {
    schedules();
    const { container } = renderConsole("/backups/schedules");

    const list = await screen.findByRole("list", { name: "Tasks Noust runs by itself" });
    const copy = within(list).getByText("Copy of vault.casa.example.org").closest("li") as HTMLElement;
    expect(within(copy).getByText(/2 runs came due while Noust was off: they ran once when Noust came back\./)).toBeInTheDocument();
    const cleanup = within(list).getByText("Cleanup of Docker and the disk").closest("li") as HTMLElement;
    // A failure is said with what the command said, not as a bare state.
    expect(within(cleanup).getByText("It failed: docker: permission denied")).toBeInTheDocument();
    // A calendar Noust does not read is said, never run at a guess.
    const check = within(list).getByText("Look for new versions of the images").closest("li") as HTMLElement;
    expect(within(check).getByText(/It cannot be scheduled: the calendar 'someday' is not one Noust reads/)).toBeInTheDocument();
    expect(within(check).getByText("Has not run yet")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says there is nothing yet, not an empty table", { timeout: 60_000 }, async () => {
    schedules(nasSession(), []);
    renderConsole("/backups/schedules");

    expect(await screen.findByText(/Nothing is scheduled yet/)).toBeInTheDocument();
  });

  it("is not drawn on a server, whose timers are systemd's and are the list above", { timeout: 60_000 }, async () => {
    const backend = schedules(SESSION);
    renderConsole("/backups/schedules");

    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(screen.queryByRole("region", { name: "What Noust runs by itself" })).not.toBeInTheDocument();
    expect(backend.callsTo("GET /api/central/schedule")).toHaveLength(0);
  });
});

describe("the sentence of a task", () => {
  it("is made from what it is and what it is for, and says a kind it does not know as the server named it", () => {
    const { result } = renderHook(() => useT());
    const t = result.current;
    const base = { subject: "", calendar: "daily", next_run: null, last_run: null, last_status: null, last_detail: "", missed: 0, problem: null };

    expect(taskTitle(t, { ...base, key: "a", kind: "volume_backup", subject: "vault.casa.example.org" })).toBe("Copy of the data of vault.casa.example.org");
    expect(taskTitle(t, { ...base, key: "b", kind: "sweep" })).toBe("Retention sweep of the copies");
    expect(taskTitle(t, { ...base, key: "c", kind: "future_kind" })).toBe("future_kind");
  });
});
