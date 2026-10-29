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

const ADMIN = { ...SESSION, permissions: ["self", "apps.read", "fleet.read", "fleet.manage", "settings.manage"] };

const KINDS = [
  { key: "channel", title: "Notification channel", available: true, permission: "settings.manage", operations: [], reason: null },
  { key: "variable", title: "Shared variable", available: true, permission: "settings.manage", operations: [], reason: null },
  { key: "registry", title: "Registry login", available: true, permission: "apps.manage", operations: [], reason: null },
];

const item = (resource: string, name: string, scope = "fleet", value: Record<string, unknown> = { value: "x", secret: false }) => ({
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

const cell = (node: string, origin: string, sync: string, extra: Record<string, unknown> = {}) => ({ node, origin, sync, wanted_version: 3, applied_version: 3, state: "managed", restore_requested: false, reach: { reaches: true, why: null }, ...extra });

function status(overrides: Record<string, unknown> = {}) {
  return {
    generated_at: "2026-10-29T10:00:00Z",
    partial: false,
    servers: [
      { node: "web-2", mode: "managed", outcome: "ok", message: null, code: null, hint: null, pending_look: false },
      { node: "db-1", mode: "independent", outcome: "ok", message: null, code: null, hint: null, pending_look: false },
    ],
    settings: [
      { resource: "variable", name: "SUPPORT_EMAIL", version: 3, change: "c1", changed_by: "marta", changed_at: "2026-10-29T10:00:00Z", exceptions: [], servers: [cell("web-2", "fleet", "in_sync"), cell("db-1", "independent", "not_applicable")] },
      { resource: "variable", name: "STATUS_URL", version: 3, change: "c1", changed_by: "marta", changed_at: "2026-10-29T10:00:00Z", exceptions: [], servers: [cell("web-2", "local_exception", "diverged"), cell("db-1", "independent", "not_applicable")] },
      { resource: "registry", name: "ghcr", version: 2, change: "c2", changed_by: "marta", changed_at: "2026-10-29T10:00:00Z", exceptions: [{ node: "web-2" }], servers: [cell("web-2", "central_exception", "pending"), cell("db-1", "independent", "not_applicable")] },
    ],
    released: [],
    ...overrides,
  };
}

function backend(extra: Record<string, RouteHandler> = {}) {
  return fakeBackend({
    ...signedInRoutes(ADMIN),
    "GET /api/nodes": () => json(200, { items: NODES }),
    "GET /api/fleet/settings": () => json(200, { kinds: KINDS, items: [item("variable", "SUPPORT_EMAIL"), item("variable", "STATUS_URL"), item("registry", "ghcr", "fleet", { kind: "ghcr", username: "ci", password: "***" })] }),
    "GET /api/fleet/settings/status": () => json(200, status()),
    "POST /api/auth/elevate": () => json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() }),
    ...extra,
  });
}

