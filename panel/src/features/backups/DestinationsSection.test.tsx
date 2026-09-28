import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const SFTP_FIELDS = [
  { key: "host", label: "Host", secret: false, required: true, placeholder: "", help: "SSH server address.", choices: [] },
  { key: "user", label: "User", secret: false, required: true, placeholder: "", help: "", choices: [] },
  { key: "port", label: "Port", secret: false, required: false, placeholder: "22", help: "", choices: [] },
  { key: "pass", label: "Password", secret: true, required: false, placeholder: "", help: "Leave blank when using a private key instead.", choices: [] },
  { key: "key_file", label: "Private key path", secret: false, required: false, placeholder: "", help: "", choices: [] },
  { key: "path", label: "Remote folder", secret: false, required: false, placeholder: "wasm-backups", help: "", choices: [] },
];

const BACKENDS = { backends: [{ backend: "sftp", fields: SFTP_FIELDS }] };

function destination(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "offsite",
    backend: "sftp",
    encrypted: false,
    settings: { host: "backup.example.com", user: "wasm", path: "wasm-backups" },
    configured_secret_fields: [],
    encryption_configured: false,
    created_at: "2026-01-01T00:00:00+00:00",
    updated_at: "2026-01-01T00:00:00+00:00",
    ...overrides,
  };
}

