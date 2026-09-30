import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { MACHINE_METRICS } from "./MachineCharts";
import { metricsReadFixture, overviewFixture } from "./testFixtures";

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

const ATTENTION = [
  {
    id: "app:admin.example.com",
    subject: { kind: "app", domain: "admin.example.com" },
    title: "admin.example.com",
    severity: "fail",
    reasons: [
      { kind: "state", severity: "fail", code: "service_failed", params: {}, detail: null, when: null, deployment_id: null, actions: ["view_log", "diagnose"] },
      {
        kind: "deploy",
        severity: "fail",
        code: "deploy_failed",
        params: {},
        detail: "npm ERR! code ELIFECYCLE",
        when: "2026-09-29T19:20:35+02:00",
        deployment_id: 12,
        actions: ["view_deployment", "diagnose"],
      },
    ],
  },
  {
    id: "unit:queue-worker",
    subject: { kind: "unit", name: "queue-worker" },
    title: "queue-worker",
    severity: "fail",
    reasons: [{ kind: "unit", severity: "fail", code: "unit_failed", params: {}, detail: null, when: null, deployment_id: null, actions: ["open_service"] }],
  },
  {
    id: "app:shop.example.com",
    subject: { kind: "app", domain: "shop.example.com" },
    title: "shop.example.com",
    severity: "warn",
    reasons: [
      {
        kind: "certificate",
        severity: "warn",
        code: "certificate_expires_in",
        params: { days: 12, valid_until: "2026-10-11" },
        detail: null,
        when: null,
        deployment_id: null,
        actions: ["renew_certificate"],
      },
    ],
  },
];

const ACTIVITY = [
  { id: "job:1", type: "deploy", title: "Deploy shop.example.com", status: "completed", domain: "shop.example.com", actor: "master", at: new Date(Date.now() - 60_000).toISOString() },
  { id: "job:2", type: "update", title: "Update admin.example.com", status: "failed", domain: "admin.example.com", actor: "master", at: new Date(Date.now() - 120_000).toISOString() },
];

function routes(extra: Record<string, RouteHandler> = {}, overview = overviewFixture({ attention: ATTENTION, attention_total: 3, activity: ACTIVITY })): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/overview": () => json(200, overview),
    // A young history: the day asked for, with readings only in its last ten minutes.
    "GET /api/metrics/query": (call) => json(200, metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 1440, recordedFrom: 1430 })),
    "GET /api/config": () => json(200, { config: { notifications: { enabled: false } }, path: "/etc/noust/config.yaml" }),
    ...extra,
  };
}

async function overview(extra?: Record<string, RouteHandler>, path = "/", data?: ReturnType<typeof overviewFixture>) {
  const backend = fakeBackend(routes(extra, data));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Overview" });
  return { ...harness, backend };
}

