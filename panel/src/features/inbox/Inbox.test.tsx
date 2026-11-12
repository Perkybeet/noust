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

/**
 * The alert that fires, as the server lists it among what needs attention: it is the server that
 * says so (`managers/overview.py`), so that a fleet reading each server's own list counts it too.
 */
function alertItem(taken: boolean, by = "alice") {
  const params = { rule: "disk-high", subject: "", since: SINCE, ...(taken ? { by, at: SINCE + 60 } : {}) };
  return {
    id: "alert:disk-high:",
    subject: { kind: "alert", rule: "disk-high", subject: "" },
    title: "disk-high",
    origin: "alert",
    severity: "warn",
    reasons: [
      {
        kind: "alert",
        severity: "warn",
        code: taken ? "alert_taken" : "alert_firing",
        params,
        detail: null,
        when: new Date(SINCE * 1000).toISOString(),
        deployment_id: null,
        actions: [taken ? "reopen_alert" : "acknowledge_alert"],
      },
    ],
  };
}

/** A probe that is down, as the server lists it too: which probes need somebody is the server's to say. */
const PROBE_DOWN = {
  id: "probe:blog",
  subject: { kind: "probe", name: "blog" },
  title: "blog",
  origin: "alert",
  severity: "fail",
  reasons: [
    {
      kind: "probe",
      severity: "fail",
      code: "probe_down",
      params: { probe: "blog", since: SINCE },
      detail: "HTTP 503 Service Unavailable",
      when: new Date(SINCE * 1000).toISOString(),
      deployment_id: null,
      actions: ["open_probes"],
    },
  ],
};

function overviewWithAlert(taken: boolean, by = "alice") {
  return overviewFixture({ attention_total: 4, attention: [PROBE_DOWN, FAILED_APP, alertItem(taken, by), EXPIRING] });
}