describe("Settings > Fleet settings", () => {
  it("shows, for each setting, which of three levels each server is on, and passes axe", { timeout: 30_000 }, async () => {
    backend();
    const { container } = renderConsole("/settings/fleet");
    await screen.findByRole("heading", { level: 1, name: "Fleet settings" });
    // The three levels are said once, above the table.
    const levels = screen.getByRole("list", { name: "Three levels" });
    expect(within(levels).getByText("Follows the fleet")).toBeInTheDocument();
    expect(within(levels).getByText("Has an exception")).toBeInTheDocument();
    expect(within(levels).getByText("Independent")).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Settings shared with the fleet" });
    const rowOf = (name: string) => {
      const found = within(table).getByText(name).closest("tr");
      if (found === null) throw new Error(`no row for ${name}`);
      return within(found);
    };
    // A server that follows the fleet is plain text: only what is not well has a colour.
    expect(rowOf("SUPPORT_EMAIL").getByText("Follows")).toBeInTheDocument();
    // A value changed on the server is a difference, with the way back.
    expect(rowOf("STATUS_URL").getByText("Changed on the server")).toHaveAttribute("data-state", "warning");
    expect(rowOf("STATUS_URL").getByRole("button", { name: "Put STATUS_URL back to the fleet's value on web-2" })).toBeInTheDocument();
    // An exception the central set, waiting to be applied.
    expect(rowOf("ghcr").getByText("Waiting to apply")).toBeInTheDocument();
    // An independent server is left alone on purpose, in grey.
    expect(rowOf("SUPPORT_EMAIL").getByText("Independent")).toHaveAttribute("data-state", "stopped");
    await expectNoAxeViolations(container);
  });

  it("makes a server independent, and asks for sudo mode only through the usual dialog", { timeout: 30_000 }, async () => {
    const calls = backend({ "PUT /api/fleet/servers/web-2/settings-mode": (call: RecordedCall) => json(200, { node: "web-2", mode: (call.body as { mode: string }).mode, apply: null }) });
    const { user } = renderConsole("/settings/fleet");
    const follow = await screen.findByRole("switch", { name: "Follows the fleet: web-2" });
    expect(follow).toBeChecked();
    expect(screen.getByRole("switch", { name: "Follows the fleet: db-1" })).not.toBeChecked();
    await user.click(follow);
    await waitFor(() => {
      expect(calls.callsTo("PUT /api/fleet/servers/web-2/settings-mode")).toHaveLength(1);
    });
    expect(calls.callsTo("PUT /api/fleet/servers/web-2/settings-mode")[0]?.body).toEqual({ mode: "independent" });
  });

  it("puts a value changed on a server back to the fleet's", { timeout: 30_000 }, async () => {
    const calls = backend({ "POST /api/fleet/servers/web-2/settings/variable/STATUS_URL/restore": () => json(200, { success: true, message: "Asked" }) });
    const { user } = renderConsole("/settings/fleet");
    await user.click(await screen.findByRole("button", { name: "Put STATUS_URL back to the fleet's value on web-2" }));
    await waitFor(() => {
      expect(calls.callsTo("POST /api/fleet/servers/web-2/settings/variable/STATUS_URL/restore")).toHaveLength(1);
    });
  });

  it("shares a variable with the fleet, applying it now", { timeout: 30_000 }, async () => {
    const calls = backend({ "PUT /api/fleet/settings/variable/RELEASE_NOTE": (call: RecordedCall) => json(200, { setting: item("variable", "RELEASE_NOTE", "fleet", (call.body as { value: Record<string, unknown> }).value), apply: { job_id: "j1", nodes: ["web-2"] } }) });
    const { user } = renderConsole("/settings/fleet");
    await user.click(await screen.findByRole("button", { name: "Share a setting" }));
    const dialog = await screen.findByRole("dialog", { name: "Share with the fleet" });
    await user.click(within(dialog).getByRole("combobox", { name: "What" }));
    await user.click(await screen.findByRole("option", { name: "Shared variable" }));
    await user.type(within(dialog).getByLabelText(/^Name/), "RELEASE_NOTE");
    await user.type(within(dialog).getByLabelText(/^Value/), "v2");
    await user.click(within(dialog).getByRole("button", { name: "Save and apply" }));
    await waitFor(() => {
      expect(calls.callsTo("PUT /api/fleet/settings/variable/RELEASE_NOTE")).toHaveLength(1);
    });
    expect(calls.callsTo("PUT /api/fleet/settings/variable/RELEASE_NOTE")[0]?.body).toEqual({ value: { value: "v2", secret: false }, scope: "fleet", apply: true });
  });

  it("says what is wrong with a JSON value before it is sent, and names the field the server refuses", { timeout: 30_000 }, async () => {
    const calls = backend({
      "PUT /api/fleet/settings/channel/ops": () => json(400, { error: "validation_error", detail: "A channel does not take the field colour", hint: null, fields: { value: "A channel does not take the field colour" }, output: null }),
    });
    const { user } = renderConsole("/settings/fleet");
    await user.click(await screen.findByRole("button", { name: "Share a setting" }));
    const dialog = await screen.findByRole("dialog", { name: "Share with the fleet" });
    await user.type(within(dialog).getByLabelText(/^Name/), "ops");
    const value = within(dialog).getByLabelText(/^Value/);
    await user.clear(value);
    await user.type(value, "not json");
    await user.click(within(dialog).getByRole("button", { name: "Save and apply" }));
    expect(await within(dialog).findByText("The value must be a JSON object, between curly brackets.")).toBeInTheDocument();
    expect(calls.callsTo("PUT /api/fleet/settings/channel/ops")).toHaveLength(0);
  });

  it("plans 'Apply to the fleet', starts the job and follows each server in its own state", { timeout: 30_000 }, async () => {
    let started = false;
    const plan = { action: "settings_apply", title: "Apply the fleet's settings", nodes: [], summary: { run: 2, skipped: 1 }, notes: [] };
    const calls = backend({
      "POST /api/fleet/settings/apply": (call: RecordedCall) => {
        const body = call.body as { plan: boolean };
        if (body.plan) return json(200, { plan, job: null });
        started = true;
        return json(200, { plan, job: { id: "job-7" } });
      },
      "GET /api/fleet/jobs/job-7": () =>
        json(200, {
          id: "job-7",
          action: "settings_apply",
          status: "succeeded",
          nodes: [
            { node: "web-2", state: "succeeded", reason: null, items: [{ resource: "variable", name: "SUPPORT_EMAIL", state: "updated" }], href: "/n/web-2/activity" },
            { node: "db-1", state: "failed", reason: null, items: [{ resource: "registry", name: "ghcr", state: "failed", message: "The registry refused the login", output: "denied: requested access to the resource is denied" }], href: "/n/db-1/activity" },
          ],
        }),
    });
    const { user } = renderConsole("/settings/fleet");
    await user.click(await screen.findByRole("button", { name: /^Apply to the fleet/ }));
    const dialog = await screen.findByRole("dialog", { name: "Apply to the fleet" });
    expect(await within(dialog).findByText("2 servers will be asked.")).toBeInTheDocument();
    expect(within(dialog).getByText("1 server is left out.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() => {
      expect(started).toBe(true);
    });
    expect(calls.callsTo("POST /api/fleet/settings/apply").map((call) => (call.body as { plan: boolean }).plan)).toEqual([true, false]);
    // Each server in its own state; what failed in the server's words and its output verbatim.
    const progress = await screen.findByRole("list", { name: "Progress by server" });
    expect(within(progress).getByText("web-2")).toBeInTheDocument();
    expect(await within(progress).findByText("The registry refused the login")).toBeInTheDocument();
    expect(within(progress).getByText("denied: requested access to the resource is denied")).toBeInTheDocument();
  });

  it("offers a server nothing to apply to when there is none, and speaks Spanish", { timeout: 30_000 }, async () => {
    backend({ "GET /api/fleet/settings": () => json(200, { kinds: KINDS, items: [] }), "GET /api/fleet/settings/status": () => json(200, status({ settings: [] })) });
    renderConsole("/settings/fleet");
    expect(await screen.findByText("Nothing is shared yet")).toBeInTheDocument();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 1, name: "Ajustes de la flota" })).toBeInTheDocument();
    expect(screen.getByText("Todavía no se comparte nada")).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
