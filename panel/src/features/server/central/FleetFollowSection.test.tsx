/**
 * A server's page "With the central": what it holds of the fleet's settings, and what only it has (item 166), read from the
 * server through the central's proxy.
 */

import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, onNode } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { WEB2, centralSession, fleetRoutes } from "../../fleet/testFixtures";
import { onDesktop, serverRoutes } from "../testing";

beforeEach(onDesktop);

const SELF = { level: "deploy", host_access: false, fleet: true, token_name: "fleet-fra-1", central: "fra-1", updated_at: null };
const ADMIN = centralSession({}, { ...SESSION, permissions: ["self", "apps.read", "fleet.read", "fleet.manage", "settings.read", "settings.manage"] });

const status = {
  generated_at: "2026-10-29T10:00:00Z",
  partial: false,
  servers: [{ node: "web-2", mode: "managed", outcome: "ok", message: null, code: null, hint: null, pending_look: false }],
  settings: [
    {
      resource: "channel",
      name: "slack-deploys",
      version: 3,
      change: "c1",
      changed_by: "marta",
      changed_at: "2026-10-29T10:00:00Z",
      exceptions: [],
      following: 1,
      of: 1,
      servers: [
        {
          node: "web-2",
          origin: "fleet",
          sync: "in_sync",
          wanted_version: 3,
          applied_version: 3,
          waiting: false,
          state: "managed",
          restore_requested: false,
          reach: { reaches: true, why: null },
        },
      ],
    },
  ],
  released: [],
};

function onWeb2(extra: Record<string, RouteHandler> = {}) {
  fakeBackend({
    ...fleetRoutes(ADMIN, [WEB2]),
    "GET /api/fleet/settings": () => json(200, { kinds: [], items: [] }),
    "GET /api/fleet/settings/status": () => json(200, status),
    ...onNode("web-2", {
      ...serverRoutes(),
      "GET /api/openapi.json": () => json(200, { paths: { "/api/server/summary": { get: {} }, "/api/auth/fleet/self": { get: {} } } }),
      "GET /api/apps": () => json(200, { apps: [], total: 0 }),
      "GET /api/auth/fleet/self": () => json(200, SELF),
    }),
    ...extra,
  });
  return renderConsole("/n/web-2/server/central");
}

describe("what only a server has, on its page with the central", () => {
  it("lists, with the kind of each, what the server keeps and the fleet does not know, and leaves out what the fleet carries", async () => {
    onWeb2({
      "GET /api/nodes/web-2/api/notifications/channels": () =>
        json(200, { enabled: true, items: [{ name: "slack-deploys", type: "slack", configured: true }, { name: "pager-web-2", type: "webhook", configured: true }, { name: "email", type: "email", configured: false }], types: [] }),
      "GET /api/nodes/web-2/api/alerts/rules": () => json(200, { items: [{ name: "disk-web-2" }], firing: 0, metrics: [] }),
      "GET /api/nodes/web-2/api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
      "GET /api/nodes/web-2/api/registries": () => json(200, { logins: [], kinds: [] }),
      "GET /api/nodes/web-2/api/variables/server": () => json(200, { variables: [] }),
      "GET /api/nodes/web-2/api/notifications/console-url": () => json(200, { url: "" }),
    });
    const holds = await screen.findByRole("region", { name: "What it holds" });
    const scope = within(holds);
    expect(await scope.findByText("Only it has")).toBeInTheDocument();
    expect(await scope.findByText("pager-web-2")).toBeInTheDocument();
    expect(scope.getByText("disk-web-2")).toBeInTheDocument();
    expect(scope.getByText("Channel")).toBeInTheDocument();
    expect(scope.getByText("Alert rule")).toBeInTheDocument();
    // What the fleet carries is not "only here", and a channel that is not set up is not held.
    expect(scope.queryByText("slack-deploys")).toBeNull();
    expect(scope.queryByText("email")).toBeNull();
    await expectNoAxeViolations(holds);
  });

  it("draws no row when the server holds nothing of its own", async () => {
    onWeb2({
      "GET /api/nodes/web-2/api/notifications/channels": () => json(200, { enabled: true, items: [{ name: "slack-deploys", type: "slack", configured: true }], types: [] }),
      "GET /api/nodes/web-2/api/alerts/rules": () => json(200, { items: [], firing: 0, metrics: [] }),
      "GET /api/nodes/web-2/api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
      "GET /api/nodes/web-2/api/registries": () => json(200, { logins: [], kinds: [] }),
      "GET /api/nodes/web-2/api/variables/server": () => json(200, { variables: [] }),
      "GET /api/nodes/web-2/api/notifications/console-url": () => json(200, { url: "" }),
    });
    const holds = await screen.findByRole("region", { name: "What it holds" });
    await within(holds).findByText("Follows");
    expect(within(holds).queryByText("Only it has")).toBeNull();
  });
});
