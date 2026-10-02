import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import type { Timeline, TimelineEvent, TimelineMinute } from "../../api/queries/timeline";
import { createQueryClient } from "../../app/App";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import { TimelineDrawer } from "./TimelineDrawer";
import { busiestMinute, filterEvents, logLines, unitsOf } from "./timelineData";

const START = 1_759_390_000;
const END = START + 3_600;

function event(at: number, fields: Partial<TimelineEvent>): TimelineEvent {
  return { at, source: "journal", kind: "journal", level: "warning", text: "", details: {}, ...fields };
}

const EVENTS: TimelineEvent[] = [
  event(START + 60, { source: "monitor", kind: "boot", level: "notice", text: "Thu 2025-10-02 08:47:40 UTC" }),
  event(START + 300, { source: "audit", kind: "audit", level: "notice", text: "apps.deploy", actor: "alice", status: "ok", details: { target: "app:shop.example.com" } }),
  event(START + 320, { source: "deployments", kind: "deployment", level: "error", text: "Fix the cart", app: "shop.example.com", actor: "panel", status: "failed", ref: "7", details: { error: "build failed" } }),
  event(START + 600, { text: "upstream timed out", unit: "nginx.service", level: "error", priority: 3 }),
  event(START + 1_200, { source: "jobs", kind: "job", level: "info", text: "Back up blog.example.com", app: "blog.example.com", actor: "bob", status: "completed", ref: "job-1", details: { type: "backup" } }),
  event(START + 1_500, { source: "monitor", kind: "observation", level: "warning", text: "node used 97% CPU", details: { process: "node", pid: 1001 } }),
];

function minute(at: number, cpu: number, name = "node"): TimelineMinute {
  return {
    at,
    cpu: [
      { position: 1, pid: 1001, name, user: "shop", cpu_percent: cpu, memory_bytes: 50_000_000, memory_percent: 1, app: "shop.example.com", owner_kind: "unit", owner: "shop-example-com.service", command: "node build.js --token=***" },
      { position: 2, pid: 812, name: "postgres", user: "postgres", cpu_percent: 12, memory_bytes: 900_000_000, memory_percent: 9, owner_kind: "unit", owner: "postgresql.service", command: null },
    ],
    memory: [{ position: 1, pid: 812, name: "postgres", user: "postgres", cpu_percent: 12, memory_bytes: 900_000_000, memory_percent: 9, owner_kind: "unit", owner: "postgresql.service", command: null }],
  };
}

const TIMELINE: Timeline = {
  start: START,
  end: END,
  app: null,
  events: EVENTS,
  processes: { minutes: [minute(START + 1_380, 20, "tsc"), minute(START + 1_440, 97)], total_minutes: 2, since: START + 900, commands: true },
  sources: [
    { source: "journal", state: "shown", count: 1, truncated: false },
    { source: "audit", state: "withheld", count: 0, truncated: false, permission: "audit.read" },
    { source: "deployments", state: "shown", count: 1, truncated: false },
    { source: "jobs", state: "shown", count: 1, truncated: true },
    { source: "monitor", state: "failed", count: 0, truncated: false, message: "Could not read the journal", evidence: "Failed to open journal" },
    { source: "processes", state: "shown", count: 2, truncated: false },
  ],
};

async function renderDrawer(ui: ReactNode) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  const root = createRootRoute();
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => <main>{ui}</main> });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  const view = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  // The drawer is modal: the page behind it is hidden from the accessibility tree.
  await screen.findByRole("main", { hidden: true });
  return view;
}

