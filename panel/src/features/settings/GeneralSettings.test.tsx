import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { readServerIdentity } from "./GeneralSettings.model";

/** A session already in sudo mode, so a save goes straight through. */
const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

function generalRoutes(config: Record<string, unknown> = {}, extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(ELEVATED),
    "GET /api/config": () => json(200, { config, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/config/apps-directory": () => json(200, { apps_directory: "/var/www/apps" }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/config/ssl": () => json(200, { enabled: true, provider: "certbot", email: "ops@example.com" }),
    "GET /api/config/backup": () => json(200, { directory: "/var/backups/noust", max_per_app: 10 }),
    "GET /api/config/web": () => json(200, { host: "127.0.0.1", port: 8080, session_timeout: 3600 }),
    "GET /api/system/update": () => json(200, { current_version: "3.1.0", method: "apt", supported: true }),
    ...extra,
  };
}

function saveBar(): HTMLElement {
  return screen.getByRole("region", { name: "Unsaved changes" });
}

describe("reading the general settings", () => {
  it("reads the name, the addresses, whether updates are checked and the role", () => {
    expect(readServerIdentity({})).toEqual({ name: "", publicUrl: "", hooksUrl: "", checkUpdates: true, role: "server", serviceUser: "" });
    expect(
      readServerIdentity({
        server: { name: "web-1" },
        web: { public_url: "https://console.example.com", hooks_url: "https://hooks.example.com" },
        updates: { check: false },
        central: { role: "hub" },
        service_user: "www-data",
      }),
    ).toEqual({
      name: "web-1",
      publicUrl: "https://console.example.com",
      hooksUrl: "https://hooks.example.com",
      checkUpdates: false,
      role: "hub",
      serviceUser: "www-data",
    });
  });
});

describe("Settings > General", () => {
  it("shows one form with one save bar, the read-only details apart, and passes axe", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ service_user: "www-data" }));
    const { container } = renderConsole("/settings");
    await screen.findByDisplayValue("/var/www/apps");
    // The server's name is empty: the machine's own name is what it goes by, and says so.
    const name = screen.getByLabelText(/Server name/);
    expect(name).toHaveValue("");
    await waitFor(() => {
      expect(name).toHaveAttribute("placeholder", "web-01");
    });
    expect(screen.getByLabelText(/Backups kept per app/)).toHaveValue("10");
    expect(screen.getByRole("checkbox", { name: /Check for new versions/ })).toBeChecked();
    expect(await screen.findByText(/the apt repository/)).toBeInTheDocument();
    // What is not edited here is shown apart, with where it is set.
    const details = screen.getByRole("region", { name: "Details" });
    expect(within(details).getByText("/etc/noust/config.yaml")).toBeInTheDocument();
    expect(within(details).getByText("noust web expose-hooks hooks.example.com")).toBeInTheDocument();
    // One save bar, there from the start, with nothing to save.
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("saves every changed group from the one bar, and a refusal stays beside its field", { timeout: 20_000 }, async () => {
    let backup = { directory: "/var/backups/noust", max_per_app: 10 };
    let config: Record<string, unknown> = {};
    const backend = fakeBackend(
      generalRoutes(
        {},
        {
          "GET /api/config": () => json(200, { config, path: "/etc/noust/config.yaml", writable: true }),
          "GET /api/config/backup": () => json(200, backup),
          "PATCH /api/config": (call) => {
            const body = call.body as { path: string; value: unknown };
            config = { ...config, server: { name: body.value } };
            return json(200, { message: "ok", path: "/etc/noust/config.yaml", value: body.value });
          },
          "PUT /api/config/backup": (call) => {
            const body = call.body as typeof backup;
            if (body.max_per_app > 100) {
              return problem(422, "validation_error", "Validation failed", { fields: { max_per_app: "Input should be less than or equal to 100" } });
            }
            backup = body;
            return json(200, { message: "Backup configuration updated" });
          },
        },
      ),
    );
    const { user } = renderConsole("/settings");
    const name = await screen.findByLabelText(/Server name/);
    const count = screen.getByLabelText(/Backups kept per app/);
    await user.type(name, "web-1");
    await user.clear(count);
    await user.type(count, "500");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    // The name was saved; the count was refused and says why, where it is.
    expect(await screen.findByText("Input should be less than or equal to 100")).toBeInTheDocument();
    expect(count).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo("PATCH /api/config")[0]?.body).toEqual({ path: "server.name", value: "web-1" });
    await waitFor(() => {
      expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
    });

    await user.clear(count);
    await user.type(count, "20");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
    expect(backend.callsTo("PUT /api/config/backup").at(-1)?.body).toEqual({ directory: "/var/backups/noust", max_per_app: 20 });
    expect(count).toHaveValue("20");
  });

  it("discards every change at once", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(generalRoutes());
    const { user } = renderConsole("/settings");
    const directory = await screen.findByLabelText(/Applications folder/);
    await user.clear(directory);
    await user.type(directory, "/srv/apps");
    await user.click(screen.getByRole("checkbox", { name: /Check for new versions/ }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Discard" }));
    expect(directory).toHaveValue("/var/www/apps");
    expect(screen.getByRole("checkbox", { name: /Check for new versions/ })).toBeChecked();
    expect(backend.callsTo("PUT /api/config/apps-directory")).toHaveLength(0);
  });

  it("on a central that runs no applications, leaves out where applications live", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ central: { role: "hub" } }));
    renderConsole("/settings");
    expect(await screen.findByText("This central runs no applications")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Applications folder/)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Server name/)).toBeInTheDocument();
  });

  it("warns before anything is typed when the configuration file cannot be written", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({}, { "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: false }) }));
    renderConsole("/settings");
    expect(await screen.findByText("Saving will fail")).toBeInTheDocument();
  });
});
