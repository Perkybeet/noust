import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { FakeEventSource, SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { clearNotices } from "../../realtime/notices";
import { overviewFixture } from "../overview/testFixtures";

const SINCE = Math.floor(Date.now() / 1000) - 840;

const FAILED_APP = {
  id: "app:worker.example.dev",
  subject: { kind: "app", domain: "worker.example.dev" },
  title: "worker.example.dev",
  severity: "fail",
  reasons: [{ kind: "state", severity: "fail", code: "service_failed", params: {}, detail: "npm ERR! code ELIFECYCLE", when: null, deployment_id: null, actions: ["diagnose"] }],
};

const EXPIRING = {
  id: "certificate:example.com",
  subject: { kind: "certificate", domain: "example.com" },
  title: "example.com",
  severity: "warn",
  reasons: [{ kind: "certificate", severity: "warn", code: "certificate_expires_in", params: { days: 12 }, detail: null, when: null, deployment_id: null, actions: ["renew_certificate"] }],
};

function alertRules(taken: boolean) {
  return {
    items: [
      {
        name: "disk-high",
        enabled: true,
        severity: "warning",
        metric: "disk",
        states: [{ subject: "", state: "firing", since: SINCE, last_value: 91, acknowledged_by: taken ? "alice" : null, acknowledged_at: taken ? SINCE + 60 : null }],
      },
    ],
    metrics: [],
    firing: 1,
  };
}

function inboxRoutes(over: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/overview": () => json(200, overviewFixture({ attention_total: 2, attention: [FAILED_APP, EXPIRING] })),
    "GET /api/alerts/rules": () => json(200, alertRules(false)),
    "GET /api/probes": () =>
      json(200, {
        items: [{ name: "blog", status: { state: "down", since: SINCE, last_error: "HTTP 503 Service Unavailable", failures: 3 } }],
        down: 1,
        status_page: {},
      }),
    "GET /api/approvals": () => json(200, { approvals: [], pending: 0 }),
    ...over,
  };
}

/** The row at a place in a list: a missing one is a test that found fewer rows than it expects. */
function at(rows: readonly HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (row === undefined) throw new Error(`there is no row ${String(index)} among ${String(rows.length)}`);
  return row;
}

async function openBell(name: RegExp | string) {
  const harness = renderConsole("/apps");
  const bell = await screen.findByRole("button", { name });
  await harness.user.click(bell);
  return harness;
}

