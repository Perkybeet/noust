import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";

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

function schedule(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    domain: "shop.example.com",
    app_name: "shop-example-com",
    timer: "noust-backup-shop-example-com",
    schedule: "daily",
    on_calendar: "*-*-* 02:00:00",
    next_run: "2099-01-01T02:00:00+00:00",
    last_run: "never",
    retention_count: 7,
    retention_days: 30,
    destinations: [],
    ...overrides,
  };
}

// Checklist M-47: a schedule is read in words with its next run, never as the raw expression alone.
describe("the backup schedules list", () => {
  it("says when each schedule runs in words, keeps the calendar beneath it, and shows when it runs next", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/backups": () => json(200, { backups: [], total: 0 }),
      "GET /api/backups/storage": () => json(200, { path: "/var/backups/noust", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
      "GET /api/backup-schedules": () =>
        json(200, {
          schedules: [
            schedule({}),
            schedule({ domain: "blog.example.com", app_name: "blog-example-com", schedule: "weekly", on_calendar: "Mon *-*-* 02:00:00", next_run: "pending" }),
          ],
          total: 2,
          default_retention_count: 10,
        }),
      "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
    });
    const { container } = renderConsole("/backups/schedules");

    const table = await screen.findByRole("table", { name: "Backup schedules" });
    expect(within(table).getByRole("columnheader", { name: "Next run" })).toBeInTheDocument();
    const shop = await within(table).findByRole("row", { name: /shop\.example\.com/ });
    expect(within(shop).getByText("Every day at 02:00")).toBeInTheDocument();
    expect(within(shop).getByText("*-*-* 02:00:00")).toBeInTheDocument();
    // The next run is a moment: machine-readable, with the exact time in its tooltip.
    expect(shop.querySelector('time[datetime="2099-01-01T02:00:00.000Z"]')).not.toBeNull();

    const blog = within(table).getByRole("row", { name: /blog\.example\.com/ });
    expect(within(blog).getByText("Every Monday at 02:00")).toBeInTheDocument();
    expect(within(blog).getByText("Pending")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
