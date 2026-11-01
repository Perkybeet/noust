import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Overview } from "../../api/queries/overview";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { expectLabelInName } from "../../test/labelInName";
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

const APPS = {
  total: 4,
  apps: [
    { domain: "admin.example.com", name: "admin", status: "failed", active: false, enabled: true, layout: "inplace" },
    { domain: "shop.example.com", name: "shop", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "blog.example.com", name: "blog", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "old.example.com", name: "old", status: "stopped", active: false, enabled: false, layout: "inplace" },
  ],
};

function routes(extra: Record<string, RouteHandler> = {}, overview = overviewFixture({ attention: ATTENTION, attention_total: 3, activity: ACTIVITY })): Record<string, RouteHandler> {
  return {
    ...signedInRoutes(),
    "GET /api/overview": () => json(200, overview),
    "GET /api/apps": () => json(200, APPS),
    // A young history: the day asked for, with readings only in its last ten minutes.
    "GET /api/metrics/query": (call) => json(200, metricsReadFixture(call.search.getAll("metric"), { now: NOW, cells: 1440, recordedFrom: 1430 })),
    "GET /api/config": () => json(200, { config: { notifications: { enabled: false } }, path: "/etc/noust/config.yaml" }),
    ...extra,
  };
}

const EMPTY = overviewFixture({
  apps: { running: 0, failed: 0, stopped: 0, static: 0, unmanaged: 0, error: null },
  backups: { ...overviewFixture().backups, apps: 0, scheduled: 0, with_backup_24h: 0 },
});

async function home(extra?: Record<string, RouteHandler>, path = "/", data?: ReturnType<typeof overviewFixture>) {
  const backend = fakeBackend(routes(extra, data));
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Home" });
  return { ...harness, backend };
}

/** What needs attention, as the page draws it: the section under its heading. */
async function board(): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { level: 2, name: "Needs attention" });
  const section = heading.closest("section");
  if (section === null) throw new Error("no board");
  return section;
}