function baseRoutes(destinations: Record<string, unknown>[], extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/backups": () => json(200, { backups: [], total: 0 }),
    "GET /api/backups/storage": () => json(200, { path: "/var/backups/wasm", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
    "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0 }),
    "GET /api/backup-destinations": () => json(200, { destinations, total: destinations.length }),
    "GET /api/backup-destinations/backends": () => json(200, BACKENDS),
    ...extra,
  };
}

async function destinationsRegion(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Backup destinations" });
}

describe("DestinationsSection", () => {
  it("lists destinations with their backend, and has no accessibility violations", async () => {
    fakeBackend(baseRoutes([destination()]));
    const { container } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    const region = await destinationsRegion();
    expect(within(region).getByText("offsite")).toBeInTheDocument();
    expect(within(region).getByText("SFTP server")).toBeInTheDocument();
    expect(within(region).getByText("Not encrypted")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("shows the empty state and its CLI hint when there are none yet", async () => {
    fakeBackend(baseRoutes([]));
    renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(await screen.findByText("No destinations yet")).toBeInTheDocument();
    expect(screen.getByText("wasm backup destination add <name> --type <backend>")).toBeInTheDocument();
  });

  it("adds a new destination, sending only the fields that were filled in", async () => {
    const backend = fakeBackend(
      baseRoutes([], {
        "POST /api/backup-destinations": () =>
          json(201, { success: true, message: "Backup destination created: offsite", destination: destination() }),
      }),
    );
    const { user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    await user.click(await screen.findByRole("button", { name: "Add destination" }));

    const dialog = await screen.findByRole("dialog", { name: "Add backup destination" });
    await user.type(within(dialog).getByLabelText("Name"), "offsite");
    await user.click(within(dialog).getByRole("combobox", { name: "Backend" }));
    // The option's accessible name includes its hint line ("Any server reachable over SSH."),
    // not just the backend's label: match the start of it rather than the whole thing.
    await user.click(await screen.findByRole("option", { name: /^SFTP server/ }));

    await user.type(within(dialog).getByLabelText("Host"), "backup.example.com");
    await user.type(within(dialog).getByLabelText("User"), "wasm");

    await user.click(within(dialog).getByRole("button", { name: "Add destination" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-destinations")).toHaveLength(1);
    });
    const body = backend.callsTo("POST /api/backup-destinations")[0]?.body as { name: string; backend: string; fields: Record<string, string>; encrypted: boolean };
    expect(body.name).toBe("offsite");
    expect(body.backend).toBe("sftp");
    expect(body.encrypted).toBe(false);
    expect(body.fields["host"]).toBe("backup.example.com");
    expect(body.fields["user"]).toBe("wasm");
    // The password was left blank and is a secret field: it is not sent at all.
    expect(body.fields).not.toHaveProperty("pass");
  });

  it("tests a destination and shows whether it answered", async () => {
    fakeBackend(
      baseRoutes([destination()], {
        "POST /api/backup-destinations/offsite/test": () => json(200, { ok: true, entries: ["shop-example-com"] }),
      }),
    );
    const { user } = renderConsole("/backups");
    const region = await destinationsRegion();
    await within(region).findByText("offsite");

    await user.click(within(region).getByRole("button", { name: "Actions for offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test" }));

    await waitFor(() => {
      expect(within(region).getByText("Reachable")).toBeInTheDocument();
    });
  });

  it("refuses to remove a destination a schedule still uses, and offers to remove it anyway", async () => {
    let forced = false;
    const backend = fakeBackend(
      baseRoutes([destination()], {
        "DELETE /api/backup-destinations/offsite": (call) => {
          if (call.search.get("force") !== "true") {
            return problem(500, "backuperror", "Backup destination 'offsite' is used by 1 schedule(s): shop.example.com", {
              hint: "Pass --force to remove it and drop the reference from those schedules.",
            });
          }
          forced = true;
          return json(200, { success: true, message: "Backup destination removed: offsite" });
        },
      }),
    );
    const { user } = renderConsole("/backups");
    const region = await destinationsRegion();
    await within(region).findByText("offsite");

    await user.click(within(region).getByRole("button", { name: "Actions for offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));

    const confirmDialog = await screen.findByRole("alertdialog", { name: "Remove offsite" });
    await user.type(within(confirmDialog).getByRole("textbox"), "offsite");
    await user.click(within(confirmDialog).getByRole("button", { name: "Remove destination" }));

    await within(confirmDialog).findByText(/Pass --force/);
    await user.click(within(confirmDialog).getByRole("button", { name: "Remove anyway" }));

    await waitFor(() => {
      expect(forced).toBe(true);
    });
    expect(backend.callsTo("DELETE /api/backup-destinations/offsite")).toHaveLength(2);
  });

  it("browses a destination and restores a backup found there", async () => {
    const backend = fakeBackend(
      baseRoutes([destination()], {
        "GET /api/backup-destinations/offsite/backups": (call) =>
          call.search.get("app") === "shop-example-com"
            ? json(200, { backups: [{ backup_id: "shop-example-com_20260101_000000", app_name: "shop-example-com", size: 1024, modified: "2026-01-01T00:00:00Z", has_metadata: true }] })
            : json(200, { apps: ["shop-example-com"] }),
        "POST /api/backup-destinations/offsite/backups/shop-example-com_20260101_000000/restore": () =>
          json(202, { job_id: "j1", status: "pending", message: "Restore queued", job: {} }),
        "GET /api/apps": () =>
          json(200, {
            apps: [{ domain: "shop.example.com", name: "shop.example.com", status: "running", active: true, enabled: true, layout: "releases", webhook_enabled: false, keep_releases: 5, zero_downtime: false }],
            total: 1,
          }),
      }),
    );
    const { user } = renderConsole("/backups");
    const region = await destinationsRegion();
    await within(region).findByText("offsite");

    await user.click(within(region).getByRole("button", { name: "Actions for offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Browse" }));

    const browse = await screen.findByRole("dialog", { name: "Browse a destination" });
    await user.click(await within(browse).findByRole("button", { name: "shop-example-com" }));

    const restoreRow = await within(browse).findByText("shop-example-com_20260101_000000");
    await user.click(within(restoreRow.closest("tr") ?? browse).getByRole("button", { name: "Restore" }));

    const confirm = await screen.findByRole("alertdialog", { name: "Restore shop-example-com_20260101_000000" });
    // The folder is named after the application; the target offered is its domain.
    const target = within(confirm).getByLabelText("Restore into");
    await waitFor(() => expect(target).toHaveValue("shop.example.com"));
    await user.type(within(confirm).getByLabelText(/Type/), "shop.example.com");
    await user.click(within(confirm).getByRole("button", { name: "Restore" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-destinations/offsite/backups/shop-example-com_20260101_000000/restore")).toHaveLength(1);
    });
    const [call] = backend.callsTo("POST /api/backup-destinations/offsite/backups/shop-example-com_20260101_000000/restore");
    expect(call?.body).not.toHaveProperty("target_domain", "shop-example-com");
  });
  it("adds an encrypted destination with a key the operator already has, and shows no new key", async () => {
    const backend = fakeBackend(
      baseRoutes([], {
        "POST /api/backup-destinations": () =>
          json(201, { success: true, message: "Backup destination created: offsite", destination: destination({ encrypted: true, encryption_configured: true }) }),
      }),
    );
    const { user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    await user.click(await screen.findByRole("button", { name: "Add destination" }));

    const dialog = await screen.findByRole("dialog", { name: "Add backup destination" });
    await user.type(within(dialog).getByLabelText("Name"), "offsite");
    await user.click(within(dialog).getByRole("combobox", { name: "Backend" }));
    await user.click(await screen.findByRole("option", { name: /^SFTP server/ }));
    await user.type(within(dialog).getByLabelText("Host"), "backup.example.com");
    await user.type(within(dialog).getByLabelText("User"), "wasm");
    await user.click(within(dialog).getByRole("checkbox", { name: /Encrypt backups before upload/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /I already have a key for this folder/ }));

    const submit = within(dialog).getByRole("button", { name: "Add destination" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Password", { selector: "input" }), "old-passphrase");
    await user.type(within(dialog).getByLabelText("Password 2 (salt)", { selector: "input" }), "old-salt");
    await user.click(submit);

    await waitFor(() => {
      expect(backend.callsTo("POST /api/backup-destinations")).toHaveLength(1);
    });
    const body = backend.callsTo("POST /api/backup-destinations")[0]?.body as { encrypted: boolean; encryption_key: { password: string; password2: string } | null };
    expect(body.encrypted).toBe(true);
    expect(body.encryption_key).toEqual({ password: "old-passphrase", password2: "old-salt" });
    // The key was typed in: there is nothing new to write down.
    expect(screen.queryByRole("dialog", { name: "Encryption key for offsite" })).not.toBeInTheDocument();
    expect(backend.callsTo("POST /api/backup-destinations/offsite/show-key")).toHaveLength(0);
  });

  it("shows an encrypted destination's key before removing it, and removes it with key_saved", async () => {
    const backend = fakeBackend(
      baseRoutes([destination({ encrypted: true, encryption_configured: true })], {
        "POST /api/backup-destinations/offsite/show-key": () => json(200, { password: "first-passphrase", password2: "second-salt" }),
        "DELETE /api/backup-destinations/offsite": () => json(200, { success: true, message: "Backup destination removed: offsite" }),
      }),
    );
    const { user } = renderConsole("/backups");
    const region = await destinationsRegion();
    await within(region).findByText("offsite");

    await user.click(within(region).getByRole("button", { name: "Actions for offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));

    const keyDialog = await screen.findByRole("dialog", { name: "Save the key before removing offsite" });
    expect(await within(keyDialog).findByText("first-passphrase")).toBeInTheDocument();
    const next = within(keyDialog).getByRole("button", { name: "Continue to remove" });
    expect(next).toBeDisabled();
    await user.click(within(keyDialog).getByRole("checkbox", { name: /I have saved this key/ }));
    await user.click(next);

    const confirmDialog = await screen.findByRole("alertdialog", { name: "Remove offsite" });
    expect(within(confirmDialog).getByText(/readable only with the key you saved/)).toBeInTheDocument();
    await user.type(within(confirmDialog).getByRole("textbox"), "offsite");
    await user.click(within(confirmDialog).getByRole("button", { name: "Remove destination" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/backup-destinations/offsite")).toHaveLength(1);
    });
    expect(backend.callsTo("DELETE /api/backup-destinations/offsite")[0]?.search.get("key_saved")).toBe("true");
  });

  it("cancelling the key step removes nothing", async () => {
    const backend = fakeBackend(
      baseRoutes([destination({ encrypted: true, encryption_configured: true })], {
        "POST /api/backup-destinations/offsite/show-key": () => json(200, { password: "first-passphrase", password2: "second-salt" }),
      }),
    );
    const { user } = renderConsole("/backups");
    const region = await destinationsRegion();
    await within(region).findByText("offsite");

    await user.click(within(region).getByRole("button", { name: "Actions for offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const keyDialog = await screen.findByRole("dialog", { name: "Save the key before removing offsite" });
    await within(keyDialog).findByText("first-passphrase");
    await user.click(within(keyDialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Save the key before removing offsite" })).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("alertdialog", { name: "Remove offsite" })).not.toBeInTheDocument();
    expect(backend.callsTo("DELETE /api/backup-destinations/offsite")).toHaveLength(0);
  });
});

describe("DestinationsSection in Spanish", () => {
  it("lists destinations, opens the add dialog and shows the encryption key dialog in Spanish, with no accessibility violations", async () => {
    fakeBackend(baseRoutes([destination({ encrypted: true, encryption_configured: true })], {
      "POST /api/backup-destinations/offsite/show-key": () => json(200, { password: "first-passphrase", password2: "second-salt" }),
    }));
    await act(() => setLocale("es"));
    const { container, user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Copias de seguridad" });
    const region = await screen.findByRole("region", { name: "Destinos de copias de seguridad" });
    const offsiteRow = (await within(region).findByText("offsite")).closest("tr");
    if (!offsiteRow) throw new Error("no row");
    expect(within(offsiteRow).getByText("Cifrado")).toBeInTheDocument();
    await expectNoAxeViolations(container);

    await user.click(within(region).getByRole("button", { name: "Acciones de offsite" }));
    await user.click(await screen.findByRole("menuitem", { name: "Mostrar clave de cifrado" }));
    const keyDialog = await screen.findByRole("dialog", { name: "Clave de cifrado de offsite" });
    expect(await within(keyDialog).findByText("first-passphrase")).toBeInTheDocument();
    expect(within(keyDialog).getByRole("checkbox", { name: /He guardado esta clave/ })).toBeInTheDocument();
  });

  it("adds a destination through the dialog in Spanish", async () => {
    fakeBackend(
      baseRoutes([], {
        "POST /api/backup-destinations": () =>
          json(201, { success: true, message: "Backup destination created: offsite", destination: destination() }),
      }),
    );
    await act(() => setLocale("es"));
    const { user } = renderConsole("/backups");
    await screen.findByRole("heading", { level: 1, name: "Copias de seguridad" });
    await user.click(await screen.findByRole("button", { name: "Añadir destino" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir destino de copias de seguridad" });
    expect(within(dialog).getByText("Nombre")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
  });
});
