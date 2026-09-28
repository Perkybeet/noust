import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

function destination(name: string): Record<string, unknown> {
  return { name, backend: "sftp", encrypted: false, settings: {}, configured_secret_fields: [], encryption_configured: false };
}

function schedulesRoutes(schedules: Record<string, unknown>[], extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () => json(200, { path: "/var/backups/wasm", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
    "GET /api/backup-schedules": () => json(200, { schedules, total: schedules.length }),
    "GET /api/backup-destinations": () => json(200, { destinations: [destination("offsite")], total: 1 }),
    "GET /api/backup-destinations/backends": () => json(200, { backends: [] }),
    ...extra,
  };
}

describe("ScheduleDialog", () => {
  it("creates a schedule that pushes to a destination with its own retention", async () => {
    const backend = fakeBackend(
      schedulesRoutes([], {
        "POST /api/backup-schedules": () =>
          json(201, { success: true, message: "Backup schedule created for shop.example.com" }),
      }),
    );
    const { user, container } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    await user.click(await screen.findByRole("button", { name: "New schedule" }));

    const dialog = await screen.findByRole("dialog", { name: "New backup schedule" });
    await user.click(within(dialog).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "shop.example.com" }));

    await user.click(within(dialog).getByRole("combobox", { name: "Add a destination" }));
    await user.click(await screen.findByRole("option", { name: "offsite" }));
    await user.click(within(dialog).getByRole("button", { name: "Add" }));

    await user.type(within(dialog).getByLabelText("Keep on offsite"), "3");
    await expectNoAxeViolations(container);

    await user.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-schedules")).toHaveLength(1);
    });
    const body = backend.callsTo("POST /api/backup-schedules")[0]?.body as {
      domain: string;
      destinations: { name: string; retention_count: number | null; retention_days: number | null }[];
    };
    expect(body.domain).toBe("shop.example.com");
    expect(body.destinations).toEqual([{ name: "offsite", retention_count: 3, retention_days: null }]);
  });

  it("edits a schedule through PUT, not another POST", async () => {
    const existing = {
      domain: "shop.example.com",
      app_name: "shop-example-com",
      timer: "wasm-backup-shop-example-com",
      schedule: "daily",
      on_calendar: "*-*-* 02:00:00",
      next_run: "pending",
      last_run: "never",
      retention_count: 7,
      retention_days: 30,
      destinations: [{ name: "offsite", retention_count: null, retention_days: null }],
    };
    const backend = fakeBackend(
      schedulesRoutes([existing], {
        "PUT /api/backup-schedules/shop.example.com": () =>
          json(200, { success: true, message: "Backup schedule updated for shop.example.com" }),
      }),
    );
    const { user } = renderConsole("/backups");
    const table = await screen.findByRole("region", { name: "Backup schedules" });
    await within(table).findByText("shop.example.com");
    expect(within(table).getByText("offsite")).toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: /^Actions for the schedule on/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog", { name: "Edit the schedule for shop.example.com" });
    expect(within(dialog).getByText("offsite")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/backup-schedules/shop.example.com")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/backup-schedules")).toHaveLength(0);
  });
});