describe("the overview", () => {
  it("opens with six key figures, each a link to where it comes from", async () => {
    await overview();
    const figures = await screen.findByRole("region", { name: "Key figures" });
    const apps = await within(figures).findByRole("link", { name: "Applications: 3 running, 0 failed, 1 stopped, 1 static" });
    expect(apps).toHaveAttribute("href", "/apps");
    expect(within(figures).getByRole("link", { name: /^Deploys today: 2/ })).toHaveAttribute("href", "/activity");
    expect(within(figures).getByRole("link", { name: /^Certificates: 4/ })).toHaveAttribute("href", "/domains");
    expect(within(figures).getByRole("link", { name: /^Backups in the last 24 hours/ })).toHaveAttribute("href", "/backups");
    expect(within(figures).getByRole("link", { name: /^Disk: 60% free/ })).toBeInTheDocument();
    expect(within(figures).getByRole("link", { name: "Operating system updates: Up to date" })).toBeInTheDocument();
  });

  it("colours a figure only when it is a problem, with its glyph and its word", async () => {
    await overview(
      undefined,
      "/",
      overviewFixture({ disk: { used: 95e9, total: 100e9, percent: 95, free_percent: 5, forecast_full_days: 4, forecast_reason: null, error: null } }),
    );
    const figures = await screen.findByRole("region", { name: "Key figures" });
    const disk = await within(figures).findByRole("link", { name: /^Disk/ });
    expect(within(disk).getByText("Critical")).toHaveClass("text-fail");
    expect(within(disk).getByText(/Full in about 4 days/)).toBeInTheDocument();
  });

  it("says a figure it could not read, in the tool's own words", async () => {
    await overview(undefined, "/", overviewFixture({ certificates: { total: 0, expiring: 0, expired: 0, next_days: null, warning_days: 21, error: "certbot: command not found" } }));
    expect(await screen.findByText("certbot: command not found")).toBeInTheDocument();
  });

  it("lists what needs attention from the server's answer, verbatim, each with where it is fixed", async () => {
    await overview();
    const attention = screen.getByRole("region", { name: "Needs attention" });
    const app = await within(attention).findByRole("link", { name: "admin.example.com" });
    expect(app).toHaveAttribute("href", "/apps/admin.example.com");
    expect(within(attention).getAllByText("The service has failed")).toHaveLength(2);
    expect(within(attention).getByText("Last deploy failed")).toBeInTheDocument();
    expect(within(attention).getByText("npm ERR! code ELIFECYCLE")).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: "View log of the deploy of admin.example.com" })).toHaveAttribute("href", "/apps/admin.example.com/deployments/12");
    expect(within(attention).getByRole("link", { name: "Diagnose admin.example.com" })).toHaveAttribute("href", "/apps/admin.example.com/diagnose");
    expect(within(attention).getByRole("link", { name: "Open the service queue-worker" })).toHaveAttribute("href", "/services/queue-worker");
    expect(within(attention).getByText("Certificate expires in 12 days")).toBeInTheDocument();
  });

  it("adds an application nothing answers on, which only the console knows", async () => {
    await overview({
      "GET /api/apps": () => json(200, { total: 1, apps: [{ domain: "api.example.com", name: "api", status: "no_answer", active: true, enabled: true, layout: "inplace" }] }),
    });
    const attention = screen.getByRole("region", { name: "Needs attention" });
    expect(await within(attention).findByText("The service runs, but nothing answers on its port")).toBeInTheDocument();
  });

  it("says in one line that nothing needs attention on a calm server", async () => {
    await overview(undefined, "/", overviewFixture());
    expect(await screen.findByText("Nothing needs attention.")).toBeInTheDocument();
  });

  it("shows five items until asked for all", async () => {
    const many = Array.from({ length: 7 }, (_, i) => ({ ...ATTENTION[1], id: `unit:w${String(i)}`, subject: { kind: "unit", name: `w${String(i)}` }, title: `w${String(i)}` }));
    const { user } = await overview(undefined, "/", overviewFixture({ attention: many as typeof ATTENTION, attention_total: 7 }));
    const attention = screen.getByRole("region", { name: "Needs attention" });
    await within(attention).findByRole("link", { name: "w0" });
    expect(within(attention).queryByRole("link", { name: "w6" })).not.toBeInTheDocument();
    await user.click(within(attention).getByRole("button", { name: "Show all 7" }));
    expect(within(attention).getByRole("link", { name: "w6" })).toBeInTheDocument();
  });

  it("tells what happened lately as a timeline, with the state when it did not simply succeed", async () => {
    await overview();
    const activity = await screen.findByRole("group", { name: "Recent activity, newest first" });
    expect(within(activity).getByText("Today")).toBeInTheDocument();
    expect(within(activity).getByText("Deploy")).toBeInTheDocument();
    expect(within(activity).getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All activity" })).toHaveAttribute("href", "/activity");
  });

  it("reads the machine's history in one request and says what it shows", async () => {
    const { backend } = await overview();
    const machine = await screen.findByRole("region", { name: "Machine" });
    expect(await within(machine).findByRole("img", { name: /^CPU, last 24 hours, 1-minute averages\. CPU: latest 12%/ })).toBeInTheDocument();
    const calls = backend.callsTo("GET /api/metrics/query");
    expect(calls.length).toBeGreaterThanOrEqual(1);
    expect(calls[0]?.search.getAll("metric")).toEqual([...MACHINE_METRICS]);
    expect(calls[0]?.search.get("window")).toBe("24h");
    expect(within(machine).getByText(/^Showing .+, 1-minute averages\. History since /)).toBeInTheDocument();
  });

  it("folds a chart with no reading in its whole window into one line on a phone", async () => {
    await overview({
      "GET /api/metrics/query": (call) => json(200, metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 1440, recordedFrom: 1440 })),
    });
    const machine = await screen.findByRole("region", { name: "Machine" });
    const cpu = await within(machine).findByRole("group", { name: "CPU" });
    expect(cpu).toHaveTextContent("No readings in this window");
    expect(within(machine).queryByRole("img", { name: /^CPU, / })).not.toBeInTheDocument();
  });

  it("keeps the chosen range in the URL, and reads that window", async () => {
    const { user, location, backend } = await overview();
    await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
    await user.click(screen.getByRole("radio", { name: "1h" }));
    await waitFor(() => {
      expect(location().search).toEqual({ window: "1h" });
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/metrics/query").some((call) => call.search.get("window") === "1h")).toBe(true);
    });
  });

  it("changes the range where the operator is: the page does not jump to the top", async () => {
    const { user, location, history } = await overview();
    await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    try {
      const entries = history.length;
      await user.click(screen.getByRole("radio", { name: "7d" }));
      await waitFor(() => {
        expect(location().search).toEqual({ window: "7d" });
      });
      await screen.findByRole("img", { name: /^CPU, last 7 days/ });
      expect(scrollTo).not.toHaveBeenCalled();
      // A view of the same page, not a new page: Back leaves the overview instead of stepping ranges.
      expect(history.length).toBe(entries);
    } finally {
      scrollTo.mockRestore();
    }
  });

  it("says when history is not being recorded, why, and the command that fixes it", async () => {
    await overview({
      "GET /api/metrics/query": (call) =>
        json(200, {
          ...metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 60, recordedFrom: 60 }),
          collector: {
            recording: false,
            host: null,
            since: null,
            last_sample_at: null,
            interval_s: 5,
            last_error: null,
            reason: { code: "monitor_disabled", message: "The monitor service is disabled.", fix: "noust monitor enable", evidence: null, params: {} },
            advice: null,
            retention_days: 400,
          },
        }),
    });
    expect(await screen.findByText("Metrics history is not being recorded")).toBeInTheDocument();
    expect(screen.getByText(/The monitor service that records them is installed but turned off\./)).toBeInTheDocument();
    expect(screen.getAllByText("noust monitor enable").length).toBeGreaterThan(0);
  });

  it("welcomes an empty server with its first steps instead of empty charts", async () => {
    await overview(undefined, "/", overviewFixture({ apps: { running: 0, failed: 0, stopped: 0, static: 0, error: null }, backups: { ...overviewFixture().backups, apps: 0, scheduled: 0, with_backup_24h: 0 } }));
    const steps = await screen.findByRole("list", { name: "First steps" });
    // Beside the step from a tablet up, under it on a phone: one of the two is shown.
    expect(within(steps).getAllByRole("link", { name: "New application" })[0]).toHaveAttribute("href", "/apps/new");
    expect(screen.queryByRole("region", { name: "Machine" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Key figures" })).not.toBeInTheDocument();
  });

  it("summarises the fleet on a central, each server from its own overview, and says why one is missing", async () => {
    await overview({
      "GET /api/nodes": () => json(200, { items: [{ name: "db-1" }] }),
      "GET /api/fleet/summary": () =>
        json(200, {
          resource: "summary",
          generated_at: "2026-09-29T21:40:00Z",
          partial: true,
          nodes: [
            { name: "web-01", local: true, status: "ok", missing: [], warnings: [] },
            { name: "db-1", local: false, status: "unreachable", code: "node_unreachable", error_verbatim: "ssh: connect to host db-1 port 22: Connection timed out", missing: [], warnings: [] },
          ],
          items: [
            { node: "web-01", local: true, name: "web-01", reachability: "reachable", version: "3.1.0", apps: { running: 3, failed: 0 }, overview: overviewFixture() },
            { node: "db-1", local: false, name: "db-1", reachability: "unreachable", version: null, apps: null, overview: null },
          ],
        }),
    });
    expect(await screen.findByText("This server")).toBeInTheDocument();
    expect(screen.getByText("ssh: connect to host db-1 port 22: Connection timed out")).toBeInTheDocument();
    expect(screen.getByText("2 servers · 1 reachable · 1 cannot be reached")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open fleet" })).toHaveAttribute("href", "/fleet");
  });

  it("offers the quick actions behind More actions", async () => {
    const { user } = await overview();
    await user.click(screen.getByRole("button", { name: "More actions" }));
    expect(await screen.findByRole("menuitem", { name: "Renew certificates" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Check for updates" })).toBeInTheDocument();
  });

  it("opens with the range the URL names", async () => {
    await overview(undefined, "/?window=30d");
    expect(await screen.findByRole("radio", { name: "30d" })).toHaveAttribute("aria-checked", "true");
  });

  it("has no accessibility violations", async () => {
    await overview();
    await within(screen.getByRole("region", { name: "Needs attention" })).findByRole("link", { name: "admin.example.com" });
    await screen.findByRole("img", { name: /^CPU/ });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("reads in Spanish", async () => {
    await act(() => setLocale("es"));
    fakeBackend(routes());
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "Resumen" });
    const attention = screen.getByRole("region", { name: "Requiere atención" });
    await within(attention).findByRole("link", { name: "admin.example.com" });
    expect(within(attention).getByText("El último despliegue falló")).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: "Diagnosticar admin.example.com" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Cifras clave" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Máquina" })).toBeInTheDocument();
    await screen.findByRole("img", { name: /^CPU, últimas 24 horas, medias de 1 min/ });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
