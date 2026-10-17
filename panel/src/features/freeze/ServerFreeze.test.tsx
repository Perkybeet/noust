import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const SERVER_FREEZE = {
  id: 8,
  scope: "server",
  domain: null,
  reason: "Datacentre move",
  starts_at: "2026-10-16T08:00:00+00:00",
  ends_at: "2999-01-01T00:00:00+00:00",
  created_by: "ana",
  created_at: "2026-10-16T08:00:00+00:00",
  lifted_at: null,
  lifted_by: null,
  state: "active",
};
const APP_FREEZE = { ...SERVER_FREEZE, id: 9, scope: "shop.example.com", domain: "shop.example.com", reason: "Only the shop" };

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(ELEVATED),
    "GET /api/config": () => json(200, { config: {}, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/config/apps-directory": () => json(200, { apps_directory: "/var/www/apps" }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/config/ssl": () => json(200, { enabled: true, provider: "certbot", email: "ops@example.com" }),
    "GET /api/config/backup": () => json(200, { directory: "/var/backups/noust", max_per_app: 10 }),
    "GET /api/config/web": () => json(200, { host: "127.0.0.1", port: 8080, session_timeout: 3600 }),
    "GET /api/system/update": () => json(200, { current_version: "3.1.0", method: "apt", supported: true }),
    ...extra,
  };
}

async function general(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend(routes(extra));
  const harness = renderConsole("/settings");
  await screen.findByDisplayValue("/var/www/apps");
  return { ...harness, backend };
}

describe("the freeze of the whole server, in Settings > General", { timeout: 20_000 }, () => {
  it("lists the server's freezes and not an app's", async () => {
    await general({ "GET /api/deploy-freezes": () => json(200, { freezes: [SERVER_FREEZE, APP_FREEZE] }) });

    const section = await screen.findByRole("region", { name: "Deploy freeze" });
    const list = await within(section).findByRole("list", { name: "Freeze deploys" });
    expect(within(list).getByText("Datacentre move")).toBeInTheDocument();
    expect(within(list).queryByText("Only the shop")).not.toBeInTheDocument();
    await expectNoAxeViolations(section);
  });

  it("freezes the whole server, with no application named", async () => {
    const { user, backend } = await general({
      "GET /api/deploy-freezes": () => json(200, { freezes: [] }),
      "POST /api/deploy-freezes": () => json(201, SERVER_FREEZE),
    });

    const section = await screen.findByRole("region", { name: "Deploy freeze" });
    expect(await within(section).findByText("No freeze is set.")).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Freeze deploys" }));
    const dialog = await screen.findByRole("dialog", { name: "Freeze deploys on this server" });
    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Datacentre move");
    await user.click(within(dialog).getByRole("button", { name: "Freeze" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/deploy-freezes")[0]?.body).toEqual({ reason: "Datacentre move", hours: 4 });
    });
  });

  it("is not shown by a server that has no freezes", async () => {
    await general({});

    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "Deploy freeze" })).not.toBeInTheDocument();
    });
  });
});
