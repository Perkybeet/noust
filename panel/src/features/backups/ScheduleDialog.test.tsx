import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

/** The first of several matches: the header's primary action, which an empty state repeats. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

function destination(name: string): Record<string, unknown> {
  return { name, backend: "sftp", encrypted: false, settings: {}, configured_secret_fields: [], encryption_configured: false };
}

function schedulesRoutes(schedules: Record<string, unknown>[], extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () => json(200, { path: "/var/backups/noust", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
    "GET /api/backup-schedules": () => json(200, { schedules, total: schedules.length, default_retention_count: 10 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [destination("offsite")], total: 1 }),
    "GET /api/backup-destinations/backends": () => json(200, { backends: [] }),
    ...extra,
  };
}

describe("ScheduleDialog", () => {
  it("creates a schedule that pushes to a destination with its own retention", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(
      schedulesRoutes([], {
        "POST /api/backup-schedules": () =>
          json(201, { success: true, message: "Backup schedule created for shop.example.com" }),
      }),
    );
    const { user, container } = renderConsole("/backups/schedules");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    await user.click(first(await screen.findAllByRole("button", { name: "New schedule" })));

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
      timer: "noust-backup-shop-example-com",
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
    const { user } = renderConsole("/backups/schedules");
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
  it("creates a schedule with 7 backups / 30 days unless told otherwise, and says so", async () => {
    const backend = fakeBackend(
      schedulesRoutes([], {
        "POST /api/backup-schedules": () => json(201, { success: true, message: "Backup schedule created for shop.example.com" }),
      }),
    );
    const { user } = renderConsole("/backups/schedules");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    await user.click(first(await screen.findAllByRole("button", { name: "New schedule" })));
    const dialog = await screen.findByRole("dialog", { name: "New backup schedule" });
    expect(within(dialog).getByText(/keeps its own last 7 backups for up to 30 days/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "shop.example.com" }));
    await user.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-schedules")).toHaveLength(1);
    });
    const body = backend.callsTo("POST /api/backup-schedules")[0]?.body as { retention_count: number | null; retention_days: number | null };
    expect(body.retention_count).toBe(7);
    expect(body.retention_days).toBe(30);
  });

  it("keeps an adopted schedule on the server default instead of inventing 7/30", async () => {
    const adopted = {
      domain: "shop.example.com",
      app_name: "shop-example-com",
      timer: "noust-backup-shop-example-com",
      schedule: "daily",
      on_calendar: "*-*-* 02:00:00",
      next_run: "pending",
      last_run: "never",
      retention_count: null,
      retention_days: null,
      include_databases: false,
      destinations: [],
    };
    const backend = fakeBackend(
      schedulesRoutes([adopted], {
        "PUT /api/backup-schedules/shop.example.com": () => json(200, { success: true, message: "Backup schedule updated for shop.example.com" }),
      }),
    );
    const { user, container } = renderConsole("/backups/schedules");
    const table = await screen.findByRole("region", { name: "Backup schedules" });
    await within(table).findByText("shop.example.com");
    await user.click(within(table).getByRole("button", { name: /^Actions for the schedule on/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog", { name: "Edit the schedule for shop.example.com" });
    expect(await within(dialog).findByText("Server default: the newest 10")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Keep")).not.toBeInTheDocument();
    await expectNoAxeViolations(container);

    // Adding a destination is exactly what used to prune 23 of 30 local backups.
    await user.click(within(dialog).getByRole("combobox", { name: "Add a destination" }));
    await user.click(await screen.findByRole("option", { name: "offsite" }));
    await user.click(within(dialog).getByRole("button", { name: "Add" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/backup-schedules/shop.example.com")).toHaveLength(1);
    });
    const body = backend.callsTo("PUT /api/backup-schedules/shop.example.com")[0]?.body as {
      retention_count: number | null;
      retention_days: number | null;
      include_databases: boolean;
    };
    expect(body.retention_count).toBeNull();
    expect(body.retention_days).toBeNull();
    expect(body.include_databases).toBe(false);
  });
});

describe("scheduling every application at once", () => {
  it("creates one schedule per application that has none, and names the one that failed", async () => {
    const backend = fakeBackend(
      schedulesRoutes([], {
        "POST /api/backup-schedules": (call) =>
          (call.body as { domain: string }).domain === "admin.example.com"
            ? problem(500, "backuperror", "systemctl enable failed for admin.example.com")
            : json(201, { success: true, message: "Backup schedule created" }),
      }),
    );
    const { user } = renderConsole("/backups/schedules");
    await user.click(first(await screen.findAllByRole("button", { name: "New schedule" })));
    const dialog = await screen.findByRole("dialog", { name: "New backup schedule" });
    await user.click(within(dialog).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "Every application without a schedule (2)" }));
    await user.click(within(dialog).getByRole("button", { name: "Create schedule" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-schedules")).toHaveLength(2);
    });
    const domains = backend.callsTo("POST /api/backup-schedules").map((call) => (call.body as { domain: string }).domain);
    expect(domains.sort()).toEqual(["admin.example.com", "shop.example.com"]);
    // The failure stays in the dialog, in the server's own words, next to the application.
    expect(await within(dialog).findByText("Some schedules were not created")).toBeInTheDocument();
    expect(within(dialog).getByText(/admin\.example\.com: systemctl enable failed for admin\.example\.com/)).toBeInTheDocument();
  });
});

describe("ScheduleDialog in Spanish", () => {
  it("opens the new schedule dialog in Spanish, with no accessibility violations", async () => {
    fakeBackend(schedulesRoutes([]));
    await act(() => setLocale("es"));
    const { user, container } = renderConsole("/backups/schedules");
    await screen.findByRole("heading", { level: 1, name: "Copias de seguridad" });
    await user.click(first(await screen.findAllByRole("button", { name: "Nueva programación" })));

    const dialog = await screen.findByRole("dialog", { name: "Nueva programación de copias de seguridad" });
    expect(within(dialog).getByText(/conserva sus últimas 7 copias de seguridad durante un máximo de 30 días/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Crear programación" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});
