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
import { CONTAINER_ROLES, busiest, containerMetrics } from "./ContainerCharts";
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

/** A day of traffic: 2400 requests, a few of them failed, and what stood out. */
function trafficFixture(patch: Record<string, unknown> = {}) {
  const point = (ts: number, requests: number, s5xx: number) => ({ ts, requests, s2xx: requests - s5xx - 2, s3xx: 1, s4xx: 1, s5xx, bytes: requests * 10_000 });
  const points = Array.from({ length: 24 }, (_, i) => point(NOW - (24 - i) * 3600, 100, i === 20 ? 24 : 0));
  return {
    domain: TAB_DOMAIN,
    window: "24h",
    start: NOW - 24 * 3600,
    end: NOW,
    step: 3600,
    available: true,
    log: "/var/log/nginx/x.access.log",
    reason: null,
    totals: { ts: NOW - 24 * 3600, requests: 2400, s2xx: 2160, s3xx: 120, s4xx: 96, s5xx: 24, bytes: 24_000_000 },
    points,
    top: {
      paths: [
        { key: "GET /orders/:n", count: 900 },
        { key: "GET /", count: 700 },
      ],
      networks: [{ key: "203.0.113.0/24", count: 1200 }],
      statuses: [
        { key: "200", count: 2160 },
        { key: "503", count: 24 },
      ],
      referrers: [],
    },
    collector: RECORDING,
    ...patch,
  };
}

