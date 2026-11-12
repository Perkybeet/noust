import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { NODES, SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";

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

const KINDS = [
  { key: "variable", title: "Shared variable", available: true, permission: "settings.manage", operations: [], reason: null },
  { key: "channel", title: "Notification channel", available: true, permission: "settings.manage", operations: [], reason: null },
];

const item = (resource: string, name: string, scope = "fleet", value: Record<string, unknown> = { value: "Europe/Madrid", secret: false }) => ({
  resource,
  name,
  scope,
  node: scope === "fleet" ? null : scope.replace("node:", ""),
  version: 3,
  value,
  secrets: [],
  change: "c1",
  changed_by: "marta",
  changed_at: "2026-10-29T10:00:00Z",
});

const cell = (node: string, origin: string, sync: string, extra: Record<string, unknown> = {}) => ({
  node,
  origin,
  sync,
  wanted_version: 3,
  applied_version: 3,
  waiting: false,
  state: "managed",
  restore_requested: false,
  reach: { reaches: true, why: null },
  ...extra,
});

/** A fleet of the central, web-2 (answers) and db-1 (does not): the server's own count says one of two follow TZ. */
function status(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-10-29T10:00:00Z",
    partial: false,
    servers: [
      { node: "web-2", mode: "managed", outcome: "ok", message: null, code: null, hint: null, pending_look: false },
      { node: "db-1", mode: "managed", outcome: "unreachable", message: null, code: null, hint: null, pending_look: false },
    ],
    settings: [
      {
        resource: "variable",
        name: "TZ",
        version: 3,
        change: "c1",
        changed_by: "marta",
        changed_at: "2026-10-29T10:00:00Z",
        exceptions: [],
        following: 1,
        of: 2,
        servers: [cell("web-2", "fleet", "in_sync"), cell("db-1", "fleet", "unreachable", { applied_version: 2, waiting: true })],
      },
    ],
    released: [],
    ...overrides,
  };
}

const variables = { scope: "server", label: "Server", unmasked: false, syntax: "{{server.NAME}}", variables: [] };

function backend(extra: Record<string, RouteHandler> = {}, nodes: readonly unknown[] = NODES) {
  return fakeBackend({
    ...signedInRoutes(ADMIN),
    "GET /api/nodes": () => json(200, { items: nodes }),
    "GET /api/variables/server": () => json(200, variables),
    "GET /api/fleet/settings": () => json(200, { kinds: KINDS, items: [item("variable", "TZ")] }),
    "GET /api/fleet/settings/status": () => json(200, status()),
    "POST /api/auth/elevate": () => json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() }),
    ...extra,
  });
}

