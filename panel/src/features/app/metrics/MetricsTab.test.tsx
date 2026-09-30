import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AppMetricsStatus } from "../../../api/queries/metrics";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { metricsReadFixture } from "../../overview/testFixtures";
import { TAB_DOMAIN, appRoutes } from "../testRoutes";
import { fixCommand } from "./MetricsTab";

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
      bbox: { left: 0, top: 0, width: 400, height: 180 },
      valToPos: () => 0,
    };
  }),
}));

const NOW = Math.floor(Date.now() / 1000);
const CPU = `app.${TAB_DOMAIN}.cpu.percent`;
const MEMORY = `app.${TAB_DOMAIN}.mem.bytes`;
const RECORDING = { recording: true, host: "daemon", since: NOW - 86_400, last_sample_at: NOW, interval_s: 5, last_error: null, reason: null, advice: null, retention_days: 400 };

function localIso(seconds: number): string {
  const date = new Date(seconds * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function status(patch: Partial<AppMetricsStatus> = {}): AppMetricsStatus {
  return {
    domain: TAB_DOMAIN,
    kind: "unit",
    sampled: true,
    source: "cgroup",
    reason: null,
    units: [],
    series: { cpu: CPU, memory: MEMORY },
    traffic: { available: false, log: null, reason: null },
    last_sample_at: NOW,
    collector: RECORDING,
    ...patch,
  };
}

async function metricsAt({
  app = { memory_max_mb: 512 },
  appStatus = status(),
  read = (metrics: string[]) => metricsReadFixture(metrics, { now: NOW, cells: 1440 }),
  path = `/apps/${TAB_DOMAIN}/metrics`,
}: {
  app?: Record<string, unknown>;
  appStatus?: AppMetricsStatus;
  read?: (metrics: string[]) => unknown;
  path?: string;
} = {}) {
  const routes: Record<string, RouteHandler> = {
    [`GET /api/apps/${TAB_DOMAIN}/metrics`]: () => json(200, appStatus),
    "GET /api/metrics/query": (call) => json(200, read(call.search.getAll("metric"))),
    "GET /api/deployments": () =>
      json(200, {
        items: [
          { id: 25, domain: TAB_DOMAIN, status: "success", triggered_by: "webhook", git_commit: "2a8b7c4", git_branch: "main", started_at: localIso(NOW - 1_800), finished_at: null, duration_s: 47, error: null, has_log: true },
          { id: 9, domain: TAB_DOMAIN, status: "failed", triggered_by: "cli", git_commit: "d08e4f7", git_branch: "main", started_at: localIso(NOW - 20 * 86_400), finished_at: null, duration_s: 30, error: "x", has_log: true },
        ],
        total: 2,
        next_before_id: null,
      }),
  };
  const backend = fakeBackend(appRoutes(app, routes));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

describe("the metrics tab", () => {
  it("draws CPU and memory over the day, read together, with the limit and the deploys", async () => {
    const { backend } = await metricsAt();
    expect(await screen.findByRole("img", { name: /^CPU, last 24 hours, 1-minute averages\. CPU: latest 12%/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Memory, last 24 hours/ })).toBeInTheDocument();
    const read = backend.callsTo("GET /api/metrics/query")[0];
    expect(read?.search.getAll("metric")).toEqual([CPU, MEMORY]);
    expect(read?.search.get("window")).toBe("24h");
    // The deploy in the range is listed and linked; the one three weeks ago is not.
    expect(screen.getByRole("link", { name: /^Deploy 25, succeeded/ })).toHaveAttribute("href", `/apps/${TAB_DOMAIN}/deployments/25`);
    expect(screen.queryByRole("link", { name: /^Deploy 9,/ })).not.toBeInTheDocument();
  });

  it("keeps its range in the URL and reads that window", async () => {
    const { user, location, backend } = await metricsAt();
    await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
    await user.click(screen.getByRole("radio", { name: "7d" }));
    await waitFor(() => {
      expect(location().search).toEqual({ range: "7d" });
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/metrics/query").some((call) => call.search.get("window") === "7d")).toBe(true);
    });
  });

  it("says why a static site has no CPU or memory, and draws its requests and server errors", async () => {
    const REQUESTS = `app.${TAB_DOMAIN}.http.requests_per_min`;
    const ERRORS = `app.${TAB_DOMAIN}.http.errors_5xx_per_min`;
    const { backend } = await metricsAt({
      appStatus: status({
        kind: "static",
        sampled: false,
        source: "none",
        reason: { code: "static", message: "A static site has no process.", fix: null, evidence: null, params: {} },
        series: { requests: REQUESTS, errors_5xx: ERRORS },
        traffic: { available: true, log: "/var/log/nginx/x.access.log", reason: null },
      }),
    });
    expect(await screen.findByRole("heading", { name: "Traffic" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /^Requests, last 24 hours/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Server errors \(5xx\), last 24 hours/ })).toBeInTheDocument();
    expect(backend.callsTo("GET /api/metrics/query")[0]?.search.getAll("metric")).toEqual([REQUESTS, ERRORS]);
  });

  it("says why there is no data at all for an application that cannot be measured, and how to fix it", async () => {
    await metricsAt({
      appStatus: status({
        kind: "compose",
        sampled: false,
        source: "none",
        reason: { code: "compose_docker_unavailable", message: "The docker daemon could not be asked.", fix: "systemctl status docker", evidence: "Cannot connect to the Docker daemon", params: {} },
        series: {},
      }),
    });
    expect(await screen.findByRole("heading", { name: "Docker could not be asked" })).toBeInTheDocument();
    expect(screen.getByText("systemctl status docker")).toBeInTheDocument();
    expect(screen.getByText("Cannot connect to the Docker daemon")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("replaces empty charts with the reason when the window has no reading, and the fix", async () => {
    await metricsAt({
      appStatus: status({
        sampled: false,
        reason: { code: "stopped", message: "x.service is stopped.", fix: `noust service start ${TAB_DOMAIN}`, evidence: "ActiveState=inactive", params: { unit: "x" } },
      }),
      read: (metrics) => metricsReadFixture(metrics, { now: NOW, cells: 60, recordedFrom: 60 }),
    });
    expect(await screen.findByRole("heading", { name: "Nothing is running to measure" })).toBeInTheDocument();
    expect(screen.getByText(`noust service start ${TAB_DOMAIN}`)).toBeInTheDocument();
    expect(screen.getByText("ActiveState=inactive")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /^CPU/ })).not.toBeInTheDocument();
  });

  it("keeps the earlier readings on screen when new ones stop, and says why above them", async () => {
    await metricsAt({
      appStatus: status({
        sampled: false,
        reason: { code: "accounting_off", message: "systemd is not counting memory.", fix: "Turn accounting on.", evidence: null, params: { unit: "shop-example-com" } },
      }),
    });
    expect(await screen.findByText("systemd is not counting this application's memory")).toBeInTheDocument();
    expect(screen.getByText("systemctl edit shop-example-com")).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /^CPU/ })).toBeInTheDocument();
  });

  it("says when nothing records history at all", async () => {
    const stopped = {
      ...RECORDING,
      recording: false,
      reason: { code: "collector_stopped", message: "No collector is recording metrics.", fix: "noust monitor enable", evidence: null, params: {} },
    };
    await metricsAt({
      appStatus: status({ collector: stopped }),
      read: (metrics) => ({ ...(metricsReadFixture(metrics, { now: NOW, cells: 60, recordedFrom: 60 }) as object), collector: stopped }),
    });
    expect(await screen.findByRole("heading", { name: "Metrics history is not being recorded" })).toBeInTheDocument();
    expect(screen.getByText("noust monitor enable")).toBeInTheDocument();
  });

  it("finds the command in the backend's fix, wherever it is", () => {
    expect(fixCommand({ code: "unit_missing", fix: "Redeploy it: noust app update shop.example.com", params: {} })).toBe("noust app update shop.example.com");
    expect(fixCommand({ code: "failed", fix: "journalctl -u shop -n 50", params: {} })).toBe("journalctl -u shop -n 50");
    expect(fixCommand({ code: "php_fpm_missing", fix: "Install PHP-FPM, then redeploy the application.", params: {} })).toBeNull();
    expect(fixCommand({ code: "compose_cgroup_unreadable", fix: "Use 'docker stats' for their usage.", params: {} })).toBe("docker stats");
  });

  it("has no accessibility violations, with charts and with a reason", async () => {
    await metricsAt();
    await screen.findByRole("img", { name: /^CPU/ });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("reads in Spanish", async () => {
    await act(() => setLocale("es"));
    await metricsAt({
      appStatus: status({
        sampled: false,
        reason: { code: "stopped", message: "stopped", fix: "noust service start x", evidence: null, params: {} },
      }),
      read: (metrics) => metricsReadFixture(metrics, { now: NOW, cells: 60, recordedFrom: 60 }),
    });
    expect(await screen.findByRole("heading", { name: "No hay nada en marcha que medir" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("Iníciala:")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
