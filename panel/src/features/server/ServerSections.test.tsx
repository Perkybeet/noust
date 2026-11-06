/**
 * The sections of a server's page that 4.1 added or reshaped: what the central may do there, what
 * the applications keep between deployments, the server's SSH key and Noust's own update.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { WEB2, centralSession, fleetRoutes } from "../fleet/testFixtures";
import { onDesktop, serverRoutes } from "./testing";

beforeEach(onDesktop);

const SELF = { level: "deploy", host_access: false, fleet: true, token_name: "fleet-fra-1", central: "fra-1", updated_at: null };

/** A central that manages web-2, whose Noust publishes the capabilities listed. */
function onWeb2(path: string, paths: readonly string[], extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...fleetRoutes(centralSession(), [WEB2]),
    ...onNode("web-2", {
      ...serverRoutes(),
      "GET /api/openapi.json": () => json(200, { paths: Object.fromEntries(["/api/server/summary", ...paths].map((entry) => [entry, { get: {} }])) }),
      "GET /api/apps": () => json(200, { apps: [], total: 0 }),
    }),
    ...extra,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

describe("the central's permissions", () => {
  const SELF_PATH = "/api/nodes/web-2/api/auth/fleet/self";

  it("is in the server's column on a fleet, and says what this server lets the central do", async () => {
    onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, SELF) });

    // The page says whose central it is, then the two facts it is made of: the level, and whether it may touch how the server is reached.
    expect(await screen.findByText("What fra-1 can do here")).toBeInTheDocument();
    expect(await screen.findByText(/^Operate and update: /)).toBeInTheDocument();
    expect(screen.getByText("No: it cannot touch SSH, the firewall or install software")).toBeInTheDocument();
    const column = await screen.findByRole("navigation", { name: "Server sections" });
    await waitFor(() => {
      expect(within(column).getByRole("link", { name: "Central's permissions" })).toHaveAttribute("aria-current", "page");
    });
    // The level it has now is marked, in the fleet's own words.
    const now = screen.getByRole("radio", { name: /Operate and update/ });
    expect(now).toBeChecked();
    expect(within(now.closest("label") ?? now).getByText("Now")).toBeInTheDocument();
  });

  it("shows the command that sets another level, as it is chosen, and says only the server can run it", async () => {
    const { user } = onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, SELF) });
    await screen.findByText(/^Operate and update: /);
    expect(screen.getByText("This is what web-2 lets the central do now.")).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Read only/ }));
    expect(await screen.findByText("Change it on web-2")).toBeInTheDocument();
    expect(screen.getByText("noust fleet access --level read --host-access off")).toBeInTheDocument();
    expect(screen.getByText(/nothing the central does can raise it/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /Everything/ }));
    await user.click(screen.getByRole("checkbox", { name: /Also change how the server is reached/ }));
    expect(screen.getByText("noust fleet access --level admin --host-access on")).toBeInTheDocument();
  });

  it("does not offer host access below the level that allows everything, and says why", async () => {
    const { user } = onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, SELF) });
    await screen.findByText(/^Operate and update: /);
    await user.click(screen.getByRole("radio", { name: /Read only/ }));

    expect(screen.getByRole("checkbox", { name: /Also change how the server is reached/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("Only with the level that allows everything.")).toBeInTheDocument();
  });

  it("reads the server again on request, to learn the command was run", async () => {
    let level = "read";
    const { user, backend } = onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, { ...SELF, level }) });
    await screen.findByText(/^Read only: /);

    level = "admin";
    await user.click(screen.getByRole("button", { name: "Check again" }));

    expect(await screen.findByText(/^Everything: /)).toBeInTheDocument();
    expect(backend.callsTo(`GET ${SELF_PATH}`).length).toBeGreaterThanOrEqual(2);
  });

  it("says a server that publishes nothing does not say what it lets the central do", async () => {
    onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, { ...SELF, level: "from-the-future" }) });

    expect(await screen.findByText("web-2 does not say what it lets the central do.")).toBeInTheDocument();
  });

  it("says plainly that a server older than the console does not offer it", async () => {
    onWeb2("/n/web-2/server/central", []);

    expect(await screen.findByText(/^Not available on web-2/)).toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /Read only/ })).toBeNull();
  });

  it("is not in the column of the central itself, which no central drives", async () => {
    const backend = fakeBackend({ ...fleetRoutes(centralSession(), [WEB2]), ...serverRoutes() });
    renderConsole("/server");
    await screen.findByRole("heading", { level: 1 });

    const column = await screen.findByRole("navigation", { name: "Server sections" });
    expect(within(column).queryByRole("link", { name: "Central's permissions" })).toBeNull();
    expect(backend.callsTo("GET /api/nodes").length).toBeGreaterThan(0);
  });

  it("has no accessibility violations", async () => {
    onWeb2("/n/web-2/server/central", ["/api/auth/fleet/self"], { [`GET ${SELF_PATH}`]: () => json(200, SELF) });
    await screen.findByText(/^Operate and update: /);

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("what the applications keep between deployments", () => {
  function files(extra: Record<string, RouteHandler>) {
    const backend = fakeBackend({
      ...signedInRoutes(),
      ...serverRoutes(),
      "GET /api/apps": () => json(200, { apps: [{ domain: "shop.example.net", name: "shop", active: true, enabled: true }], total: 1 }),
      "GET /api/volume-backups/targets/shop.example.net": () => json(200, []),
      ...extra,
    });
    const harness = renderConsole("/server/files");
    return { ...harness, backend };
  }

  it("lists each application's folders kept beside its releases and the files bound into it, with where they are, and links to the application", async () => {
    files({
      "GET /api/volume-backups/targets/shop.example.net": () =>
        json(200, [{ declared_by: "persistent", exists: true, key: "storage/app", kind: "path", path: "storage/app", slug: "storage-app", source: "/var/www/apps/shop/shared/storage/app" }]),
      "GET /api/apps/shop.example.net/mounts": () =>
        json(200, {
          container: false,
          domain: "shop.example.net",
          total: 1,
          mounts: [{ exists: true, kind: "dir", mode: "0755", path: "/var/www/apps/shop/shared/uploads", read_only: false, size: 5 * 1024 ** 2, source: "/var/www/apps/shop/shared/uploads", target: "uploads" }],
        }),
    });

    const table = await screen.findByRole("table", { name: "Folders and files the applications keep between deployments" });
    const row = await within(table).findByRole("row", { name: /uploads/ });
    expect(within(row).getByRole("link", { name: "shop.example.net" })).toHaveAttribute("href", "/apps/shop.example.net");
    expect(within(row).getByText("5.0 MB")).toBeInTheDocument();
    // What the releases share is listed beside what is bound into the application.
    const shared = within(table).getByRole("row", { name: /storage\/app/ });
    expect(within(shared).getByText("/var/www/apps/shop/shared/storage/app")).toBeInTheDocument();
  });

  it("says no application keeps a folder yet, and where to add one", async () => {
    files({ "GET /api/apps/shop.example.net/mounts": () => json(200, { container: false, domain: "shop.example.net", total: 0, mounts: [] }) });

    expect(await screen.findByText("No application keeps a folder yet. Add one in the settings of an application.")).toBeInTheDocument();
  });

  it("does not ask a stack or a static site for its mounts, which the server refuses, and still lists the volumes of a stack", async () => {
    const { backend } = files({
      "GET /api/apps": () =>
        json(200, {
          apps: [
            { domain: "shop.example.net", name: "shop", active: true, enabled: true, app_type: "nodejs" },
            { domain: "queue.example.net", name: "queue", active: true, enabled: true, app_type: "docker-compose" },
            { domain: "docs.example.net", name: "docs", active: true, enabled: true, app_type: "static" },
          ],
          total: 3,
        }),
      "GET /api/apps/shop.example.net/mounts": () => json(200, { container: false, domain: "shop.example.net", total: 0, mounts: [] }),
      "GET /api/volume-backups/targets/queue.example.net": () => json(200, [{ declared_by: "compose", exists: true, key: "queue_data", kind: "volume", path: null, slug: "queue-data", source: "queue_data" }]),
      "GET /api/volume-backups/targets/docs.example.net": () => json(200, []),
    });

    const table = await screen.findByRole("table", { name: "Folders and files the applications keep between deployments" });
    // The volume is named in the folder column and again as the place it is stored.
    expect((await within(table).findAllByText("queue_data")).length).toBeGreaterThan(0);
    expect(backend.callsTo("GET /api/apps/shop.example.net/mounts")).toHaveLength(1);
    expect(backend.callsTo("GET /api/apps/queue.example.net/mounts")).toHaveLength(0);
    expect(backend.callsTo("GET /api/apps/docs.example.net/mounts")).toHaveLength(0);
  });

  it("says a folder that is declared and not created yet", async () => {
    files({
      "GET /api/apps/shop.example.net/mounts": () =>
        json(200, { container: false, domain: "shop.example.net", total: 1, mounts: [{ exists: false, kind: "dir", mode: "0755", path: "/x", read_only: false, size: null, source: "/x", target: "media" }] }),
    });

    expect(await screen.findByText("Not created yet")).toBeInTheDocument();
  });
});

describe("the server's SSH key", () => {
  function ssh(extra: Record<string, RouteHandler>) {
    const backend = fakeBackend({ ...signedInRoutes(), ...serverRoutes(), ...extra });
    const harness = renderConsole("/server/security?view=ssh");
    return { ...harness, backend };
  }

  const KEY = { public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPublicHalf noust", fingerprint: "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG" };

  it("makes the key from the console when the server has none, and shows its public half to copy", async () => {
    const { user, backend } = ssh({
      "GET /api/deploy-keys/server": () => json(200, { public_key: null, fingerprint: null }),
      "POST /api/deploy-keys/server": () => json(201, KEY),
    });
    expect(await screen.findByText("This server has no key yet.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create the key" }));

    expect(await screen.findByText(KEY.public_key)).toBeInTheDocument();
    expect(screen.getByText(KEY.fingerprint)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/deploy-keys/server")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Create the key" })).toBeNull();
  });

  it("shows the key a server has and offers no way to replace it", async () => {
    ssh({ "GET /api/deploy-keys/server": () => json(200, KEY) });

    expect(await screen.findByText(KEY.public_key)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create the key" })).toBeNull();
    expect(screen.getByText("Add it at the code host as a deploy key or as a key of your account.")).toBeInTheDocument();
  });

  it("says why when the key could not be made, in the system's own words", async () => {
    const { user } = ssh({
      "GET /api/deploy-keys/server": () => json(200, { public_key: null, fingerprint: null }),
      "POST /api/deploy-keys/server": () => problem(400, "source_error", "The server's SSH key could not be made", { hint: "Saving key failed: Permission denied. Run `noust setup ssh --generate` on the server to see why." }),
    });
    await user.click(await screen.findByRole("button", { name: "Create the key" }));

    await waitFor(() => {
      expect(screen.getAllByText(/Permission denied/).length).toBeGreaterThan(0);
    });
    expect(screen.getByRole("button", { name: "Create the key" })).toBeInTheDocument();
  });
});

describe("Noust's own update, beside the system's", () => {
  function system(extra: Record<string, RouteHandler>) {
    const backend = fakeBackend({ ...signedInRoutes(), ...serverRoutes(), ...extra });
    const harness = renderConsole("/server/system");
    return { ...harness, backend };
  }

  const VERSION = { current_version: "3.3.1", has_update: true, latest_version: "4.0.0", security_update: false };
  const METHOD = { current_version: "3.3.1", method: "pip", supported: true, command: ["pip", "install", "--upgrade", "noust"], last_run: null };

  it("says which Noust runs and that a newer one is out, and updates it as a job", async () => {
    const { user, backend } = system({
      "GET /api/system/version": () => json(200, VERSION),
      "GET /api/system/update": () => json(200, METHOD),
      "POST /api/system/update": () => json(202, { job_id: "j9", message: "queued", status: "pending" }),
    });
    // The version is set in mono inside the sentence, so the sentence is read from the paragraph.
    expect(await screen.findByText((_, element) => element?.tagName === "P" && element.textContent === "This server runs Noust 3.3.1.")).toBeInTheDocument();
    expect(screen.getByText("4.0.0 available")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Update Noust" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/system/update")).toHaveLength(1);
    });
  });

  it("keeps the system's updates the view's primary action, and Noust's a secondary one", async () => {
    system({ "GET /api/system/version": () => json(200, VERSION), "GET /api/system/update": () => json(200, METHOD) });
    const noust = await screen.findByRole("button", { name: "Update Noust" });
    // The page's one primary action installs what is waiting; the security ones beside it and Noust's own are secondary.
    const everything = await screen.findByRole("button", { name: /^Install all/ });
    const security = await screen.findByRole("button", { name: /Install security updates/ });

    expect(noust.className).not.toEqual(everything.className);
    expect(noust.className).toEqual(security.className);
  });

  it("says Noust is up to date, with no button", async () => {
    system({ "GET /api/system/version": () => json(200, { ...VERSION, has_update: false, latest_version: "3.3.1" }), "GET /api/system/update": () => json(200, METHOD) });

    expect(await screen.findByText("Up to date")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update Noust" })).toBeNull();
  });

  it("says an installation the console does not update, and the command to run instead", async () => {
    system({
      "GET /api/system/version": () => json(200, VERSION),
      "GET /api/system/update": () =>
        json(200, { ...METHOD, method: "unknown", supported: false, reason: "Noust runs from a container image here", hint: "docker compose pull && docker compose up -d", command: null }),
    });

    expect(await screen.findByText("Noust runs from a container image here")).toBeInTheDocument();
    expect(screen.getByText("docker compose pull && docker compose up -d")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Update Noust" })).toBeNull();
  });

  it("opens on the updates, and the machine, the processes and the resources are sections beside them", async () => {
    const { user } = system({ "GET /api/system/version": () => json(200, VERSION), "GET /api/system/update": () => json(200, METHOD) });
    const sections = await screen.findByRole("navigation", { name: "Server sections" });
    await waitFor(() => {
      expect(within(sections).getByRole("link", { name: /^Updates/ })).toHaveAttribute("aria-current", "page");
    });
    // The views are the column's sections now, and the page draws no switch of them.
    expect(screen.queryByRole("radio", { name: "Updates" })).not.toBeInTheDocument();

    await user.click(within(sections).getByRole("link", { name: "Machine" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Machine" })).toBeInTheDocument();
    expect(await screen.findByText("Kernel")).toBeInTheDocument();
  });

  it("keeps the 4.0 and 4.1 addresses of the updates and of the storage, which go to where they are now", async () => {
    fakeBackend({ ...signedInRoutes(), ...serverRoutes() });
    const { location } = renderConsole("/server/system");
    await waitFor(() => {
      expect(location().pathname).toBe("/server/updates");
    });
    expect(await screen.findByRole("navigation", { name: "Server sections" })).toBeInTheDocument();
  });
});