describe("the settings the central carries, in the section they belong to", () => {
  it("says how many servers follow a setting as the server counts them, and what stands in the way once for the page", { timeout: 30_000 }, async () => {
    backend();
    const { container } = renderConsole("/settings/variables");
    const block = await screen.findByRole("region", { name: "Variables for the whole fleet" });
    // The count is the server's: the central is the source and is not one of the two.
    expect(await within(block).findByRole("button", { name: "1 of 2 servers follows it" })).toBeInTheDocument();
    // What is true of this setting on a server (a change waiting for one that does not answer) is a mark on the row.
    expect(within(block).getByText("will get it when it is back")).toBeInTheDocument();
    // One strip under the title says what stands in the way of a server, whatever the setting.
    const strip = container.querySelector('[data-slot="node-strip"]');
    expect(strip).not.toBeNull();
    expect(within(strip as HTMLElement).getByRole("link", { name: "Open the page of db-1" })).toHaveAttribute("href", "/n/db-1/server/central");
    // The selector is a label that says whose the page is: no control, and a "?" that says why.
    expect(screen.getByRole("group", { name: /the central\. Not chosen here$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Server:/ })).toBeNull();
    expect(screen.queryByText("All the fleet")).toBeNull();
    await expectNoAxeViolations(container);
  });

  it("opens a panel that is a table of servers, one face each, and says why it is so", { timeout: 30_000 }, async () => {
    backend();
    const { user } = renderConsole("/settings/variables");
    await user.click(await screen.findByRole("button", { name: "1 of 2 servers follows it" }));
    const panel = await screen.findByRole("dialog", { name: "TZ" });
    const table = within(panel).getByRole("table", { name: "Servers of TZ" });
    const web = within(table).getByRole("row", { name: /web-2/ });
    expect(within(web).getByText("Follows")).toBeInTheDocument();
    expect(within(web).getByText("Holds the fleet's value.")).toBeInTheDocument();
    const db = within(table).getByRole("row", { name: /db-1/ });
    expect(within(db).getByText("No connection")).toHaveAttribute("data-state", "failed");
    expect(within(db).getByText("Not answering. It gets the change when it is back.")).toBeInTheDocument();
  });

  it("says before saving what the change does on each server", { timeout: 30_000 }, async () => {
    backend();
    const { user } = renderConsole("/settings/variables");
    const block = await screen.findByRole("region", { name: "Variables for the whole fleet" });
    await user.click(await within(block).findByRole("button", { name: "Actions for TZ" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit for the whole fleet…" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit TZ for the fleet" });
    const plan = within(dialog).getByRole("group", { name: "What happens when it is saved" });
    expect(within(within(plan).getByText("web-2").closest("li") as HTMLElement).getByText("Gets it now")).toBeInTheDocument();
    expect(within(within(plan).getByText("db-1").closest("li") as HTMLElement).getByText("Gets it when it is back")).toBeInTheDocument();
  });

  it("makes a value for one server from its row, and applies one setting alone", { timeout: 30_000 }, async () => {
    const calls = backend({
      "PUT /api/fleet/settings/variable/TZ": (call: RecordedCall) => json(200, { setting: item("variable", "TZ", "node:web-2", (call.body as { value: Record<string, unknown> }).value), apply: { job_id: "j1", nodes: ["web-2"] } }),
      "POST /api/fleet/settings/apply": () => json(200, { plan: { nodes: [], summary: { run: 1, skipped: 0 } }, job: null }),
    });
    const { user } = renderConsole("/settings/variables");
    await user.click(await screen.findByRole("button", { name: "1 of 2 servers follows it" }));
    const panel = await screen.findByRole("dialog", { name: "TZ" });
    await user.click(within(panel).getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Give it a value of its own…" }));
    const dialog = await screen.findByRole("dialog", { name: "TZ on web-2" });
    const value = within(dialog).getByLabelText(/^Value/);
    await user.clear(value);
    await user.type(value, "UTC");
    await user.click(within(dialog).getByRole("button", { name: "Save and apply" }));
    await waitFor(() => {
      expect(calls.callsTo("PUT /api/fleet/settings/variable/TZ")).toHaveLength(1);
    });
    expect(calls.callsTo("PUT /api/fleet/settings/variable/TZ")[0]?.body).toEqual({ value: { value: "UTC", secret: false }, scope: "node:web-2", apply: true });
  });

  it("says what a server keeps for itself and the fleet does not know, with the way into its page", { timeout: 30_000 }, async () => {
    const calls = backend({
      // The server holds the fleet's TZ and one variable of its own; db-1 does not answer, so nobody asks it.
      "GET /api/nodes/web-2/api/variables/server": () => json(200, { ...variables, variables: [{ name: "TZ", value: "UTC" }, { name: "LOCAL_ONLY", value: "x" }] }),
    });
    const { container } = renderConsole("/settings/variables");
    const block = await screen.findByRole("region", { name: "Variables for the whole fleet" });
    const only = await within(block).findByRole("list", { name: "Only on one server" });
    // One row: getByRole throws on two.
    const row = within(only).getByRole("listitem");
    expect(within(row).getByText("LOCAL_ONLY")).toBeInTheDocument();
    expect(row).toHaveTextContent("Only web-2 has it. The fleet does not know it.");
    expect(within(row).getByRole("link", { name: "Open the page of web-2" })).toHaveAttribute("href", "/n/web-2/server/central");
    expect(within(only).queryByText("TZ")).toBeNull();
    expect(calls.callsTo("GET /api/nodes/db-1/api/variables/server")).toHaveLength(0);
    await expectNoAxeViolations(container);
  });

  it("says nothing of what a server keeps when everything it holds is the fleet's, or it cannot be read", { timeout: 30_000 }, async () => {
    const calls = backend({
      "GET /api/nodes/web-2/api/variables/server": () => json(403, { error: { code: "forbidden", message: "No", hint: null } }),
    });
    renderConsole("/settings/variables");
    const block = await screen.findByRole("region", { name: "Variables for the whole fleet" });
    await within(block).findByRole("button", { name: "1 of 2 servers follows it" });
    await waitFor(() => {
      expect(calls.callsTo("GET /api/nodes/web-2/api/variables/server").length).toBeGreaterThan(0);
    });
    expect(within(block).queryByRole("list", { name: "Only on one server" })).toBeNull();
  });

  it("draws no line where the central does not say the count, and still marks what differs", { timeout: 30_000 }, async () => {
    const old = status();
    for (const row of old.settings) {
      delete (row as Record<string, unknown>)["following"];
      delete (row as Record<string, unknown>)["of"];
      for (const one of row.servers) delete (one as Record<string, unknown>)["waiting"];
    }
    backend({ "GET /api/fleet/settings/status": () => json(200, old) });
    renderConsole("/settings/variables");
    const block = await screen.findByRole("region", { name: "Variables for the whole fleet" });
    // A central of 4.2 sends no count and no `waiting`: the versions it always sent say what waits.
    expect(await within(block).findByText("will get it when it is back")).toBeInTheDocument();
    expect(within(block).queryByText(/servers? follows? it/)).toBeNull();
  });

  it("is not there on a lone server, which sees the section as it always was", { timeout: 30_000 }, async () => {
    backend({}, []);
    renderConsole("/settings/variables");
    expect(await screen.findByRole("heading", { level: 1, name: "Variables" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByText("Variables for the whole fleet")).toBeNull();
    });
    expect(screen.queryByRole("group", { name: /the central\. Not chosen here$/ })).toBeNull();
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    backend();
    renderConsole("/settings/variables");
    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Lo sigue 1 de 2 servidores" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Variables para toda la flota" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
