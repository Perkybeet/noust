import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { overviewFixture } from "../../features/overview/testFixtures";
import { expectNoAxeViolations } from "../../test/axe";
import { SESSION, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { renderWidget } from "../../test/widget";
import { WIDGETS } from "./catalog";
import type { SizeKey } from "./model";

const AGO = (minutes: number): string => new Date(Date.now() - minutes * 60_000).toISOString();

const ATTENTION = [
  {
    id: "app:api.example.com",
    subject: { kind: "app", domain: "api.example.com" },
    title: "api.example.com",
    severity: "fail",
    reasons: [{ kind: "deploy", severity: "fail", code: "deploy_failed", params: {}, detail: "npm ERR! code ELIFECYCLE", when: AGO(4), deployment_id: 12, actions: ["view_deployment"] }],
  },
  {
    id: "unit:queue-worker",
    subject: { kind: "unit", name: "queue-worker" },
    title: "queue-worker.service",
    severity: "fail",
    reasons: [{ kind: "unit", severity: "fail", code: "unit_failed", params: {}, detail: null, when: AGO(22), deployment_id: null, actions: ["open_service"] }],
  },
  {
    id: "alert:disk:",
    subject: { kind: "alert", rule: "disk-high", subject: "" },
    title: "disk-high",
    severity: "warn",
    reasons: [{ kind: "alert", severity: "warn", code: "alert_firing", params: {}, detail: null, when: null, deployment_id: null, actions: [] }],
  },
  {
    id: "app:shop.example.com",
    subject: { kind: "app", domain: "shop.example.com" },
    title: "shop.example.com",
    severity: "warn",
    reasons: [{ kind: "certificate", severity: "warn", code: "certificate_expires_in", params: { days: 12 }, detail: null, when: null, deployment_id: null, actions: ["renew_certificate"] }],
  },
];

const OVERVIEW = overviewFixture({
  attention: ATTENTION,
  attention_total: 4,
  apps: { running: 9, failed: 1, stopped: 1, static: 1, unmanaged: 0, error: null },
  activity: [
    { id: "job:1", type: "deploy", title: "Deploy api.example.com", status: "failed", domain: "api.example.com", actor: "account:yago", actor_name: "yago", at: AGO(4) },
    { id: "job:2", type: "backup", title: "Back up tienda_prod", status: "completed", domain: null, actor: "schedule", at: AGO(60) },
  ],
  certificates: { total: 4, expiring: 1, expired: 0, next_days: 12, warning_days: 21, error: null },
  disk: { used: 62e9, total: 100e9, percent: 62, free_percent: 38, forecast_full_days: 140, forecast_reason: null, error: null },
  updates: { supported: true, available: 14, security: 2, reboot_required: false, checked_at: null, reason: null, error: null },
});

const APPS = {
  total: 4,
  apps: [
    { domain: "api.example.com", name: "api", status: "failed", active: false, enabled: true, layout: "inplace" },
    { domain: "shop.example.com", name: "shop", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "blog.example.com", name: "blog", status: "running", active: true, enabled: true, layout: "inplace" },
    { domain: "old.example.com", name: "old", status: "stopped", active: false, enabled: false, layout: "inplace" },
  ],
};

const CERTS = {
  total: 3,
  certificates: [
    { domain: "shop.example.com", domains: ["shop.example.com"], serves: [], days_remaining: 12, auto_renew: true, challenge: "http", expires_on: null, issuer: null, key_path: null, path: null, source: "certbot", valid_until: null },
    { domain: "blog.example.com", domains: ["blog.example.com"], serves: [], days_remaining: 60, auto_renew: true, challenge: "http", expires_on: null, issuer: null, key_path: null, path: null, source: "certbot", valid_until: null },
    { domain: "old.example.com", domains: ["old.example.com"], serves: [], days_remaining: 80, auto_renew: false, challenge: "http", expires_on: null, issuer: null, key_path: null, path: null, source: "certbot", valid_until: null },
  ],
};

const BACKUPS = {
  total: 1,
  backups: [
    { backup_id: "b1", domain: "shop.example.com", timestamp: AGO(120), age: "2 hours", size: 1, size_human: "1 B", description: "", tags: [], has_database: true, includes_build: false, includes_env: true, includes_node_modules: false, git_branch: null, git_commit: null, app_type: null, last_verified_at: null, verified_ok: true, database_backups: {} },
  ],
};

const DEPLOYMENTS = {
  total: 2,
  next_before_id: null,
  items: [
    { id: 13, domain: "api.example.com", status: "failed", started_at: AGO(4), finished_at: AGO(3), git_commit: "a91c2f0aabb", commit_message: "Add the shipping calculation", triggered_by: "push", hooks: [] },
    { id: 12, domain: "shop.example.com", status: "success", started_at: AGO(90), finished_at: AGO(88), git_commit: "c07d5e3aabb", commit_message: "Update Next.js to 15.2", triggered_by: "manual", hooks: [] },
  ],
};

const JOBS = {
  active: 2,
  total: 2,
  jobs: [
    { id: "j1", type: "deploy", name: "Deploy metabase.example.net", title: null, description: "", status: "running", state: "running", progress: 3, total_steps: 5, current_step: "Starting the container", created_at: AGO(1), cancellable: false, cancel_requested: false },
    { id: "j2", type: "backup", name: "Back up tienda_prod", title: null, description: "", status: "queued", state: "queued", progress: 0, total_steps: 0, current_step: "", waiting_for: "a free slot", created_at: AGO(1), cancellable: true, cancel_requested: false },
  ],
};

const CRON = {
  total: 2,
  jobs: [
    { name: "clean-sessions", app_domain: "shop.example.com", enabled: true, next_run: new Date(Date.now() + 12 * 60_000).toISOString(), last_run: "", last_result: "", last_exit_code: null, command: "x", schedule: "", on_calendar: "", service: "", timeout: null, user: "", working_directory: "" },
    { name: "rotate-logs", app_domain: "", enabled: true, next_run: new Date(Date.now() + 3_600_000).toISOString(), last_run: "", last_result: "", last_exit_code: null, command: "x", schedule: "", on_calendar: "", service: "", timeout: null, user: "", working_directory: "" },
  ],
};

const DATABASES = {
  total: 2,
  problems: [],
  databases: [
    { engine: "postgresql", name: "tienda_prod", size: "2.4 GB", last_backup: AGO(60), missing: false, actions: [], apps: [], detected_apps: [], noust_state: "tracked", tags: [], tracked: true, unverified: false },
    { engine: "mysql", name: "blog_wp", size: "840 MB", last_backup: null, missing: false, actions: [], apps: [], detected_apps: [], noust_state: "tracked", tags: [], tracked: true, unverified: false },
  ],
};

const PROBES = {
  down: 1,
  status_page: {},
  items: [
    { name: "shop", enabled: true, status: { state: "down", failures: 3, last_checked: null, last_error: null, last_latency_ms: null, last_status: null, since: 0 } },
    { name: "blog", enabled: true, status: { state: "up", failures: 0, last_checked: null, last_error: null, last_latency_ms: null, last_status: 200, since: 0 } },
  ],
};

const SERVICES = {
  total: 3,
  services: [
    { name: "queue-worker.service", active: false, active_state: "failed", status: "failed", enabled: true, managed: true, scheduled: false, tags: [] },
    { name: "noust-web.service", active: true, active_state: "active", status: "running", enabled: true, managed: true, scheduled: false, tags: [] },
    { name: "noust-monitor.service", active: true, active_state: "active", status: "running", enabled: true, managed: true, scheduled: false, tags: [] },
  ],
};

const SECURITY = { checking: false, checked_at: null, attention: [], pending: [], counts: { critical: 1, warning: 1, passed: 18, accepted: 0, unknown: 0, not_applicable: 0 } };

const SERVER_ROUTES: Record<string, RouteHandler> = {
  "GET /api/overview": () => json(200, OVERVIEW),
  "GET /api/apps": () => json(200, APPS),
  "GET /api/certs": () => json(200, CERTS),
  "GET /api/backups": () => json(200, BACKUPS),
  "GET /api/deployments": () => json(200, DEPLOYMENTS),
  "GET /api/jobs/active": () => json(200, JOBS),
  "GET /api/operations/active": () => json(200, { operations: [{ id: "op1", title: { code: "operation.restart_service", params: { name: "nginx" } }, started_at: AGO(1) }] }),
  "GET /api/cron": () => json(200, CRON),
  "GET /api/databases/databases": () => json(200, DATABASES),
  "GET /api/probes": () => json(200, PROBES),
  "GET /api/services": () => json(200, SERVICES),
  "GET /api/server/security": () => json(200, SECURITY),
};

/** The cell of a widget is the container it is drawn in; it is there once the router has drawn the page. */
async function loaded(): Promise<HTMLElement> {
  const widgetCell = await screen.findByTestId("cell");
  await waitFor(() => expect(widgetCell.querySelector("[aria-busy=true]")).toBeNull());
  return widgetCell;
}

/** The sizes a widget is tried at: the smallest it takes and the biggest. */
function sizesOf(sizes: readonly SizeKey[]): SizeKey[] {
  const first = sizes[0];
  const last = sizes[sizes.length - 1];
  return first === undefined || last === undefined || first === last ? (first === undefined ? [] : [first]) : [first, last];
}

describe("every widget of a server's panel", () => {
  for (const widget of WIDGETS.filter((candidate) => candidate.scopes.includes("server"))) {
    for (const size of sizesOf(widget.sizes)) {
      it(`${widget.type} draws something that is not a skeleton at the ${size} size, and passes axe`, async () => {
        renderWidget(widget.type, { size, routes: SERVER_ROUTES });
        const content = await loaded();
        expect(content.textContent.trim(), widget.type).not.toBe("");
        expect(content.querySelector("[data-slot=skeleton]")).toBeNull();
        await expectNoAxeViolations(content);
      });
    }
  }
});

describe("a widget that cannot read what it draws", () => {
  it("says so in its cell with its own Try again, and reads again when asked", async () => {
    let answer: "fail" | "ok" = "fail";
    const handler: RouteHandler = () => (answer === "fail" ? problem(500, "boom", "The store is locked") : json(200, CRON));
    const user = userEvent.setup();
    renderWidget("tasks", { size: "medium", routes: { "GET /api/cron": handler } });
    expect(await screen.findByText("Could not load Scheduled tasks")).toBeInTheDocument();
    answer = "ok";
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByText("clean-sessions")).length).toBeGreaterThan(0);
  });

  it("holds its place with a skeleton in a region that says what it waits for", async () => {
    renderWidget("deployments", { size: "medium", routes: { "GET /api/deployments": () => new Promise<Response>(() => undefined) } });
    const region = await screen.findByText("Loading Recent deployments…");
    expect(region.closest("[aria-busy=true]")).not.toBeNull();
  });
});

