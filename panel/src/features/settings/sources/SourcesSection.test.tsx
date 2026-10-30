import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { NODES, SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";

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

const ADMIN = { ...SESSION, permissions: ["self", "apps.read", "fleet.read", "fleet.manage", "settings.read", "settings.manage"] };

const SOURCES = {
  github: { configured: true, host: "github.com", hooks_url: "https://hooks.example.com/hooks", warnings: [] },
  nodes: [
    { node: "web-2", state: "leased", leases: [{ key: "k1", state: "leased", installation_id: 1, repositories: ["acme/web", "acme/api"], expires_in: 2400, host: "github.com" }], uncovered: [], problems: [], error: null, checked_at: "2026-10-29T10:00:00Z" },
    {
      node: "db-1",
      state: "ceiling",
      leases: [],
      uncovered: ["acme/db"],
      problems: [{ text: "db-1 only lets this central read", hint: "Raise it with noust fleet access --level deploy", coded: { code: "sources.node_ceiling", params: { node: "db-1", command: "noust fleet access --level deploy" } } }],
      error: null,
      checked_at: "2026-10-29T10:00:00Z",
    },
  ],
  deliveries: { pending: 1, delivered: 12, failed: 1, expired: 0 },
  following: 2,
};

const FAILED = {
  items: [{ id: "f1", delivery: "d1", kind: "push", repository: "acme/web", ref: "main", state: "failed", attempts: 4, node: "web-2", created_at: "2026-10-29T09:00:00Z", jobs: [], follows: [] }],
  total: 1,
  counts: { pending: 1, delivered: 12, failed: 1, expired: 0 },
};

function central(extra: Record<string, RouteHandler> = {}) {
  return fakeBackend({
    ...signedInRoutes(ADMIN),
    "GET /api/nodes": () => json(200, { items: NODES }),
    "GET /api/fleet/sources": () => json(200, SOURCES),
    "GET /api/fleet/sources/deliveries": () => json(200, FAILED),
    ...extra,
  });
}

describe("Settings > Connections, on a central", () => {
  it("says how each server reaches its repositories, what keeps one from it, and passes axe", { timeout: 90_000 }, async () => {
    central();
    const { container } = renderConsole("/settings/integrations");
    const table = await screen.findByRole("region", { name: "How each server reaches its repositories" });
    const rowOf = (name: string) => {
      const found = within(table).getByText(name).closest("tr");
      if (found === null) throw new Error(`no row for ${name}`);
      return within(found);
    };
    // A server with a token is plain text and says how many repositories it holds one for.
    expect(rowOf("web-2").getByText("Has a token")).toBeInTheDocument();
    expect(rowOf("web-2").getByText("2 tokens")).toBeInTheDocument();
    // What keeps another from one: its own limit, in the console's language from the server's code, with the fix.
    expect(rowOf("db-1").getByText("Limited by the server")).toHaveAttribute("data-state", "warning");
    expect(rowOf("db-1").getByText("1 repository without one")).toBeInTheDocument();
    expect(screen.getByText("12 delivered")).toBeInTheDocument();
    expect(screen.getByText("1 waiting")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("renews the tokens of every server now", { timeout: 90_000 }, async () => {
    const calls = central({ "POST /api/fleet/sources/leases": () => json(200, { nodes: SOURCES.nodes }) });
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Renew now" }));
    await waitFor(() => {
      expect(calls.callsTo("POST /api/fleet/sources/leases")).toHaveLength(1);
    });
    expect(calls.callsTo("POST /api/fleet/sources/leases")[0]?.body).toEqual({ force: true });
  });

  it("renews the tokens of one server now", { timeout: 90_000 }, async () => {
    const calls = central({ "POST /api/fleet/sources/leases": () => json(200, { nodes: SOURCES.nodes }) });
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Renew the tokens of web-2" }));
    await waitFor(() => {
      expect(calls.callsTo("POST /api/fleet/sources/leases")).toHaveLength(1);
    });
    expect(calls.callsTo("POST /api/fleet/sources/leases")[0]?.body).toEqual({ force: true, nodes: ["web-2"] });
  });

  it("retries a push that did not arrive", { timeout: 90_000 }, async () => {
    const calls = central({ "POST /api/fleet/sources/deliveries/f1/retry": () => json(200, FAILED.items[0]) });
    const { user } = renderConsole("/settings/integrations");
    await user.click(await screen.findByRole("button", { name: "Retry the push of acme/web to web-2" }));
    await waitFor(() => {
      expect(calls.callsTo("POST /api/fleet/sources/deliveries/f1/retry")).toHaveLength(1);
    });
  });

  it("is not drawn on a server with no fleet", { timeout: 90_000 }, async () => {
    fakeBackend({ ...signedInRoutes(ADMIN), "GET /api/fleet/sources": () => json(200, SOURCES) });
    renderConsole("/settings/integrations");
    await screen.findByRole("heading", { level: 1, name: "Connections" });
    expect(screen.queryByText("Code on your servers")).toBeNull();
  });
});
