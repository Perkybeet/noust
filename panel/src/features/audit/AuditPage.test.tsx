import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../test/fakes";
import { findLoadedRegion } from "../../test/regions";

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

const EVENTS = [
  {
    timestamp: "2026-09-30T09:12:00+00:00",
    action: "user.role_change",
    result: "ok",
    actor: "bea",
    client_ip: "203.0.113.7",
    resource: "account:ana",
    detail: "operator to admin, approved by gus",
    id: "e3",
    seq: 3,
    category: "change",
    correlation_id: "req-9f2",
    who: { kind: "account", name: "bea", role: "security", via: "console" },
    details: { from: "operator", to: "admin", approval: 41 },
    sensitive: false,
  },
  {
    timestamp: "2026-09-30T09:10:00+00:00",
    action: "auth.login",
    result: "failure",
    actor: "anonymous",
    client_ip: "198.51.100.9",
    resource: "account:ana",
    detail: "account sign-in refused: bad_password",
    id: "e2",
    seq: 2,
    category: "access",
    correlation_id: "req-1aa",
    sensitive: false,
  },
];

function auditBackend(extra: Record<string, RouteHandler> = {}) {
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/audit": (call: RecordedCall) =>
      json(200, { items: call.search.get("correlation_id") === "req-9f2" ? [EVENTS[0]] : EVENTS, next_before: null }),
    "GET /api/audit/verify": () => json(200, { ok: true, checked: 1_204, legacy: 0, first_seq: 1, last_seq: 1_204, notes: [], limitation: "root can rewrite it" }),
    "GET /api/audit/status": () => json(200, { status: "ok", problems: [], total_bytes: 1, failing: false, failures: 0, sinks: [{ sink_id: "journald", degraded: false }] }),
    "GET /api/audit/events": () => json(200, { categories: ["access", "change", "read"], events: [] }),
    "GET /api/audit/reviews": () => json(200, { items: [] }),
    ...extra,
  });
}