function inboxRoutes(over: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  const overview: RouteHandler = over["GET /api/overview"] ?? (() => json(200, overviewWithAlert(false)));
  return {
    ...signedInRoutes(),
    "GET /api/overview": overview,
    // The one list is the server's (`GET /api/attention`, joined and ranked by `managers/attention.py`): here it is the Overview's rows.
    "GET /api/attention": async (call) => {
      const body = (await (await overview(call)).json()) as { attention: unknown[]; attention_total: number };
      return json(200, { items: body.attention, total: body.attention_total });
    },
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
    const backend = fakeBackend(inboxRoutes());
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
    expect(within(at(rows, 0)).getByRole("link", { name: "Open probes, blog" })).toHaveAttribute("href", "/alerts/probes");
    expect(within(at(rows, 1)).getByRole("link", { name: "Diagnose worker.example.dev" })).toHaveAttribute("href", "/apps/worker.example.dev/diagnose");
    // The verb is drawn with what it acts on, decoratively: the name of the button stays the words.
    const diagnose = within(at(rows, 1)).getByRole("link", { name: "Diagnose worker.example.dev" }).querySelector("svg");
    expect(diagnose).toHaveAttribute("aria-hidden", "true");
    expect(within(at(rows, 3)).getByRole("link", { name: "Certificate of example.com" })).toHaveAttribute("href", "/domains?q=example.com");
    expect(within(panel).getByRole("link", { name: "Open activity" })).toHaveAttribute("href", "/activity");
    await expectNoAxeViolations(panel);
    // The probe that is down came with the server's own list, as the alert did: the console asks nothing else for it.
    expect(backend.callsTo("GET /api/probes")).toHaveLength(0);
    await user.keyboard("{Escape}");
  });

  it("takes an alert in the row, which then offers to reopen it and stops being a new thing to do", async () => {
    let taken = false;
    const backend = fakeBackend(
      inboxRoutes({
        "GET /api/overview": () => json(200, overviewWithAlert(taken)),
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

  it("lists an alert once, from the server's list alone, and does not read the rules to add it again", async () => {
    // A console that also read the rules would name the alert twice: two reasons on one row.
    const backend = fakeBackend(inboxRoutes());
    const { user } = await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const rows = await within(panel).findAllByRole("listitem");
    expect(rows.filter((row) => row.textContent.includes("disk-high"))).toHaveLength(1);
    expect(within(panel).getAllByText("This alert is firing.", { exact: false })).toHaveLength(1);
    expect(backend.callsTo("GET /api/alerts/rules")).toHaveLength(0);
    await user.keyboard("{Escape}");
  });

  it("names the person who took an alert through a central, first and in the console's language", async () => {
    fakeBackend(inboxRoutes({ "GET /api/overview": () => json(200, overviewWithAlert(true, "fleet-fra-1 on behalf of marta")) }));
    const { user } = await openBell(/^Inbox, \d+ things? need you$/);
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    expect(await within(panel).findByText("This alert is firing. marta (from the central fra-1) took it.")).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("on behalf of");
    await user.keyboard("{Escape}");
  });

  it("says nothing needs the operator when nothing does, and offers the history", async () => {
    fakeBackend(inboxRoutes({ "GET /api/overview": () => json(200, overviewFixture()) }));
    await openBell("Inbox, nothing needs you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    expect(await within(panel).findByText("Nothing needs you")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Open activity" })).toBeInTheDocument();
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

  it("asks the server for one list and joins nothing: what the account may not read is the server's to leave out", async () => {
    const viewer = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read"] };
    const backend = fakeBackend(inboxRoutes({ "GET /api/auth/session": () => json(200, viewer) }));
    renderConsole("/apps");
    await screen.findByRole("button", { name: /^Inbox/ });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/attention").length).toBeGreaterThan(0);
    });
    // The requests, the newer Noust and the recipes are joined into that list by the server: nobody asks for them one by one.
    expect(backend.callsTo("GET /api/overview")).toHaveLength(0);
    expect(backend.callsTo("GET /api/approvals")).toHaveLength(0);
    expect(backend.callsTo("GET /api/recipes/installed")).toHaveLength(0);
    expect(backend.callsTo("GET /api/alerts/rules")).toHaveLength(0);
    expect(backend.callsTo("GET /api/probes")).toHaveLength(0);
    expect(backend.callsTo("GET /api/system/version")).toHaveLength(0);
  });

  it("says where each row comes from, and opens the screen that deals with it from a press anywhere on the row", async () => {
    fakeBackend(inboxRoutes());
    const { user, location } = await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const rows = await within(panel).findAllByRole("listitem");
    // A probe is an alert's, an application's service is a state.
    expect(rows.map((row) => row.getAttribute("data-origin"))).toEqual(["alert", "state", "alert", "state"]);
    expect(within(at(rows, 0)).getByText("Alert", { selector: "[data-slot='origin']" })).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("State", { selector: "[data-slot='origin']" })).toBeInTheDocument();
    // Where the button leads to that same screen it is the row's one link (two links to one place are one too many for a keyboard),
    // and a press anywhere else on the row opens the screen too.
    expect(within(at(rows, 1)).getAllByRole("link")).toHaveLength(1);
    await user.click(within(at(rows, 1)).getByText("worker.example.dev"));
    await waitFor(() => {
      expect(location().pathname).toBe("/apps/worker.example.dev/diagnose");
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Inbox" })).not.toBeInTheDocument();
    });
  });

  it("makes the name the link, over the whole row, where the button does something else than open the screen", async () => {
    fakeBackend(inboxRoutes());
    const { user } = await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const rows = await within(panel).findAllByRole("listitem");
    // An alert's button takes it and goes nowhere: the name is how the row leads to the alerts, stretched over the row, with the button above it.
    const name = within(at(rows, 2)).getByRole("link", { name: "disk-high" });
    expect(name).toHaveAttribute("href", "/alerts");
    expect(name.className).toContain("after:absolute");
    expect(within(at(rows, 2)).getByRole("button", { name: "Acknowledge disk-high" }).closest("div")?.className).toContain("z-10");
    await user.keyboard("{Escape}");
  });

  it("is one list with one count: the machine's facts and the findings of Security are rows of it, with their own words", async () => {
    const MACHINE_DISK = {
      id: "machine:disk",
      subject: { kind: "machine", fact: "disk" },
      title: "Disk",
      severity: "fail",
      origin: "state",
      reasons: [{ kind: "machine", origin: "state", severity: "fail", code: "disk_full", params: { mount: "/var", percent: 96, free: 3_200_000_000 }, detail: null, when: null, deployment_id: null, actions: ["free_space"] }],
    };
    const FINDING = {
      id: "security:fw.docker_bypass",
      subject: { kind: "finding", check: "fw.docker_bypass", group: "firewall" },
      title: "Docker publishes ports around the firewall",
      severity: "fail",
      origin: "security",
      reasons: [{ kind: "security", origin: "security", severity: "fail", code: "security_finding", params: { check: "fw.docker_bypass", group: "firewall", severity: "critical" }, detail: "Port 5432 is published on 0.0.0.0 by Docker.", when: null, deployment_id: null, actions: ["review_finding"] }],
    };
    fakeBackend(inboxRoutes({ "GET /api/overview": () => json(200, overviewFixture({ attention_total: 2, attention: [MACHINE_DISK, FINDING] })) }));
    const { user } = await openBell("Inbox, 2 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const rows = await within(panel).findAllByRole("listitem");
    expect(within(at(rows, 0)).getByText("Disk")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByText("/var is 96% full, 3.0 GB left")).toBeInTheDocument();
    expect(within(at(rows, 0)).getByRole("link", { name: "Free space on Disk" })).toHaveAttribute("href", "/server/files");
    expect(within(at(rows, 1)).getByText("Security", { selector: "[data-slot='origin']" })).toBeInTheDocument();
    expect(within(at(rows, 1)).getByText("A critical finding of the security checks")).toBeInTheDocument();
    // What the check found stays in Noust's own words, under it.
    expect(within(at(rows, 1)).getByText("Port 5432 is published on 0.0.0.0 by Docker.")).toBeInTheDocument();
    expect(within(at(rows, 1)).getByRole("link", { name: "Review the finding: Docker publishes ports around the firewall" })).toHaveAttribute("href", "/network/firewall");
    await user.keyboard("{Escape}");
  });

  it("reads the Overview of a server that has no such list, which is a server before 4.3", async () => {
    const backend = fakeBackend(inboxRoutes({ "GET /api/attention": () => new Response(JSON.stringify({ error: "not_found", detail: "Not Found" }), { status: 404, headers: { "Content-Type": "application/json" } }) }));
    await openBell("Inbox, 4 things need you");
    expect(backend.callsTo("GET /api/overview").length).toBeGreaterThan(0);
  });

  it("does not cut the list short in the phone's sheet, which has the room and scrolls: every line is drawn", async () => {
    // Item 144: the popover caps its list at a height, and the sheet of a phone carried the same cap, so three of five lines were
    // drawn and the other two sat under a scroll nobody saw.
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
    fakeBackend(inboxRoutes());
    await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const list = await within(panel).findByRole("list", { name: "4 items need you" });

    expect(list.closest("div")?.className).not.toContain("max-h-96");
    expect(within(list).getAllByRole("listitem")).toHaveLength(4);
  });

  it("keeps the popover's list to a height, which is a place to scroll on a desktop", async () => {
    fakeBackend(inboxRoutes());
    await openBell("Inbox, 4 things need you");
    const panel = await screen.findByRole("dialog", { name: "Inbox" });
    const list = await within(panel).findByRole("list", { name: "4 items need you" });

    expect(list.closest("div")?.className).toContain("max-h-96");
  });

  it("is in Spanish too, and the bell says it in words", async () => {
    await act(() => setLocale("es"));
    fakeBackend(inboxRoutes());
    await openBell("Bandeja, 4 cosas requieren tu atención");
    const panel = await screen.findByRole("dialog", { name: "Bandeja" });
    expect(await within(panel).findByRole("radio", { name: "Requiere tu atención 4" })).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "Abrir actividad" })).toBeInTheDocument();
  });
});
