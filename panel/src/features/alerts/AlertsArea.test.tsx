import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";
import { metricsReadFixture } from "../overview/testFixtures";

// uPlot draws on a canvas jsdom does not have; the charts' contract is their summary.
vi.mock("uplot", () => ({
  default: vi.fn(function () {
    return {
      destroy: vi.fn(),
      setData: vi.fn(),
      setScale: vi.fn(),
      setSize: vi.fn(),
      setCursor: vi.fn(),
      redraw: vi.fn(),
      bbox: { left: 0, top: 0, width: 400, height: 140 },
      valToPos: () => 0,
    };
  }),
}));

const NOW = Math.floor(Date.now() / 1000);

/** A screen as wide as a desktop's: the tabs are a strip and the charts two abreast. */
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

function routes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/alerts/rules": () => json(200, { items: [], metrics: [], firing: 0 }),
    "GET /api/alerts/watching": () => json(200, { watcher: { state: "running", fix: null }, notifications: { enabled: true, paused_until: null, channels: [] }, watches: [] }),
    "GET /api/probes": () => json(200, { items: [], down: 0, status_page: { enabled: false, directory: "/var/lib/noust/status", components: 0, title: "Status", domain: null } }),
    "GET /api/config": () => json(200, { config: { monitor: { disk_threshold: 90.0, disk_check_seconds: 0, disk_renotify_hours: 0, cert_warning_days: 14, backup_missing_days: 2, backup_check_hours: 6, backup_missing_renotify_hours: 24, os_patches_check_hours: 24, token_warning_days: 7 } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/metrics/query": (call) => json(200, metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 1440, recordedFrom: 1430 })),
    ...extra,
  };
}

describe("the Alerts area", () => {
  it("is five sections, each titled by its own name with what it is for under it, and never a heading that repeats it", { timeout: 40_000 }, async () => {
    fakeBackend(routes());
    const { user } = renderConsole("/alerts");
    expect(await screen.findByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument();
    expect(screen.getByText("Tell me when a measurement stays past a limit. Acknowledging stops the reminders, never the alert.")).toBeInTheDocument();
    // Alerts has no h2 of its own: the h1 is the name and the list follows it.
    expect(screen.queryByRole("heading", { level: 2, name: "Alert rules" })).not.toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "Alerts sections" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Alerts", "Checks", "Probes", "Status page", "Metrics"]);

    await user.click(within(nav).getByRole("link", { name: "Probes" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Probes" })).toBeInTheDocument();
    await user.click(within(nav).getByRole("link", { name: "Status page" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Status page" })).toBeInTheDocument();
    expect(screen.getByText("The probes you mark public, as one page any web server can serve. It shows names and bars, never addresses.")).toBeInTheDocument();
    await user.click(within(nav).getByRole("link", { name: "Checks" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Checks" })).toBeInTheDocument();
  });
});

describe("Alerts > Metrics", () => {
  it("draws what the server measures over the window in the address, and keeps the window there", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(routes());
    const { user, location } = renderConsole("/alerts/metrics");
    expect(await screen.findByRole("heading", { level: 1, name: "Metrics" })).toBeInTheDocument();
    expect(screen.getByText("What the server measures, over the window you choose.")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /^CPU, last 24 hours/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Memory, last 24 hours/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Disk, last 24 hours/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Network, last 24 hours/ })).toBeInTheDocument();
    // The four people ask for: swap and load stay on the Overview.
    expect(screen.queryByRole("img", { name: /^Swap, / })).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /^Load, / })).not.toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: "7d" }));
    await waitFor(() => {
      expect(location().search).toEqual({ window: "7d" });
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/metrics/query").some((call) => call.search.get("window") === "7d")).toBe(true);
    });
    // A lone server has no other to put beside it.
    expect(screen.queryByRole("heading", { name: "Compare servers" })).not.toBeInTheDocument();
  });

  it("puts the servers side by side below the charts on a central", { timeout: 40_000 }, async () => {
    fakeBackend({ ...fleetRoutes(centralSession()), ...routes({ "GET /api/nodes": () => json(200, { items: [{ name: "web-2", status: "reachable", version: "4.1.0" }] }) }) , "GET /api/fleet/metrics": () => json(200, { resource: "metrics", generated_at: new Date().toISOString(), partial: false, nodes: [], items: [] }) });
    renderConsole("/alerts/metrics");
    expect(await screen.findByRole("heading", { level: 2, name: "Compare servers" })).toBeInTheDocument();
  });

  it("reads in Spanish, with no accessibility violations", { timeout: 40_000 }, async () => {
    await act(() => setLocale("es"));
    fakeBackend(routes());
    renderConsole("/alerts/metrics");
    expect(await screen.findByRole("heading", { level: 1, name: "Métricas" })).toBeInTheDocument();
    expect(screen.getByText("Lo que mide el servidor, en la ventana que elijas.")).toBeInTheDocument();
    await screen.findByRole("img", { name: /^CPU, / });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Alerts > Checks", () => {
  it("is the monitor's own limits as one form, apart from the rules, with its skeleton while they load", { timeout: 40_000 }, async () => {
    fakeBackend(routes());
    renderConsole("/alerts/checks");
    expect(await screen.findByRole("heading", { level: 1, name: "Checks" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Almost full at (%)")).toHaveValue("90");
    // No second heading says "Built-in checks" under the h1: the sections of the form are its rows.
    expect(screen.queryByRole("heading", { name: "Built-in checks" })).not.toBeInTheDocument();
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
  });
});