beforeEach(() => {
  clearNotices();
  // A desktop's width: the inbox is a popover there, a drawer on a phone.
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

afterEach(() => {
  clearNotices();
});

describe("the inbox", () => {
  it("counts what needs the operator in the bell, worst first, from the server's list and from what the console adds", async () => {
    fakeBackend(inboxRoutes());
    const { user } = await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const rows = await within(panel).findAllByRole("listitem");
    // What failed first (by name), then the warnings: the probe, the application, the alert, the certificate.
    expect(rows.map((row) => row.getAttribute("data-severity"))).toEqual(["fail", "fail", "warn", "warn"]);
    expect(within(at(rows, 0)).getByText("blog")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("worker.example.dev")).toBeInTheDocument();
    // The system's own words, verbatim.
    expect(within(at(rows, 0)).getByText("HTTP 503 Service Unavailable")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("npm ERR! code ELIFECYCLE")).toBeInTheDocument();
    // A row is two clicks from anywhere: the bell, then the item. The button is the first thing to do there.
    expect(within(at(rows, 0)).getByRole("link", { name: "Open probes, blog" })).toHaveAttribute("href", "/settings/probes");
    expect(within(at(rows, 1)).getByRole("link", { name: "Diagnose worker.example.dev" })).toHaveAttribute("href", "/apps/worker.example.dev/diagnose");
    expect(within(at(rows, 3)).getByRole("link", { name: "Certificate of example.com" })).toHaveAttribute("href", "/domains?q=example.com");
    expect(within(panel).getByRole("link", { name: "Open Activity" })).toHaveAttribute("href", "/activity");
    await expectNoAxeViolations(panel);
    await user.keyboard("{Escape}");
  });

  it("takes an alert in the row, which then offers to reopen it and stops being a new thing to do", async () => {
    let taken = false;
    const backend = fakeBackend(
      inboxRoutes({
        "GET /api/alerts/rules": () => json(200, alertRules(taken)),
        "POST /api/alerts/rules/disk-high/acknowledge": () => {
          taken = true;
          return json(200, { ok: true });
        },
        "DELETE /api/alerts/rules/disk-high/acknowledge": () => {
          taken = false;
          return json(200, { ok: true });
        },
      }),
    );
    const { user } = await openBell(/^Inbox, \d+ things? need you$/);
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    await user.click(await within(panel).findByRole("button", { name: "Acknowledge disk-high" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/alerts/rules/disk-high/acknowledge")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/alerts/rules/disk-high/acknowledge")[0]?.search.get("subject")).toBe("");
    // Nothing is said in a toast: the row itself changed, which is the answer.
    expect(await within(panel).findByRole("button", { name: "Reopen disk-high" })).toBeInTheDocument();
    expect(within(panel).getByText("This alert is firing. alice took it.")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Reopen disk-high" }));
    expect(await within(panel).findByRole("button", { name: "Acknowledge disk-high" })).toBeInTheDocument();
  });

  it("says nothing needs the operator when nothing does, and offers the history", async () => {
    fakeBackend(inboxRoutes({ "GET /api/overview": () => json(200, overviewFixture()), "GET /api/alerts/rules": () => json(200, { items: [], metrics: [], firing: 0 }), "GET /api/probes": () => json(200, { items: [], down: 0, status_page: {} }) }));
    await openBell("Inbox, nothing needs you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    expect(await within(panel).findByText("Nothing needs you.")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open Activity" })).toBeInTheDocument();
  });

  it("keeps what the server said while the tab was open, under Recent, with its own words", async () => {
    fakeBackend(inboxRoutes());
    const harness = renderConsole("/apps");
    await screen.findByRole("button", { name: /^Inbox/ });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("notice", { text: "Backup of shop.example.com failed: disk full", state: "failed" });
      FakeEventSource.latest().emit("notice", { text: "Deployed docs.example.org", state: "active" });
    });
    await harness.user.click(screen.getByRole("button", { name: /^Inbox/ }));
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    await harness.user.click(await within(panel).findByRole("radio", { name: "Recent" }));
    const list = await within(panel).findByRole("list", { name: "Recent" });
    const rows = within(list).getAllByRole("listitem");
    // Newest first.
    expect(rows[0]).toHaveTextContent("Deployed docs.example.org");
    expect(rows[1]).toHaveTextContent("Backup of shop.example.com failed: disk full");
    expect(within(panel).getByText("Since you opened the console")).toBeInTheDocument();
  });

  it("does not ask for what the account may not read, nor count it", async () => {
    const viewer = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read"] };
    const backend = fakeBackend(inboxRoutes({ "GET /api/auth/session": () => json(200, viewer) }));
    renderConsole("/apps");
    await screen.findByRole("button", { name: /^Inbox/ });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/approvals").length).toBeGreaterThan(0);
    });
    expect(backend.callsTo("GET /api/overview")).toHaveLength(0);
    expect(backend.callsTo("GET /api/alerts/rules")).toHaveLength(0);
    expect(backend.callsTo("GET /api/probes")).toHaveLength(0);
    expect(backend.callsTo("GET /api/system/version")).toHaveLength(0);
  });

  it("is in Spanish too, and the bell says it in words", async () => {
    await act(() => setLocale("es"));
    fakeBackend(inboxRoutes());
    await openBell("Bandeja, 4 cosas requieren tu atención");
    const panel = await screen.findByRole("dialog", { name: "Bandeja" });
    expect(await within(panel).findByRole("radio", { name: "Requiere tu atención 4" })).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Abrir Actividad" })).toBeInTheDocument();
  });
});
