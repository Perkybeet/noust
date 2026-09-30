import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
function screenWidth(wide: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: wide,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

beforeEach(() => {
  screenWidth(true);
});

function ago(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

/** One backup, with every field `BackupInfo` requires, so a test only names what it varies. */
function backup(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
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
    has_database: false,
    database_backups: [],
    tags: [],
    last_verified_at: null,
    verified_ok: null,
    ...overrides,
  };
}

function schedule(domain: string): Record<string, unknown> {
  return {
    domain,
    app_name: domain.replace(/\./g, "-"),
    timer: `noust-backup-${domain.replace(/\./g, "-")}`,
    schedule: "daily",
    on_calendar: "*-*-* 02:00:00",
    next_run: new Date(Date.now() + 5 * HOUR).toISOString(),
    last_run: ago(19 * HOUR),
    retention_count: 7,
    retention_days: 30,
    include_databases: true,
    destinations: [{ name: "offsite", retention_count: null, retention_days: null }],
  };
}

function backupsRoutes(
  backups: Record<string, unknown>[],
  extra: Record<string, RouteHandler> = {},
  schedules: Record<string, unknown>[] = [],
): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups, total: backups.length }),
    "GET /api/backups/storage": () =>
      json(200, {
        path: "/var/backups/noust",
        total_size: 3 * 1024 ** 2,
        total_size_human: "3.00 MB",
        backup_count: backups.length,
        domains: [],
        misplaced: [],
        filesystem_total: 500 * 1024 ** 3,
        filesystem_free: 120 * 1024 ** 3,
      }),
    "GET /api/backup-schedules": () => json(200, { schedules, total: schedules.length, default_retention_count: 10 }),
    "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
    ...extra,
  };
}

/** The coverage table's row of an application. */
async function appRow(domain: string): Promise<HTMLElement> {
  const table = await screen.findByRole("region", { name: /^Applications/ });
  const cell = await within(table).findByRole("button", { name: new RegExp(`^${domain.replace(/\./g, "\\.")}`) });
  const row = cell.closest("tr");
  if (!row) throw new Error(`no row for ${domain}`);
  return row;
}

/** The drawer with one application's backups, opened from its row. */
async function openBackupsOf(user: ReturnType<typeof renderConsole>["user"], domain: string): Promise<HTMLElement> {
  await user.click(within(await appRow(domain)).getByRole("button", { name: new RegExp(`^${domain.replace(/\./g, "\\.")}`) }));
  return screen.findByRole("dialog", { name: `Backups of ${domain}` });
}

/** A backup's row in the drawer, by its menu's name. */
function backupRow(drawer: HTMLElement, id: string): HTMLElement {
  const row = within(drawer).getByRole("button", { name: `Actions for ${id}` }).closest("tr");
  if (!row) throw new Error(`no row for ${id}`);
  return row;
}