async function metricsAt({
  app = { memory_max_mb: 512 },
  appStatus = status(),
  read = (metrics: string[]) => metricsReadFixture(metrics, { now: NOW, cells: 1440 }),
  path = `/apps/${TAB_DOMAIN}/metrics`,
  traffic = (): Response => json(200, trafficFixture()),
}: {
  app?: Record<string, unknown>;
  appStatus?: AppMetricsStatus;
  read?: (metrics: string[]) => unknown;
  path?: string;
  traffic?: RouteHandler;
} = {}) {
  const routes: Record<string, RouteHandler> = {
    [`GET /api/apps/${TAB_DOMAIN}/metrics`]: () => json(200, appStatus),
    [`GET /api/apps/${TAB_DOMAIN}/traffic`]: traffic,
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

  it("charts a service's disk and network traffic, each with its two directions, once its unit reports them", async () => {
    const IO = {
      disk_read: `app.${TAB_DOMAIN}.io.read_bytes_s`,
      disk_write: `app.${TAB_DOMAIN}.io.write_bytes_s`,
      net_rx: `app.${TAB_DOMAIN}.net.rx_bytes_s`,
      net_tx: `app.${TAB_DOMAIN}.net.tx_bytes_s`,
    };
    const { backend } = await metricsAt({ appStatus: status({ series: { cpu: CPU, memory: MEMORY, ...IO } }) });
    expect(await screen.findByRole("heading", { name: "Disk and network" })).toBeInTheDocument();
    expect(await screen.findByRole("img", { name: /^Disk, last 24 hours, 1-minute averages\. Read: latest 12 B\/s/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /^Network, last 24 hours/ })).toBeInTheDocument();
    // Every series of the tab is read in the one request, so the charts share a cursor and a window.
    expect(backend.callsTo("GET /api/metrics/query")[0]?.search.getAll("metric")).toEqual([CPU, MEMORY, IO.disk_read, IO.disk_write, IO.net_rx, IO.net_tx]);
    // Counted since the unit was written with accounting: nothing to apologise for.
    expect(screen.queryByText("Disk and network are not counted yet")).not.toBeInTheDocument();
  });

  it("says a service has to restart to count its disk and network when only those charts are empty", async () => {
    const IO = `app.${TAB_DOMAIN}.io.read_bytes_s`;
    await metricsAt({
      appStatus: status({ series: { cpu: CPU, memory: MEMORY, disk_read: IO } }),
      read: (metrics) => {
        const answer = metricsReadFixture(metrics, { now: NOW, cells: 1440 });
        // An older unit has no accounting of its own: its io series has no readings at all.
        return { ...answer, series: answer.series.map((series) => (series.metric === IO ? { ...series, points: series.points.map(([at]): [number, null, null] => [at, null, null]) } : series)) };
      },
    });
    expect(await screen.findByText("Disk and network are not counted yet")).toBeInTheDocument();
    expect(screen.getByText(/counts its own disk and network traffic from its next restart/)).toBeInTheDocument();
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

  describe("a Compose stack", () => {
    const series = (name: string) => ({
      cpu: `app.${TAB_DOMAIN}.ctr.${name}.cpu.percent`,
      memory: `app.${TAB_DOMAIN}.ctr.${name}.mem.bytes`,
      disk_read: `app.${TAB_DOMAIN}.ctr.${name}.io.read_bytes_s`,
    });
    const containers = ["web", "worker", "db", "cache"].map((name) => ({ name, series: series(name) }));
    /** web is the busiest, then worker, db and cache last. */
    const BUSY: Record<string, number> = { web: 50, worker: 30, db: 10, cache: 1 };
    const stack = (patch: Partial<AppMetricsStatus> = {}) => status({ kind: "compose", containers, ...patch });
    const readContainers = (metrics: string[]) => {
      const answer = metricsReadFixture(metrics, { now: NOW, cells: 1440 });
      return {
        ...answer,
        series: answer.series.map((one) => {
          const name = /\.ctr\.([^.]+)\./.exec(one.metric)?.[1];
          const value = name === undefined ? 12 : (BUSY[name] ?? 0);
          return { ...one, points: one.points.map(([at]): [number, number, number] => [at, value, value]) };
        }),
      };
    };

    it("charts each container on its own, the busiest three of each role, read with the rest in one request", async () => {
      const { backend } = await metricsAt({ appStatus: stack(), read: readContainers });
      expect(await screen.findByRole("heading", { name: "By container" })).toBeInTheDocument();
      const cpu = await screen.findByRole("img", { name: /^CPU by container, last 24 hours/ });
      // The three busiest, each by its container's name; the idlest is left out and the page says so.
      expect(cpu.getAttribute("aria-label")).toMatch(/web: latest 50%/);
      expect(cpu.getAttribute("aria-label")).toMatch(/worker: latest 30%/);
      expect(cpu.getAttribute("aria-label")).toMatch(/db: latest 10%/);
      expect(cpu.getAttribute("aria-label")).not.toMatch(/cache/);
      expect(screen.getByText("Showing the 3 busiest of 4 containers.")).toBeInTheDocument();
      expect(screen.getByRole("img", { name: /^Memory by container, last 24 hours/ })).toBeInTheDocument();
      expect(screen.getByRole("img", { name: /^Disk read by container, last 24 hours/ })).toBeInTheDocument();
      // A role no container has is not charted.
      expect(screen.queryByRole("img", { name: /^Received by container/ })).toBeNull();
      // One request for the page: the stack's own charts and its containers' share a window and a cursor.
      const reads = backend.callsTo("GET /api/metrics/query");
      expect(reads[0]?.search.getAll("metric")).toEqual([CPU, MEMORY, ...containerMetrics(containers)]);
    });

    it("does not chart a stack of one container twice", async () => {
      await metricsAt({ appStatus: stack({ containers: containers.slice(0, 1) }), read: readContainers });
      await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
      expect(screen.queryByRole("heading", { name: "By container" })).toBeNull();
    });

    it("ranks the containers by what they read over the range, ties by name", () => {
      const prepared = {
        grid: { timestamps: [1, 2, 3] },
        byMetric: new Map([
          [containers[0]?.series.cpu ?? "", { values: [10, null, 30], peaks: [], ceiling: null }],
          [containers[1]?.series.cpu ?? "", { values: [20, 20, 20], peaks: [], ceiling: null }],
          [containers[2]?.series.cpu ?? "", { values: [null, null, null], peaks: [], ceiling: null }],
        ]),
      } as unknown as Parameters<typeof busiest>[0];
      expect(busiest(prepared, containers, "cpu").map((entry) => [entry.name, entry.mean])).toEqual([
        ["web", 20],
        ["worker", 20],
      ]);
      expect(CONTAINER_ROLES).toEqual(["cpu", "memory", "disk_read", "disk_write", "net_rx", "net_tx"]);
      expect(containerMetrics(containers)).toHaveLength(12);
    });
  });

  describe("the traffic of the site", () => {
    it("says who asks for what: the totals, the requests over time and what stands out", async () => {
      const { backend } = await metricsAt({ appStatus: status({ traffic: { available: true, log: "/var/log/nginx/x.access.log", reason: null } }) });
      expect(await screen.findByRole("heading", { name: "Who asks for what" })).toBeInTheDocument();
      expect(await screen.findByText("2,400")).toBeInTheDocument();
      expect(screen.getByText("2,280 answered well")).toBeInTheDocument();
      expect(screen.getByText("1.0% of the requests")).toBeInTheDocument();
      expect(screen.getByText("23 MB")).toBeInTheDocument();
      expect(screen.getByRole("img", { name: /^Requests over time, 24 hours, 1-hour totals/ })).toBeInTheDocument();
      const pages = screen.getByRole("list", { name: "Most asked-for pages" });
      expect(within(pages).getByText("GET /orders/:n")).toBeInTheDocument();
      expect(within(pages).getByText("900")).toBeInTheDocument();
      expect(within(screen.getByRole("list", { name: "Networks" })).getByText("203.0.113.0/24")).toBeInTheDocument();
      expect(within(screen.getByRole("list", { name: "Status codes" })).getByText("503")).toBeInTheDocument();
      // An empty ranking says so rather than leaving a hole.
      expect(within(screen.getByRole("group", { name: "Where they came from" })).getByText("Nothing yet.")).toBeInTheDocument();
      expect(backend.callsTo("GET /api/apps/shop.example.com/traffic")[0]?.search.get("window")).toBe("24h");
    });

    it("shows how many different visitors there were and how long the slowest requests took, when the log says", async () => {
      await metricsAt({
        appStatus: status({ traffic: { available: true, log: "/x", reason: null } }),
        traffic: () => {
          const base = trafficFixture();
          return json(200, { ...base, totals: { ...base.totals, visitors: 312, latency_p50_ms: 42, latency_p95_ms: 1800 } });
        },
      });
      const visitors = (await screen.findByText("Visitors")).closest("div");
      expect(visitors).not.toBeNull();
      expect(within(visitors as HTMLElement).getByText("312")).toBeInTheDocument();
      expect(screen.getByText("Different addresses with their browsers")).toBeInTheDocument();
      expect(screen.getByText("Response time (p95)")).toBeInTheDocument();
      expect(screen.getByText(/Half took under/)).toBeInTheDocument();
    });

    it("shows neither tile for a site whose log carries no time and no visitor", async () => {
      await metricsAt({ appStatus: status({ traffic: { available: true, log: "/x", reason: null } }) });
      await screen.findByRole("heading", { name: "Who asks for what" });
      expect(screen.queryByText("Visitors")).toBeNull();
      expect(screen.queryByText("Response time (p95)")).toBeNull();
    });

    it("reads another window when asked, and keeps the last answer meanwhile", async () => {
      const { user, backend } = await metricsAt({ appStatus: status({ traffic: { available: true, log: "/x", reason: null } }) });
      await screen.findByRole("heading", { name: "Who asks for what" });
      await user.click(screen.getByRole("radio", { name: "7 days" }));
      await waitFor(() => {
        expect(backend.callsTo("GET /api/apps/shop.example.com/traffic").some((call) => call.search.get("window") === "7d")).toBe(true);
      });
    });

    it("says when nothing was asked for in the window", async () => {
      await metricsAt({
        appStatus: status({ traffic: { available: true, log: "/x", reason: null } }),
        traffic: () => json(200, trafficFixture({ totals: { ts: 0, requests: 0, s2xx: 0, s3xx: 0, s4xx: 0, s5xx: 0, bytes: 0 }, points: [], top: { paths: [], networks: [], statuses: [], referrers: [] } })),
      });
      expect(await screen.findByText("No request in this window.")).toBeInTheDocument();
    });

    it("is not offered when the access log is not being read", async () => {
      const { backend } = await metricsAt();
      await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
      expect(screen.queryByRole("heading", { name: "Who asks for what" })).toBeNull();
      expect(backend.callsTo("GET /api/apps/shop.example.com/traffic")).toHaveLength(0);
    });

    it("shows why it could not be read, in the server's words", async () => {
      await metricsAt({
        appStatus: status({ traffic: { available: true, log: "/x", reason: null } }),
        traffic: () => json(500, { error: "internal_error", detail: "The access log could not be read", hint: null, fields: null, output: null }),
      });
      expect(await screen.findByText("Could not read the traffic")).toBeInTheDocument();
    });
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
    expect(fixCommand({ code: "unit_missing", fix: "Redeploy it: noust update shop.example.com", params: {} })).toBe("noust update shop.example.com");
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