describe("Settings > Audit log", () => {
  it("lists the events with actor, role, target and outcome, says the chain is intact, and passes axe", { timeout: 20_000 }, async () => {
    auditBackend();
    const { container } = renderConsole("/settings/audit");
    const table = await findLoadedRegion("Audit events");
    // The event in words, not its id; what the server recorded about it is in the drawer.
    expect(await within(table).findByText("Account role changed")).toBeInTheDocument();
    expect(within(table).queryByText("user.role_change")).not.toBeInTheDocument();
    expect(within(table).queryByText("operator to admin, approved by gus")).not.toBeInTheDocument();
    const row = within(within(table).getByText("Account role changed").closest("tr") as HTMLElement);
    expect(row.getByText("Security officer")).toBeInTheDocument();
    expect(row.getByText("account:ana")).toBeInTheDocument();
    expect(row.getByText("Done")).toBeInTheDocument();
    expect(within(within(table).getByText("Sign-in").closest("tr") as HTMLElement).getByText("Failed")).toBeInTheDocument();
    // Fixed columns: a long value truncates in its cell instead of pushing the others away.
    expect(within(table).getByRole("table")).toHaveClass("table-fixed");
    expect(await screen.findByText("The audit log is intact")).toBeInTheDocument();
    expect(screen.getByText(/1,204 events verified from the first to the last/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  describe("export", () => {
    function downloads(): string[] {
      const clicks: string[] = [];
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
        clicks.push(this.getAttribute("href") ?? "");
      });
      return clicks;
    }

    it("offers the CSV and the whole log with its chain, and sends the browser to the one chosen", { timeout: 20_000 }, async () => {
      const clicks = downloads();
      auditBackend();
      const { user } = renderConsole("/settings/audit");
      await findLoadedRegion("Audit events");

      await user.click(screen.getByRole("button", { name: "Export" }));
      expect(await screen.findByRole("menuitem", { name: /CSV, for a spreadsheet/ })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /NDJSON, with the chain/ })).toBeInTheDocument();
      await user.click(screen.getByRole("menuitem", { name: /CSV, for a spreadsheet/ }));

      await waitFor(() => {
        expect(clicks).toEqual(["/api/audit/export?format=csv"]);
      });
    });

    it("keeps the filter that is on in the CSV and says so, and the NDJSON stays whole", { timeout: 20_000 }, async () => {
      const clicks = downloads();
      auditBackend();
      const { user } = renderConsole("/settings/audit?category=access&result=failure&q=ana");
      await findLoadedRegion("Audit events");

      await user.click(screen.getByRole("button", { name: "Export" }));
      expect(await screen.findByText("The events these filters and this search keep, with what each one recorded.")).toBeInTheDocument();
      await user.click(screen.getByRole("menuitem", { name: /CSV, for a spreadsheet/ }));
      await user.click(screen.getByRole("button", { name: "Export" }));
      await user.click(await screen.findByRole("menuitem", { name: /NDJSON, with the chain/ }));

      await waitFor(() => {
        expect(clicks).toEqual(["/api/audit/export?format=csv&category=access&result=failure&q=ana", "/api/audit/export?format=ndjson"]);
      });
    });
  });

  it("names the master token as Activity does, and says how it holds the console in words (item 152)", { timeout: 20_000 }, async () => {
    const event = {
      timestamp: "2026-09-30T09:20:00+00:00",
      action: "app.delete",
      result: "ok",
      actor: "master",
      client_ip: "203.0.113.9",
      resource: "app:shop.example.com",
      detail: "deleted",
      id: "e5",
      seq: 5,
      category: "change",
      correlation_id: "req-777",
      who: { kind: "master", id: "master", name: "master", role: "break_glass", via: "session" },
      sensitive: false,
    };
    auditBackend({ "GET /api/audit": () => json(200, { items: [event], next_before: null }) });
    const { user } = renderConsole("/settings/audit");
    const table = await findLoadedRegion("Audit events");
    const row = within(within(table).getByText(/deleted|Application deleted|app\.delete/i).closest("tr") as HTMLElement);
    // The same credential Activity calls "The master token", and its grant said as what it is.
    expect(row.getByText("The master token")).toBeInTheDocument();
    expect(row.getByText("Break-glass access")).toBeInTheDocument();
    expect(row.queryByText("master")).not.toBeInTheDocument();
    expect(row.queryByText("break_glass")).not.toBeInTheDocument();

    // The drawer keeps the exact value the server recorded, for whoever has to quote it.
    await user.click(row.getByText("The master token"));
    const drawer = await screen.findByRole("dialog");
    expect(within(drawer).getByText("The master token")).toBeInTheDocument();
    expect(within(drawer).getByText("Break-glass access")).toBeInTheDocument();
    expect(within(drawer).getByText("master")).toBeInTheDocument();
    expect(within(drawer).queryByText("break_glass")).not.toBeInTheDocument();
  });

  it("opens an event with every field, and follows its request", { timeout: 20_000 }, async () => {
    const backend = auditBackend();
    const { user, location } = renderConsole("/settings/audit");
    const table = await findLoadedRegion("Audit events");
    await user.click(await within(table).findByText("Account role changed"));
    const drawer = await screen.findByRole("dialog", { name: "Account role changed" });
    expect(within(drawer).getByText("user.role_change")).toBeInTheDocument();
    expect(within(drawer).getByText("operator to admin, approved by gus")).toBeInTheDocument();
    expect(within(drawer).getByText("req-9f2")).toBeInTheDocument();
    expect(within(drawer).getByText(/"approval": 41/)).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Events of the same request" }));
    await waitFor(() => {
      expect(location().search).toEqual({ correlation: "req-9f2" });
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/audit").at(-1)?.search.get("correlation_id")).toBe("req-9f2");
    });
    expect(await screen.findByText("One request")).toBeInTheDocument();
  });

  it("says where the chain breaks, verbatim, when it does", async () => {
    auditBackend({
      "GET /api/audit/verify": () =>
        json(200, { ok: false, checked: 40, legacy: 0, broken: { file: "/var/log/noust/audit.log", line: 41, seq: 41, reason: "mac mismatch" }, notes: [], limitation: "" }),
    });
    renderConsole("/settings/audit");
    expect(await screen.findByText("The audit log was changed")).toBeInTheDocument();
    expect(screen.getByText("/var/log/noust/audit.log:41 seq 41: mac mismatch")).toBeInTheDocument();
  });

  it("records a review of a period", { timeout: 20_000 }, async () => {
    const backend = auditBackend({
      "POST /api/audit/reviews": () => json(201, { timestamp: "2026-09-30T10:00:00+00:00", reviewer: "eva", period_start: "2026-09-23", period_end: "2026-09-30", notes: "ok" }),
    });
    const { user } = renderConsole("/settings/audit");
    await user.click(await screen.findByRole("button", { name: "Record a review" }));
    const dialog = await screen.findByRole("dialog", { name: "Record a review" });
    await user.type(within(dialog).getByLabelText(/^Notes/), "Nothing unexpected");
    await user.click(within(dialog).getByRole("button", { name: "Record review" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/audit/reviews")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/audit/reviews")[0]?.body).toMatchObject({ notes: "Nothing unexpected" });
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    auditBackend();
    renderConsole("/settings/audit");
    expect(await screen.findByText("El registro de auditoría está íntegro")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Eventos" })).toBeInTheDocument();
    const table = await findLoadedRegion("Eventos de auditoría");
    expect(await within(table).findByText("Rol de una cuenta cambiado")).toBeInTheDocument();
    expect(within(table).getByText("Inicio de sesión")).toBeInTheDocument();
  });

  it("records a review as a secondary action: the log is read here, not changed", async () => {
    auditBackend();
    renderConsole("/settings/audit");
    expect(await screen.findByRole("button", { name: "Record a review" })).toHaveAttribute("data-variant", "secondary");
  });
});