describe("the Backups tab", () => {
  it("answers whether each application is protected: up to date, out of date, or never backed up", { timeout: 20_000 }, async () => {
    fakeBackend(
      backupsRoutes(
        [
          backup({ backup_id: "shop-1", domain: "shop.example.com", timestamp: ago(3 * HOUR) }),
          backup({ backup_id: "shop-2", domain: "shop.example.com", timestamp: ago(DAY + 3 * HOUR) }),
          backup({ backup_id: "old-1", domain: "old.example.com", timestamp: ago(20 * DAY) }),
        ],
        {},
        [schedule("shop.example.com")],
      ),
    );
    const { container } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });

    const shop = await appRow("shop.example.com");
    expect(within(shop).getByText("Up to date")).toBeInTheDocument();
    expect(within(shop).getByText("Every day at 02:00")).toBeInTheDocument();
    expect(within(shop).getByText("offsite")).toBeInTheDocument();
    // admin.example.com is deployed and has none at all.
    const admin = await appRow("admin.example.com");
    expect(within(admin).getByText("No backups")).toBeInTheDocument();
    expect(within(admin).getByText("Not scheduled")).toBeInTheDocument();
    // A deleted application's backups keep their row: they are still restorable.
    const old = await appRow("old.example.com");
    expect(within(old).getByText("Out of date")).toBeInTheDocument();
    expect(within(old).getByText("No longer deployed")).toBeInTheDocument();

    expect(screen.getByText("3 applications")).toBeInTheDocument();
    expect(screen.getByText("noust backup list")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says in the header what the backups take and how much room the disk they are on has left", async () => {
    fakeBackend(backupsRoutes([backup()]));
    const { user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(await screen.findByText("1 backup, 3.0 MB")).toBeInTheDocument();
    expect(screen.getByText("Disk: 120 GB free of 500 GB")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "About the disk holding the backups" }));
    const meter = await screen.findByRole("meter", { name: "Space used" });
    expect(meter).toHaveAttribute("aria-valuetext", "380 GB of 500 GB");
    expect(screen.getByText(/The whole disk the backup folder is on/)).toBeInTheDocument();
    expect(screen.getByText("/var/backups/noust")).toBeInTheDocument();
  });

  it("opens an application's backups in a drawer, with each one's own last integrity check", { timeout: 20_000 }, async () => {
    fakeBackend(
      backupsRoutes([
        backup({ backup_id: "b-never", timestamp: ago(HOUR) }),
        backup({ backup_id: "b-ok", timestamp: ago(2 * HOUR), verified_ok: true, last_verified_at: ago(HOUR), has_database: true }),
        backup({ backup_id: "b-bad", timestamp: ago(3 * HOUR), verified_ok: false, last_verified_at: ago(HOUR), description: "Before the migration" }),
      ]),
    );
    const { user, location, container } = renderConsole("/backups");
    const drawer = await openBackupsOf(user, "shop.example.com");
    expect(location().search).toEqual({ domain: "shop.example.com" });
    expect(within(drawer).getByText("3 backups, 3.0 MB in total. Newest first.")).toBeInTheDocument();

    expect(within(backupRow(drawer, "b-never")).getByText("Not checked")).toBeInTheDocument();
    expect(within(backupRow(drawer, "b-never")).getByText("Files and .env")).toBeInTheDocument();
    expect(within(backupRow(drawer, "b-ok")).getByText("Verified")).toBeInTheDocument();
    expect(within(backupRow(drawer, "b-ok")).getByText("Files, .env, and databases")).toBeInTheDocument();
    expect(within(backupRow(drawer, "b-bad")).getByText("Check failed")).toBeInTheDocument();
    expect(within(backupRow(drawer, "b-bad")).getByText("Before the migration · Files and .env")).toBeInTheDocument();
    expect(within(drawer).getByText("noust backup list shop.example.com")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("lands on an application's backups from its address, as 3.0's filtered list did", async () => {
    fakeBackend(backupsRoutes([backup({ backup_id: "b-1" })]));
    renderConsole("/backups?domain=shop.example.com");
    const drawer = await screen.findByRole("dialog", { name: "Backups of shop.example.com" });
    expect(await within(drawer).findByRole("button", { name: "Actions for b-1" })).toBeInTheDocument();
  });

  it("checking a backup shows the server's fresh verdict, not a session-only guess", async () => {
    let verified: { verified_ok: boolean | null; last_verified_at: string | null } = { verified_ok: null, last_verified_at: null };
    const backend = fakeBackend(
      backupsRoutes([], {
        "GET /api/backups": () => json(200, { backups: [backup({ backup_id: "b-1", ...verified })], total: 1 }),
        "POST /api/backups/b-1/verify": () => {
          verified = { verified_ok: true, last_verified_at: new Date().toISOString() };
          return json(200, { backup_id: "b-1", valid: true, checksum_ok: true, files_ok: true, errors: [], warnings: [] });
        },
      }),
    );
    const { user } = renderConsole("/backups");
    const drawer = await openBackupsOf(user, "shop.example.com");
    expect(within(backupRow(drawer, "b-1")).getByText("Not checked")).toBeInTheDocument();

    await user.click(within(drawer).getByRole("button", { name: "Actions for b-1" }));
    await user.click(await screen.findByRole("menuitem", { name: "Check integrity" }));

    await waitFor(() => {
      expect(within(backupRow(drawer, "b-1")).getByText("Verified")).toBeInTheDocument();
    });
    expect(backend.callsTo("POST /api/backups/b-1/verify")).toHaveLength(1);
    // The list was refetched: the row reflects what GET /api/backups now answers.
    expect(backend.callsTo("GET /api/backups").length).toBeGreaterThan(1);
  });

  it("reports a failed check with the server's own detail", async () => {
    fakeBackend(
      backupsRoutes([backup({ backup_id: "b-2" })], {
        "POST /api/backups/b-2/verify": () =>
          json(200, {
            backup_id: "b-2",
            valid: false,
            checksum_ok: false,
            files_ok: true,
            errors: ["Checksum mismatch: the archive changed since it was created"],
            warnings: [],
          }),
      }),
    );
    const { user } = renderConsole("/backups");
    const drawer = await openBackupsOf(user, "shop.example.com");
    await user.click(within(drawer).getByRole("button", { name: "Actions for b-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Check integrity" }));

    await waitFor(() => {
      expect(
        [...document.querySelectorAll(".toast")].some((toast) => toast.textContent.includes("Checksum mismatch: the archive changed since it was created")),
      ).toBe(true);
    });
  });

  it("deleting a backup asks for its id typed and for the operator to confirm it's them", async () => {
    let elevated = false;
    fakeBackend(
      backupsRoutes([backup({ backup_id: "b-3" })], {
        "DELETE /api/backups/b-3": () =>
          elevated
            ? json(200, { success: true, message: "Backup deleted: b-3", backup_id: "b-3" })
            : problem(403, "elevation_required", "Confirm it's you to continue."),
        "POST /api/auth/elevate": () => {
          elevated = true;
          return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
        },
      }),
    );
    const { user } = renderConsole("/backups");
    const drawer = await openBackupsOf(user, "shop.example.com");
    await user.click(within(drawer).getByRole("button", { name: "Actions for b-3" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete a backup of shop.example.com" });
    await user.type(within(dialog).getByRole("textbox"), "b-3");
    await user.click(within(dialog).getByRole("button", { name: "Delete backup" }));

    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect([...document.querySelectorAll(".toast")].some((toast) => toast.textContent.includes("Backup deleted: b-3"))).toBe(true);
    });
  });

  it("copies a backup to a destination as a background job", async () => {
    const backend = fakeBackend(
      backupsRoutes([backup({ backup_id: "b-4" })], {
        "GET /api/backup-destinations": () =>
          json(200, {
            destinations: [{ name: "offsite", backend: "sftp", encrypted: false, settings: {}, configured_secret_fields: [], encryption_configured: false }],
            total: 1,
          }),
        "POST /api/backups/b-4/push": () => json(202, { job_id: "j-push", status: "pending", message: "Copy queued", job: {} }),
      }),
    );
    const { user } = renderConsole("/backups");
    const drawer = await openBackupsOf(user, "shop.example.com");
    await user.click(within(drawer).getByRole("button", { name: "Actions for b-4" }));
    await user.click(await screen.findByRole("menuitem", { name: "Copy to destination…" }));

    const dialog = await screen.findByRole("dialog", { name: "Copy b-4" });
    await user.click(within(dialog).getByRole("combobox", { name: "Destination" }));
    await user.click(await screen.findByRole("option", { name: "offsite" }));
    await user.click(within(dialog).getByRole("button", { name: "Copy" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backups/b-4/push")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/backups/b-4/push")[0]?.body).toEqual({ destination: "offsite" });
  });

  it("restores the latest backup from its row, once the target's name is typed", async () => {
    const backend = fakeBackend(
      backupsRoutes([backup({ backup_id: "b-new", timestamp: ago(HOUR) }), backup({ backup_id: "b-old", timestamp: ago(DAY) })], {
        "POST /api/backups/b-new/restore": () => json(202, { job_id: "j-restore", status: "pending", message: "Restore queued", job: {} }),
      }),
    );
    const { user, container } = renderConsole("/backups");
    await user.click(within(await appRow("shop.example.com")).getByRole("button", { name: "Actions for shop.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: "Restore the latest…" }));

    const dialog = await screen.findByRole("dialog", { name: "Restore a backup of shop.example.com" });
    expect(within(dialog).getByText("b-new")).toBeInTheDocument();
    const restore = within(dialog).getByRole("button", { name: "Restore" });
    expect(restore).toBeDisabled();
    expect(within(dialog).getByLabelText("Restore into")).toHaveValue("shop.example.com");
    await user.type(within(dialog).getByLabelText(/to confirm$/), "shop.example.co");
    expect(restore).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/to confirm$/), "m");
    expect(restore).toBeEnabled();
    await expectNoAxeViolations(container);
    await user.click(restore);

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backups/b-new/restore")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/backups/b-new/restore")[0]?.body).toEqual({ target_domain: null, restore_env: true, verify: true });
  });

  it("backs one application up from its row, the application already chosen", async () => {
    const backend = fakeBackend(
      backupsRoutes([], {
        "POST /api/backups": () => json(202, { job_id: "j-backup", status: "pending", message: "Backup queued for admin.example.com", job: {} }),
      }),
    );
    const { user } = renderConsole("/backups");
    await user.click(within(await appRow("admin.example.com")).getByRole("button", { name: "Actions for admin.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: "Back up now" }));

    const dialog = await screen.findByRole("dialog", { name: "Back up admin.example.com" });
    expect(within(dialog).queryByRole("combobox", { name: "Application" })).not.toBeInTheDocument();
    // What most operators never change is folded away.
    expect(within(dialog).getByRole("button", { name: "More options" })).toHaveAttribute("aria-expanded", "false");
    await user.click(within(dialog).getByRole("checkbox", { name: /Databases/ }));
    await user.click(within(dialog).getByRole("button", { name: "Create backup" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backups")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/backups")[0]?.body).toMatchObject({
      domain: "admin.example.com",
      include_env: true,
      include_database: true,
      include_node_modules: false,
      include_build: false,
      include_docker_volumes: false,
      redis_method: "rdb",
    });
  });

  it("filters by name and by what needs a backup, through the URL", async () => {
    fakeBackend(backupsRoutes([backup({ domain: "shop.example.com" })]));
    const { user, location } = renderConsole("/backups");
    await appRow("shop.example.com");

    await user.click(screen.getByRole("combobox", { name: "Show" }));
    await user.click(await screen.findByRole("option", { name: "Needing a backup" }));
    await waitFor(() => {
      expect(location().search).toEqual({ show: "attention" });
    });
    await appRow("admin.example.com");
    expect(screen.queryByRole("button", { name: "shop.example.com" })).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 applications")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search applications" }), "nothing-like-it");
    expect(await screen.findByText("No application matches these filters.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      expect(location().search).toEqual({});
    });
  });

  it("invites a schedule when applications exist but none has a backup", async () => {
    fakeBackend(backupsRoutes([]));
    const { user } = renderConsole("/backups");
    expect(await screen.findByText("None of your applications has a backup yet")).toBeInTheDocument();
    expect(within(await appRow("shop.example.com")).getByText("No backups")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Schedule backups" }));
    expect(await screen.findByRole("dialog", { name: "New backup schedule" })).toBeInTheDocument();
  });

  it("is one short first-use state on an empty server, leading to the first deploy", async () => {
    fakeBackend({ ...backupsRoutes([]), "GET /api/apps": () => json(200, { apps: [], total: 0 }) });
    const { container } = renderConsole("/backups");
    expect(await screen.findByRole("heading", { level: 2, name: "Nothing to back up yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New application" })).toHaveAttribute("href", "/apps/new");
    // No table, no filters, and no action that cannot work yet.
    expect(screen.queryByRole("region", { name: "Applications and their backups" })).not.toBeInTheDocument();
    expect(screen.queryByRole("search")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back up now" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it("names backups outside the backup directory, with the exact command that imports them", async () => {
    fakeBackend(
      backupsRoutes([], {
        "GET /api/backups/storage": () =>
          json(200, {
            path: "/mnt/backups/noust",
            total_size: 0,
            total_size_human: "0 B",
            backup_count: 0,
            domains: [],
            misplaced: [
              { directory: "/root", count: 3, command: "noust backup import /root" },
              { directory: "/var/backups/noust", count: 1, command: "noust backup import /var/backups/noust" },
            ],
            filesystem_total: null,
            filesystem_free: null,
          }),
      }),
    );
    const { container } = renderConsole("/backups");
    const title = await screen.findByText("4 backups are outside the backup directory");
    const notice = title.closest("[data-tone]");
    if (!(notice instanceof HTMLElement)) throw new Error("no notice");
    expect(notice).toHaveTextContent("3 backups in /root");
    expect(notice).toHaveTextContent("1 backup in /var/backups/noust");
    expect(notice).toHaveTextContent("noust backup import /root");
    expect(notice).toHaveTextContent("Add --dry-run to the command to see what would move first, without moving anything.");
    // The disk could not be read: said so, never an empty meter.
    expect(screen.getByText("Disk: free space unknown")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("keeps each row's actions in view on a phone", async () => {
    screenWidth(false);
    fakeBackend(backupsRoutes([backup()]));
    renderConsole("/backups");
    const list = await screen.findByRole("list", { name: "Applications and their backups" });
    const card = (await within(list).findByRole("button", { name: "shop.example.com" })).closest("li");
    if (!card) throw new Error("no card");
    expect(within(card).getByText("Up to date")).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Actions for shop.example.com" })).toBeInTheDocument();
  });
});

describe("the Backups tab in Spanish", () => {
  it("reads in Spanish, the drawer and its row actions included, with no accessibility violations", async () => {
    fakeBackend(backupsRoutes([backup({ backup_id: "b-es", verified_ok: true, last_verified_at: ago(HOUR) })]));
    await act(() => setLocale("es"));
    const { container, user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Copias de seguridad" });
    const table = await screen.findByRole("region", { name: "Aplicaciones y sus copias de seguridad" });
    const row = (await within(table).findByRole("button", { name: "shop.example.com" })).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Al día")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Programaciones/ })).toBeInTheDocument();
    await expectNoAxeViolations(container);

    await user.click(within(row).getByRole("button", { name: "shop.example.com" }));
    const drawer = await screen.findByRole("dialog", { name: "Copias de shop.example.com" });
    expect(within(drawer).getByText("Verificada")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Acciones de b-es" }));
    expect(await screen.findByRole("menuitem", { name: "Comprobar la integridad" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Restaurar…" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Copiar a un destino…" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Eliminar…" })).toBeInTheDocument();
  });
});
