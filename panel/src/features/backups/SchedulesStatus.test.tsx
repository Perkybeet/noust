import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";

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

function schedule(domain: string, nextRun: string): Record<string, unknown> {
  return {
    domain,
    app_name: domain.replace(/\./g, "-"),
    timer: `noust-backup-${domain.replace(/\./g, "-")}`,
    schedule: "daily",
    on_calendar: "*-*-* 02:00:00",
    next_run: nextRun,
    last_run: "never",
    retention_count: 7,
    retention_days: null,
    retention_gb: null,
    include_databases: true,
    destinations: [],
  };
}

describe("the status of each backup schedule", { timeout: 30_000 }, () => {
  it("says which schedules will fire and which are on record without a timer running", async () => {
    const schedules = [schedule("shop.example.com", new Date(Date.now() + 5 * 3_600_000).toISOString()), schedule("blog.example.com", "pending")];
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/backups": () => json(200, { backups: [], total: 0 }),
      "GET /api/backups/storage": () => json(200, { path: "/var/backups/noust", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
      "GET /api/backup-schedules": () => json(200, { schedules, total: 2, default_retention_count: 10 }),
      "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
    });
    renderConsole("/backups/schedules");

    const shop = (await screen.findByText("shop.example.com")).closest("tr");
    const blog = (await screen.findByText("blog.example.com")).closest("tr");
    if (!shop || !blog) throw new Error("no row");

    expect(within(shop).getByText("Active")).toBeInTheDocument();
    expect(within(blog).getByText("Timer not running")).toBeInTheDocument();
    expect(within(blog).queryByText("Active")).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