describe("Home", () => {
  it("lists what needs attention as cards, each tinted by its state and with where it is fixed", async () => {
    await home();
    const attention = await board();
    const app = await within(attention).findByRole("link", { name: "admin.example.com" });
    expect(app).toHaveAttribute("href", "/apps/admin.example.com");
    expect(within(attention).getAllByText("The service has failed")).toHaveLength(2);
    expect(within(attention).getByText("Last deploy failed")).toBeInTheDocument();
    // The system's own words, verbatim, under the sentence.
    expect(within(attention).getByText("npm ERR! code ELIFECYCLE")).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: "View log of the deploy of admin.example.com" })).toHaveAttribute("href", "/apps/admin.example.com/deployments/12");
    expect(within(attention).getByRole("link", { name: "Open service queue-worker" })).toHaveAttribute("href", "/server/services/queue-worker");
    // Each card's action is named by the words it draws first, then what it is about (WCAG 2.5.3).
    for (const action of within(attention).getAllByRole("link")) expectLabelInName(action);
    expect(within(attention).getByText("Certificate expires in 12 days")).toBeInTheDocument();
    // Only what is wrong has colour behind it: a failure is red, a warning amber.
    const card = (text: string): HTMLElement => {
      const found = within(attention).getByText(text).closest<HTMLElement>(".elev-seated");
      if (found === null) throw new Error(`no card around ${text}`);
      return found;
    };
    expect(card("Last deploy failed")).toHaveClass("bg-fail-soft", "border-fail-border");
    expect(card("Certificate expires in 12 days")).toHaveClass("bg-warn-soft", "border-warn-border");
    // A lone server names no server on a card: there is nothing to tell apart.
    expect(within(attention).queryByText("web-01")).not.toBeInTheDocument();
  });

  it("adds an application nothing answers on, which only the console knows", async () => {
    await home({
      "GET /api/apps": () => json(200, { total: 1, apps: [{ domain: "api.example.com", name: "api", status: "no_answer", active: true, enabled: true, layout: "inplace" }] }),
    });
    const attention = await board();
    expect(await within(attention).findByText("The service runs, but nothing answers on its port")).toBeInTheDocument();
  });

  it("says in one line that nothing needs attention on a calm server", async () => {
    await home(undefined, "/", overviewFixture());
    expect(await screen.findByText("Nothing needs attention")).toBeInTheDocument();
  });

  it("shows six cards until asked for all", async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({ ...ATTENTION[1], id: `unit:w${String(i)}`, subject: { kind: "unit", name: `w${String(i)}` }, title: `w${String(i)}` }));
    const { user } = await home(undefined, "/", overviewFixture({ attention: many as typeof ATTENTION, attention_total: 8 }));
    const attention = await board();
    await within(attention).findByRole("link", { name: "w0" });
    expect(within(attention).queryByRole("link", { name: "w7" })).not.toBeInTheDocument();
    await user.click(within(attention).getByRole("button", { name: "Show all 8" }));
    expect(within(attention).getByRole("link", { name: "w7" })).toBeInTheDocument();
  });

  it("draws the server in courses: each application a slab, the ones with a problem first and said in words", async () => {
    await home();
    const courses = await screen.findByRole("list", { name: "Servers and their applications" });
    expect(within(courses).getByText("web-01")).toBeInTheDocument();
    // A fleet of one shows every application, not "+N running": there is nothing else to compare with.
    expect(within(courses).queryByText(/^\+\d+ running$/)).not.toBeInTheDocument();
    expect(within(courses).getByRole("link", { name: /^admin\.example\.com\s*Failed$/ })).toHaveAttribute("href", "/apps/admin.example.com");
    expect(within(courses).getByRole("link", { name: /shop\.example\.com/ })).toBeInTheDocument();
    expect(within(courses).getByRole("link", { name: "web-01" })).toHaveAttribute("href", "/server");
    // The legend over the courses counts the states.
    const legend = screen.getByRole("list", { name: "Applications by state" });
    expect(legend).toHaveTextContent("Running 2");
    expect(legend).toHaveTextContent("Failed 1");
    expect(legend).toHaveTextContent("Stopped 1");
  });

  it("tells what happened lately, with the state when it did not simply succeed, and who did it", async () => {
    await home();
    const activity = await screen.findByRole("list", { name: "Recent activity, newest first" });
    expect(within(activity).getByText("Deploy")).toBeInTheDocument();
    expect(within(activity).getByText("Failed")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "All activity" })).toHaveAttribute("href", "/activity");
  });

  it("offers the four things a person comes to do, each leading to the place that does it", async () => {
    await home();
    const quick = (await screen.findByRole("heading", { level: 2, name: "Quick actions" })).closest<HTMLElement>("section");
    if (quick === null) throw new Error("no quick actions");
    expect(within(quick).getByRole("link", { name: "Deploy an application" })).toHaveAttribute("href", "/apps/new");
    expect(within(quick).getByRole("link", { name: "Add a server" })).toHaveAttribute("href", "/fleet?add=true");
    expect(within(quick).getByRole("link", { name: "Invite a person" })).toHaveAttribute("href", "/settings/accounts");
    expect(within(quick).getByRole("link", { name: "Create a backup" })).toHaveAttribute("href", "/backups");
  });

  it("has one primary action, the header's, and no figures: the page says what to do, not how many", async () => {
    await home();
    expect(screen.getAllByRole("link", { name: "New application" })).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Key figures" })).not.toBeInTheDocument();
  });

  it("reads the machine's history in one request and says what it shows, below the page", async () => {
    const { backend } = await home();
    const machine = await screen.findByRole("region", { name: "Machine" });
    expect(await within(machine).findByRole("img", { name: /^CPU, last 24 hours, 1-minute averages\. CPU: latest 12%/ })).toBeInTheDocument();
    const calls = backend.callsTo("GET /api/metrics/query");
    expect(calls[0]?.search.getAll("metric")).toEqual([...MACHINE_METRICS]);
    expect(calls[0]?.search.get("window")).toBe("24h");
  });

  it("keeps the chosen range in the URL, and reads that window", async () => {
    const { user, location, backend } = await home();
    await screen.findByRole("img", { name: /^CPU, last 24 hours/ });
    await user.click(screen.getByRole("radio", { name: "1h" }));
    await waitFor(() => {
      expect(location().search).toEqual({ window: "1h" });
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/metrics/query").some((call) => call.search.get("window") === "1h")).toBe(true);
    });
  });

  it("says when history is not being recorded, why, and the command that fixes it", async () => {
    await home({
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
    expect(screen.getAllByText("noust monitor enable").length).toBeGreaterThan(0);
  });

  it("welcomes an empty server with ways to get something live instead of a board of nothing", async () => {
    await home(undefined, "/", EMPTY);
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy your first app" })).toBeInTheDocument();
    // Each way has its own button, and the primary stays the header's: a demo is one press, not a second primary.
    expect(screen.getByRole("link", { name: "Try a demo" })).toHaveAttribute("href", "/apps/new?start=recipe&recipe=hello&deploy=now");
    expect(screen.getByRole("link", { name: "Use a repository" })).toHaveAttribute("href", "/apps/new");
    expect(screen.getAllByRole("link", { name: "New application" })).toHaveLength(1);
    expect(screen.queryByRole("heading", { level: 2, name: "Needs attention" })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("reads the overview of a node before 3.3, which sends no count of stacks outside their units", async () => {
    // Every sum used to be NaN: an empty node was shown a board of nothing, never its first steps.
    const apps = { running: 0, failed: 0, stopped: 0, static: 0, error: null } as unknown as Overview["apps"];
    await home(undefined, "/", overviewFixture({ apps, backups: { ...overviewFixture().backups, apps: 0, scheduled: 0, with_backup_24h: 0 } }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy your first app" })).toBeInTheDocument();
  });

  it("says why Home could not be read, with a way to try again", async () => {
    await home({ "GET /api/overview": () => json(500, { error: "internal_error", detail: "The overview could not be collected" }) });
    expect(await screen.findByText("Could not load Home")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await home();
    await screen.findByRole("list", { name: "Servers and their applications" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says a failed deploy's first line in the console's language, and what a tool printed as it came", async () => {
    const sentence = "Release 20261031-2 did not pass its health check; release 20261031-1 is active again";
    const reason = { kind: "deploy", severity: "fail", params: {}, when: null, actions: ["view_deployment"] };
    const attention = [
      {
        id: "app:shop.example.com",
        subject: { kind: "app", domain: "shop.example.com" },
        title: "shop.example.com",
        severity: "fail",
        reasons: [
          {
            ...reason,
            code: "deploy_failed",
            detail: sentence,
            detail_coded: { code: "deploy_failure.release_unhealthy_back", params: { release: "20261031-2", previous: "20261031-1" } },
            deployment_id: 7,
          },
        ],
      },
      {
        id: "app:admin.example.com",
        subject: { kind: "app", domain: "admin.example.com" },
        title: "admin.example.com",
        severity: "fail",
        reasons: [{ ...reason, code: "deploy_failed", detail: "npm ERR! code ELIFECYCLE", detail_coded: null, deployment_id: 12 }],
      },
    ];
    await act(() => setLocale("es"));
    fakeBackend(routes({}, overviewFixture({ attention, attention_total: 2, activity: [] })));
    renderConsole("/");
    const heading = await screen.findByRole("heading", { level: 2, name: "Requiere atención" });
    const section = heading.closest("section");
    if (section === null) throw new Error("no board");

    const said = await within(section).findByText("La versión 20261031-2 no superó la comprobación de arranque; la versión 20261031-1 está activa de nuevo");
    // Noust's own sentence is said as text, not set in mono as if a program had printed it.
    expect(said).not.toHaveClass("mono");
    expect(within(section).queryByText(sentence)).not.toBeInTheDocument();
    // What npm printed is not ours to translate.
    expect(within(section).getByText("npm ERR! code ELIFECYCLE")).toHaveClass("mono");
  });

  it("speaks Spanish, with the names of the glossary", async () => {
    await act(() => setLocale("es"));
    fakeBackend(routes());
    renderConsole("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Inicio" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Requiere atención" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Aplicaciones" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Actividad reciente" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { level: 2, name: "Acciones rápidas" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