describe("TimelineDrawer", () => {
  it("asks for exactly the stretch, narrowed to the application, and shows the busiest minute first", async () => {
    const backend = fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, { ...TIMELINE, app: "shop.example.com" }) });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} app="shop.example.com" onClose={vi.fn()} />);

    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });
    expect(drawer).toHaveAccessibleDescription(/narrowed to shop\.example\.com\.$/);
    const call = backend.callsTo("GET /api/timeline")[0];
    expect(call?.search.get("start")).toBe(String(START));
    expect(call?.search.get("end")).toBe(String(END));
    expect(call?.search.get("app")).toBe("shop.example.com");

    // The busiest minute opens by itself: what answers "why this peak".
    const cpu = await within(drawer).findByRole("table", { name: /^The processes with the most CPU at / });
    const first = within(cpu).getAllByRole("row")[1];
    expect(first).toHaveTextContent("node");
    expect(first).toHaveTextContent("97%");
    expect(first).toHaveTextContent("shop.example.com");
  });

  it("says what it could not show: a source withheld with its permission, one that failed with its words, one cut short", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });

    expect(await within(drawer).findByText(/Audit log is not shown: reading it needs the/)).toHaveTextContent("audit.read");
    expect(within(drawer).getByText("Monitor could not be read.")).toBeInTheDocument();
    expect(within(drawer).getByText("Failed to open journal")).toBeInTheDocument();
    expect(within(drawer).getByText("Jobs had more entries than a timeline lists; 1 are shown.")).toBeInTheDocument();
    expect(within(drawer).getByText(/^No process samples before /)).toBeInTheDocument();
  });

  it("opens another minute's processes from the table of minutes", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });
    const user = userEvent.setup();

    const minutes = await within(drawer).findByRole("table", { name: "The busiest process of each minute" });
    const earlier = within(minutes).getAllByRole("row")[1];
    if (earlier === undefined) throw new Error("no minute row");
    await user.click(earlier);

    const cpu = within(drawer).getByRole("table", { name: /^The processes with the most CPU at / });
    expect(within(cpu).getAllByRole("row")[1]).toHaveTextContent("tsc");
  });

  it("counts the events, and links deployments and jobs to where they are opened", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });

    expect(await within(drawer).findByText("6 events")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: "Deployment 7 of shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com/deployments/7");
    expect(within(drawer).getByRole("link", { name: "Backup" })).toHaveAttribute("href", "/activity");
  });

  it("shows the failure in its place when the timeline cannot be read", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => problem(400, "validation_error", "The end of the stretch must come after its start") });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });

    expect(await within(drawer).findByText("Could not load what happened in this stretch")).toBeInTheDocument();
  });

  it("says a server without the timeline runs an older Noust, rather than that it failed", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => problem(404, "not_found", "Not Found") });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });

    expect(await within(drawer).findByText("This server runs an older Noust")).toBeInTheDocument();
    expect(within(drawer).getByText("Investigating a stretch needs Noust 3.2 or later on this server.")).toBeInTheDocument();
    expect(within(drawer).queryByText("Could not load what happened in this stretch")).not.toBeInTheDocument();
  });

  it("closes from its own close button", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    const onClose = vi.fn();
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={onClose} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });
    await userEvent.setup().click(within(drawer).getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("has no accessibility violations", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    const drawer = await screen.findByRole("dialog", { name: "What happened in this stretch" });
    await within(drawer).findByRole("table", { name: "The busiest process of each minute" });
    await expectNoAxeViolations(drawer);
  });

  it("speaks Spanish once the language switches", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/timeline": () => json(200, TIMELINE) });
    await act(() => setLocale("es"));
    await renderDrawer(<TimelineDrawer stretch={[START, END]} onClose={vi.fn()} />);
    expect(await screen.findByRole("dialog", { name: "Qué pasó en este intervalo" })).toBeInTheDocument();
    expect(await screen.findByText(/Registro de auditoría no se muestra/)).toBeInTheDocument();
  });
});

describe("timeline data", () => {
  it("keeps what the filters ask for, and offers every unit named", () => {
    expect(filterEvents(EVENTS, { source: "all", level: "error", unit: "all" }).map((e) => e.kind)).toEqual(["deployment", "journal"]);
    expect(filterEvents(EVENTS, { source: "monitor", level: "all", unit: "all" }).map((e) => e.kind)).toEqual(["boot", "observation"]);
    expect(filterEvents(EVENTS, { source: "all", level: "all", unit: "nginx.service" })).toHaveLength(1);
    expect(unitsOf(EVENTS)).toEqual(["nginx.service"]);
  });

  it("writes each event as a log line of its own values", () => {
    const lines = logLines(EVENTS, "en");
    expect(lines.map((line) => line.level)).toEqual(["info", "info", "error", "error", "info", "warn"]);
    expect(lines[2]?.text).toContain("#7  failed  Fix the cart  build failed");
    expect(lines[3]?.text).toMatch(/^journal\s+nginx\.service {2}upstream timed out$/);
    expect(lines[5]?.text).toContain("node[1001]");
  });

  it("opens the busiest minute, the earliest of equals", () => {
    expect(busiestMinute([])).toBeNull();
    expect(busiestMinute([minute(1, 10), minute(2, 90), minute(3, 90)])?.at).toBe(2);
  });
});
