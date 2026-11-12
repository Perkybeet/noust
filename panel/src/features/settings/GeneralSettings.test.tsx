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
    "GET /api/config/delivery": () => json(200, { max_concurrent_builds: 2, queue_limit: 25, max_minutes: 60 }),
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

describe("Settings > General: the policy", () => {
  it("is the policy only: versions, certificates, backups kept and how long a deployment may take, with one save bar, and passes axe", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ service_user: "www-data" }));
    const { container } = renderConsole("/settings/general");
    expect(await screen.findByLabelText(/Backups kept per app/)).toHaveValue("10");
    expect(screen.getByLabelText(/Longest a deployment may take/)).toHaveValue("60");
    expect(screen.getByLabelText(/Email for certificates/)).toHaveValue("ops@example.com");
    expect(screen.getByRole("checkbox", { name: /Check for new versions/ })).toBeChecked();
    expect(await screen.findByText(/the apt repository/)).toBeInTheDocument();
    // What is the machine's is Server's: neither its folders, nor its name, nor the details of its installation are here.
    expect(screen.queryByLabelText(/Applications folder/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Server name/)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Details" })).not.toBeInTheDocument();
    // One save bar, there from the start, with nothing to save.
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    expect(within(saveBar()).getByRole("button", { name: "Save" })).toBeDisabled();
    await expectNoAxeViolations(container);
  });

  it("saves every changed group from the one bar, and a refusal stays beside its field", { timeout: 20_000 }, async () => {
    let backup = { directory: "/var/backups/noust", max_per_app: 10 };
    const backend = fakeBackend(
      generalRoutes(
        {},
        {
          "GET /api/config/backup": () => json(200, backup),
          "PUT /api/config/ssl": () => json(200, { message: "SSL configuration updated" }),
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
    const { user } = renderConsole("/settings/general");
    const email = await screen.findByLabelText(/Email for certificates/);
    const count = screen.getByLabelText(/Backups kept per app/);
    await user.clear(email);
    await user.type(email, "sistemas@example.com");
    await user.clear(count);
    await user.type(count, "500");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    // The e-mail was saved; the count was refused and says why, where it is.
    expect(await screen.findByText("Input should be less than or equal to 100")).toBeInTheDocument();
    expect(count).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo("PUT /api/config/ssl")[0]?.body).toEqual({ enabled: true, provider: "certbot", email: "sistemas@example.com" });
    await waitFor(() => {
      expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
    });

    await user.clear(count);
    await user.type(count, "20");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
    // The folder, which is the machine's, goes back as it came: the endpoint replaces the whole block.
    expect(backend.callsTo("PUT /api/config/backup").at(-1)?.body).toEqual({ directory: "/var/backups/noust", max_per_app: 20 });
    expect(count).toHaveValue("20");
  });

  it("sets how long a deployment may take and leaves the machine's limits as they were", { timeout: 20_000 }, async () => {
    let limits = { max_concurrent_builds: 2, queue_limit: 25, max_minutes: 60 };
    const backend = fakeBackend(
      generalRoutes(
        {},
        {
          "GET /api/config/delivery": () => json(200, limits),
          "PUT /api/config/delivery": (call) => {
            const body = call.body as typeof limits;
            if (body.max_minutes > 1440) {
              return problem(422, "validation_error", "Validation failed", { fields: { max_minutes: "Input should be less than or equal to 1440" } });
            }
            limits = body;
            return json(200, { message: "Delivery limits updated" });
          },
        },
      ),
    );
    const { user } = renderConsole("/settings/general");
    const minutes = await screen.findByLabelText(/Longest a deployment may take/);
    expect(minutes).toHaveValue("60");
    expect(screen.queryByLabelText(/Builds at the same time/)).not.toBeInTheDocument();

    await user.clear(minutes);
    await user.type(minutes, "5000");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Input should be less than or equal to 1440")).toBeInTheDocument();
    expect(minutes).toHaveAttribute("aria-invalid", "true");

    await user.clear(minutes);
    await user.type(minutes, "0");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
    expect(backend.callsTo("PUT /api/config/delivery").at(-1)?.body).toEqual({ max_concurrent_builds: 2, queue_limit: 25, max_minutes: 0 });
    expect(minutes).toHaveValue("0");
  });

  it("discards every change at once", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(generalRoutes());
    const { user } = renderConsole("/settings/general");
    const email = await screen.findByLabelText(/Email for certificates/);
    await user.clear(email);
    await user.type(email, "otros@example.com");
    await user.click(screen.getByRole("checkbox", { name: /Check for new versions/ }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Discard" }));
    expect(email).toHaveValue("ops@example.com");
    expect(screen.getByRole("checkbox", { name: /Check for new versions/ })).toBeChecked();
    expect(backend.callsTo("PUT /api/config/ssl")).toHaveLength(0);
  });

  it("on a central that runs no applications, keeps only the notice about versions", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ central: { role: "hub" } }));
    renderConsole("/settings/general");
    expect(await screen.findByRole("checkbox", { name: /Check for new versions/ })).toBeInTheDocument();
    expect(screen.queryByLabelText(/Backups kept per app/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Email for certificates/)).not.toBeInTheDocument();
  });

  it("warns before anything is typed when the configuration file cannot be written", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({}, { "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: false }) }));
    renderConsole("/settings/general");
    expect(await screen.findByText("Saving will fail")).toBeInTheDocument();
  });
});

describe("Server > Folders and builds", () => {
  it("is where applications and backups live and how much it builds at once, with the details of the installation", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ service_user: "www-data" }));
    const { container } = renderConsole("/server/folders");
    expect(await screen.findByDisplayValue("/var/www/apps")).toBeInTheDocument();
    expect(screen.getByLabelText(/Backups folder/)).toHaveValue("/var/backups/noust");
    expect(screen.getByLabelText(/Builds at the same time/)).toHaveValue("2");
    expect(screen.getByLabelText(/Deployments that may wait/)).toHaveValue("25");
    // The policy is Settings': not here.
    expect(screen.queryByLabelText(/Backups kept per app/)).not.toBeInTheDocument();
    const details = screen.getByRole("region", { name: "Details" });
    expect(within(details).getByText("/etc/noust/config.yaml")).toBeInTheDocument();
    expect(within(details).getByText("noust web expose-hooks hooks.example.com")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("saves the folder of the backups with the number of backups kept as it was", { timeout: 20_000 }, async () => {
    const backend = fakeBackend(generalRoutes({}, { "PUT /api/config/backup": () => json(200, { message: "Backup configuration updated" }) }));
    const { user } = renderConsole("/server/folders");
    const folder = await screen.findByLabelText(/Backups folder/);
    await user.clear(folder);
    await user.type(folder, "/srv/backups");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/config/backup")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/config/backup")[0]?.body).toEqual({ directory: "/srv/backups", max_per_app: 10 });
  });

  it("on a central that runs no applications, says so", { timeout: 20_000 }, async () => {
    fakeBackend(generalRoutes({ central: { role: "hub" } }));
    renderConsole("/server/folders");
    expect(await screen.findByText("This central runs no applications")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Applications folder/)).not.toBeInTheDocument();
  });
});