describe("Needs attention", () => {
  it("lists what fails, worst first, a line each, with the words Failure and Warning", async () => {
    renderWidget("attention", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    const rows = within(content).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Failure"),
      expect.stringContaining("Failure"),
      expect.stringContaining("Warning"),
      expect.stringContaining("Warning"),
    ]);
    expect(rows[0]).toHaveTextContent("api.example.com");
    expect(rows[0]).toHaveTextContent("Last deploy failed");
    expect(rows[1]).toHaveTextContent("queue-worker.service");
  });

  it("gives each line the one thing to press for it", async () => {
    renderWidget("attention", { size: "wide", routes: SERVER_ROUTES });
    await loaded();
    expect(screen.getByRole("link", { name: "View log of the deploy of api.example.com" })).toHaveAttribute("href", expect.stringContaining("/apps/api.example.com/deployments/12"));
    expect(screen.getByRole("link", { name: "Open service queue-worker.service" })).toBeInTheDocument();
  });

  it("is one calm line when nothing needs anyone", async () => {
    renderWidget("attention", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/overview": () => json(200, overviewFixture()) } });
    expect(await screen.findByText("Nothing needs attention")).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});

describe("Server status", () => {
  it("draws the six figures, each a link to where the whole of it is", async () => {
    renderWidget("figures", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    const links = within(content).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual(["/apps", "/activity", "/domains", "/backups", "/server", "/server/system"]);
    expect(links[0]).toHaveTextContent("Applications");
    expect(links[0]).toHaveTextContent("12");
    expect(links[2]).toHaveTextContent("expires in 12 days");
    expect(links[4]).toHaveTextContent("62%");
    expect(links[5]).toHaveTextContent("14");
  });
});

describe("Work in progress", () => {
  it("counts what runs and what waits, and says what each is doing", async () => {
    renderWidget("work", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("2");
    expect(content).toHaveTextContent("running");
    expect(content).toHaveTextContent(", 1 queued");
    expect(content).toHaveTextContent("Deploy metabase.example.net");
    expect(content).toHaveTextContent("Starting the container");
    expect(content).toHaveTextContent("a free slot");
  });

  it("goes on with the jobs alone when the server has no route for operations", async () => {
    renderWidget("work", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/operations/active": () => problem(404, "not_found", "No such route") } });
    const content = await loaded();
    expect(content).toHaveTextContent("Deploy metabase.example.net");
  });

  it("says that nothing is running", async () => {
    renderWidget("work", { size: "small", routes: { ...SERVER_ROUTES, "GET /api/jobs/active": () => json(200, { active: 0, total: 0, jobs: [] }), "GET /api/operations/active": () => json(200, { operations: [] }) } });
    expect(await screen.findByText("Nothing is running")).toBeInTheDocument();
  });

  it("is the fleet's work, each line with its server, and says which server has not been updated", async () => {
    const rows = {
      "GET /api/fleet/jobs/active": () =>
        json(200, {
          items: [
            { node: "web-01", local: true, status: "ok", jobs: JOBS.jobs.slice(0, 1), operations: [] },
            { node: "web-02", local: false, status: "unsupported", jobs: [], operations: [] },
          ],
          nodes: [],
          servers: [],
          refusals: [],
        }),
    };
    renderWidget("work", { size: "large", scope: "fleet", routes: rows });
    const content = await loaded();
    expect(content).toHaveTextContent("web-01");
    expect(content).toHaveTextContent("Deploy metabase.example.net");
    expect(content).toHaveTextContent("web-02 will count its tasks when it is updated.");
  });
});

describe("Applications", () => {
  it("counts the applications by state and puts the failing ones first, as many as the size has room for", async () => {
    const small = renderWidget("apps", { size: "small", routes: SERVER_ROUTES });
    let content = await loaded();
    expect(content).toHaveTextContent("4");
    expect(content).toHaveTextContent("2running");
    expect(content).toHaveTextContent("1failing");
    // A tile has no room for the names: it says how many there are, not which.
    expect(within(content).queryByRole("link", { name: "api.example.com" })).not.toBeInTheDocument();
    small.unmount();
    renderWidget("apps", { size: "large", routes: SERVER_ROUTES });
    content = await loaded();
    const tiles = within(content).getAllByRole("link");
    expect(tiles.map((tile) => tile.textContent)).toEqual(["api.example.com", "blog.example.com", "shop.example.com", "old.example.com"]);
    expect(tiles[0]).toHaveAttribute("href", "/apps/api.example.com");
  });

  it("says how many more run well when the size has room for only a few", async () => {
    const many = {
      total: 9,
      apps: Array.from({ length: 9 }, (_, index) => ({ domain: `app${String(index)}.example.com`, name: `app${String(index)}`, status: "running", active: true, enabled: true, layout: "inplace" })),
    };
    renderWidget("apps", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/apps": () => json(200, many) } });
    const content = await loaded();
    expect(content).toHaveTextContent("4 more running");
  });

  it("draws for a role of a team, which may read the applications and not the machine's Overview", async () => {
    const team = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read"] };
    const { backend } = renderWidget("apps", { size: "large", routes: { ...SERVER_ROUTES, "GET /api/auth/session": () => json(200, team), "GET /api/overview": () => problem(403, "forbidden", "No") } });
    const content = await loaded();
    expect(content).toHaveTextContent("api.example.com");
    expect(backend.callsTo("GET /api/overview")).toHaveLength(0);
  });

  it("does not call a stopped application one that runs", async () => {
    const stopped = {
      total: 9,
      apps: Array.from({ length: 9 }, (_, index) => ({ domain: `app${String(index)}.example.com`, name: `app${String(index)}`, status: index < 8 ? "stopped" : "running", active: false, enabled: true, layout: "inplace" })),
    };
    renderWidget("apps", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/apps": () => json(200, stopped) } });
    const content = await loaded();
    expect(content).toHaveTextContent("4 more");
    expect(content).not.toHaveTextContent("more running");
  });
});

describe("Certificates", () => {
  it("says how many expire soon and which is first, and how many of the others are fine", async () => {
    renderWidget("certificates", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("1");
    expect(content).toHaveTextContent("expiring soon");
    expect(content).toHaveTextContent("shop.example.com");
    expect(content).toHaveTextContent("in 12 days");
    expect(content).toHaveTextContent("2 more are fine");
  });

  it("is a calm line about the ones that renew when none is close", async () => {
    renderWidget("certificates", { size: "small", routes: { ...SERVER_ROUTES, "GET /api/certs": () => json(200, { total: 1, certificates: CERTS.certificates.slice(1, 2) }) } });
    const content = await loaded();
    expect(content).toHaveTextContent("0");
    expect(content).toHaveTextContent("1 certificates, 1 renew by themselves");
  });
});

describe("Backups", () => {
  it("says when the last one was taken, that it was verified, and what failed today", async () => {
    const failing = overviewFixture({ backups: { ...OVERVIEW.backups, failed_24h: 1 } });
    renderWidget("backups", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/overview": () => json(200, failing) } });
    const content = await loaded();
    expect(content).toHaveTextContent("Last backup");
    expect(content).toHaveTextContent("Verified.");
    await waitFor(() => expect(content).toHaveTextContent("1 failed in the last 24 hours"));
    expect(within(content).getByRole("img", { name: /Backups of the last 14 days: 1 with a backup/ })).toBeInTheDocument();
  });

  it("does not ask the Overview of a role that may not read the server", async () => {
    const team = { ...SESSION, scope: "viewer", permissions: ["self", "apps.read", "backups.read"] };
    const { backend } = renderWidget("backups", { size: "medium", routes: { ...SERVER_ROUTES, "GET /api/auth/session": () => json(200, team) } });
    const content = await loaded();
    expect(content).toHaveTextContent("Last backup");
    expect(backend.callsTo("GET /api/overview")).toHaveLength(0);
  });
});

describe("Recent deployments", () => {
  it("lists them newest first with where, what it said and how it ended", async () => {
    renderWidget("deployments", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    const rows = within(content).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    const [newest] = rows;
    if (newest === undefined) throw new Error("no rows");
    expect(newest).toHaveTextContent("api.example.com");
    expect(newest).toHaveTextContent("Add the shipping calculation");
    expect(newest).toHaveTextContent("a91c2f0");
    expect(within(newest).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/apps/api.example.com/deployments/13");
  });
});

describe("Scheduled tasks, Databases, Services, Probes and Security", () => {
  it("lists the tasks that run next", async () => {
    renderWidget("tasks", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("clean-sessions");
    expect(content).toHaveTextContent("rotate-logs");
  });

  it("lists the databases with their size and whether they have a backup", async () => {
    renderWidget("databases", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("tienda_prod");
    expect(content).toHaveTextContent("2.4 GB");
    expect(content).toHaveTextContent("Backed up");
    expect(content).toHaveTextContent("No backup");
  });

  it("names the services that are down and says the others run", async () => {
    renderWidget("services", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("queue-worker.service");
    expect(content).toHaveTextContent("The other 2 are running.");
  });

  it("says how many probes answer and names the one that does not", async () => {
    renderWidget("probes", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("1");
    expect(content).toHaveTextContent("of 2 answer");
    expect(content).toHaveTextContent("shop");
  });

  it("says what the security checks found", async () => {
    renderWidget("security", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("2");
    expect(content).toHaveTextContent("to look at");
    expect(content).toHaveTextContent("18 of 20 checks are fine");
  });
});

describe("Updates, Disk and Active alerts", () => {
  it("counts the system packages that wait and the security ones", async () => {
    renderWidget("updates", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("14");
    expect(content).toHaveTextContent("2 security");
  });

  it("says how full the disk is and when it will be", async () => {
    renderWidget("disk", { size: "tall", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("62%");
    expect(content).toHaveTextContent("Full in about 140 days");
  });

  it("names the alerts that fire, which the list of what needs attention already has", async () => {
    renderWidget("alerts", { size: "medium", routes: SERVER_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("1");
    expect(content).toHaveTextContent("firing");
    expect(content).toHaveTextContent("disk-high");
  });
});

describe("Quick actions", () => {
  it("offers what the credential may do and nothing else", async () => {
    renderWidget("actions", { size: "wide", routes: SERVER_ROUTES });
    const content = await loaded();
    await waitFor(() => expect(within(content).getAllByRole("link").length).toBeGreaterThan(0));
    const names = within(content).getAllByRole("link").map((link) => link.textContent);
    expect(names).toContain("New application");
    expect(names).toContain("Back up now");
    // The suite's session holds no terminal: it is not offered.
    expect(names).not.toContain("Terminal");
  });
});

/** The central's own answers about two servers: each row carries that server's own Overview. */
const FLEET_SUMMARY = {
  nodes: [],
  servers: [],
  refusals: [],
  items: [
    { name: "central-01", node: "central-01", local: true, reachability: "ok", overview: overviewFixture({ attention: ATTENTION.slice(0, 2), attention_total: 2, activity: OVERVIEW.activity ?? [], certificates: { total: 4, expiring: 1, expired: 0, next_days: 12, warning_days: 21, error: null } }) },
    { name: "web-02", node: "web-02", local: false, reachability: "ok", overview: overviewFixture({ attention: [], backups: { ...OVERVIEW.backups, apps: 3, with_backup_24h: 2, failed_24h: 1, last_at: AGO(120) } }) },
  ],
};

const FLEET_APPS = {
  nodes: [],
  servers: [],
  refusals: [],
  items: [
    { node: "central-01", domain: "api.example.com", status: "failed" },
    { node: "central-01", domain: "shop.example.com", status: "running" },
    { node: "web-02", domain: "grafana.example.com", status: "running" },
  ],
};

const FLEET_UPDATES = {
  nodes: [],
  servers: [],
  refusals: [],
  items: [
    { name: "central-01", node: "central-01", noust: { current_version: "4.2.0", update_state: "up_to_date" }, os: { updates: { pending: 14, security: 2 } } },
    { name: "web-02", node: "web-02", noust: { current_version: "4.1.0", latest_version: "4.2.0", update_state: "update_available" }, os: { updates: { pending: 0, security: 0 } } },
  ],
};

const FLEET_ROUTES: Record<string, RouteHandler> = {
  "GET /api/fleet/summary": () => json(200, FLEET_SUMMARY),
  "GET /api/views/apps": () => json(200, FLEET_APPS),
  "GET /api/fleet/updates": () => json(200, FLEET_UPDATES),
};

describe("every widget of the fleet's panel", () => {
  for (const widget of WIDGETS.filter((candidate) => candidate.scopes.includes("fleet"))) {
    it(`${widget.type} draws something that is not a skeleton, and passes axe`, async () => {
      renderWidget(widget.type, { size: widget.sizes[widget.sizes.length - 1] ?? "medium", scope: "fleet", routes: FLEET_ROUTES });
      const content = await loaded();
      expect(content.textContent.trim(), widget.type).not.toBe("");
      await expectNoAxeViolations(content);
    });
  }

  it("does not ask the server it is on for what is the fleet's to say", async () => {
    const { backend } = renderWidget("apps", { size: "wide", scope: "fleet", routes: FLEET_ROUTES });
    await loaded();
    expect(backend.callsTo("GET /api/overview")).toHaveLength(0);
    expect(backend.callsTo("GET /api/apps")).toHaveLength(0);
  });

  it("names the server of each thing that needs attention", async () => {
    renderWidget("attention", { size: "xlarge", scope: "fleet", routes: FLEET_ROUTES });
    const content = await loaded();
    expect(within(content).getAllByText("central-01").length).toBeGreaterThan(0);
    expect(content).toHaveTextContent("api.example.com");
  });

  it("draws every server with its applications", async () => {
    renderWidget("fleet", { size: "xlarge", scope: "fleet", routes: FLEET_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("central-01");
    expect(content).toHaveTextContent("web-02");
    expect(within(content).getAllByRole("link", { name: /grafana.example.com/ }).length).toBeGreaterThan(0);
  });

  it("adds up the updates of the servers and says which Noust is behind", async () => {
    renderWidget("updates", { size: "large", scope: "fleet", routes: FLEET_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("14");
    expect(content).toHaveTextContent("2 security");
    expect(content).toHaveTextContent("Noust is behind on 1 server");
    expect(content).toHaveTextContent("Noust 4.2.0 available");
  });

  it("adds up the certificates of every server, naming the ones that have something to look at", async () => {
    renderWidget("certificates", { size: "medium", scope: "fleet", routes: FLEET_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("expiring soon");
    expect(content).toHaveTextContent("central-01");
    expect(content).toHaveTextContent("in 12 days");
    expect(content).not.toHaveTextContent("web-02");
  });

  it("says which server failed a backup and how many applications are covered", async () => {
    renderWidget("backups", { size: "medium", scope: "fleet", routes: FLEET_ROUTES });
    const content = await loaded();
    expect(content).toHaveTextContent("Failed on web-02");
    expect(content).toHaveTextContent("of 7 applications have a backup from today");
  });
});
