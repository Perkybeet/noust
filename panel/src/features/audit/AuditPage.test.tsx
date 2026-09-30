import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../test/fakes";
import { familyOf, groupFindings } from "./CompliancePage";

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
    const table = await screen.findByRole("region", { name: "Audit events" });
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
    expect(screen.getByRole("link", { name: "Export" })).toHaveAttribute("href", "/api/audit/export");
    await expectNoAxeViolations(container);
  });

  it("opens an event with every field, and follows its request", { timeout: 20_000 }, async () => {
    const backend = auditBackend();
    const { user, location } = renderConsole("/settings/audit");
    const table = await screen.findByRole("region", { name: "Audit events" });
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
    const table = await screen.findByRole("region", { name: "Eventos de auditoría" });
    expect(await within(table).findByText("Rol de una cuenta cambiado")).toBeInTheDocument();
    expect(within(table).getByText("Inicio de sesión")).toBeInTheDocument();
  });

  it("records a review as a secondary action: the log is read here, not changed", async () => {
    auditBackend();
    renderConsole("/settings/audit");
    expect(await screen.findByRole("button", { name: "Record a review" })).toHaveAttribute("data-variant", "secondary");
  });
});

const FINDINGS = [
  { id: "ENS-ACC-02", title: "Second factor on every account", status: "fail", measures: ["op.acc.6.r2", "op.acc.6.r8"], summary: "1 of 3 accounts has no second factor.", evidence: ["dani: none"], remediation: "Reset dani's second factor or disable the account." },
  { id: "ENS-EXP-08", title: "Audit chain", status: "ok", measures: ["op.exp.8"], summary: "The chain verifies.", evidence: [], remediation: "" },
  { id: "ENS-ACC-01", title: "Roles", status: "warning", measures: ["op.acc.3", "org.1.3"], summary: "One person holds two roles.", evidence: [], remediation: "Add an exception." },
  { id: "ENS-MON-01", title: "Shipping", status: "n/a", measures: ["op.mon.3.1"], summary: "No receivers.", evidence: [], remediation: "" },
];

describe("Settings > Compliance", () => {
  it("groups the checks by measure, worst first, with the evidence verbatim and the fix", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/ens/incident": () => json(200, { locked: false }),
      "GET /api/ens/check": () =>
        json(200, { profile: "ens-medium", checked_at: "2026-09-30T09:00:00+00:00", host: "web-01", version: "3.1.0", verdict: "fail", counts: { fail: 1, warning: 1, ok: 1, "n/a": 1 }, findings: FINDINGS, indicators: { accounts: 3 }, errors: {} }),
    });
    const { container } = renderConsole("/settings/compliance");
    expect(await screen.findByText("1 check is not met")).toBeInTheDocument();
    expect(screen.getByText(/Profile in force: ENS category MEDIUM\./)).toBeInTheDocument();
    const failing = screen.getByText("Second factor on every account").closest("li") as HTMLElement;
    expect(within(failing).getByText("Not met")).toBeInTheDocument();
    expect(within(failing).getByText("Reset dani's second factor or disable the account.")).toBeInTheDocument();
    // The evidence, verbatim, one press away.
    await userEvent.click(within(failing).getByRole("button", { name: "Show the evidence (1 line)" }));
    expect(within(failing).getByText("dani: none")).toBeInTheDocument();
    // What is met waits folded under its family.
    expect(screen.queryByText("Audit chain")).toBeNull();
    const [firstMet] = screen.getAllByRole("button", { name: "Show the 1 met or not applicable" });
    if (firstMet === undefined) throw new Error("no folded checks");
    await userEvent.click(firstMet);
    expect(screen.getByText("Audit chain")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download report" })).toHaveAttribute("href", "/api/ens/report?format=markdown");
    await expectNoAxeViolations(container);
  });

  it("says the console is locked down for an incident, first", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/ens/incident": () => json(200, { locked: true, since: "2026-09-30T08:00:00+00:00", by: "root", reason: "INC-42", package: "/var/lib/noust/incidents/INC-42.tar.gz" }),
      "GET /api/ens/check": () => json(200, { profile: "standard", checked_at: "2026-09-30T09:00:00+00:00", host: "web-01", version: "3.1.0", verdict: "ok", counts: { ok: 1 }, findings: [FINDINGS[1]], indicators: {}, errors: {} }),
    });
    renderConsole("/settings/compliance");
    expect(await screen.findByText("The console is locked down for an incident")).toBeInTheDocument();
    expect(screen.getByText("INC-42")).toBeInTheDocument();
  });

  it("groups by the family of a check's first measure", () => {
    expect(familyOf("op.acc.6.r8")).toBe("op.acc");
    expect(familyOf("org.1.3")).toBe("org");
    expect(groupFindings(FINDINGS).map((group) => [group.family, group.worst, group.findings.map((finding) => finding.id)])).toEqual([
      ["op.acc", "fail", ["ENS-ACC-02", "ENS-ACC-01"]],
      ["op.exp", "ok", ["ENS-EXP-08"]],
      ["op.mon", "n/a", ["ENS-MON-01"]],
    ]);
  });
});
