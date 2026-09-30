import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../test/fakes";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

const JOBS = [
  {
    id: "a1",
    type: "update",
    name: "Update shop.example.com",
    description: "Updating the application at shop.example.com",
    status: "completed",
    progress: 100,
    total_steps: 100,
    current_step: "",
    created_at: "2026-09-25T09:00:00Z",
    started_at: "2026-09-25T09:00:01Z",
    completed_at: "2026-09-25T09:00:42Z",
    result: null,
    error: null,
    logs: [],
    metadata: { domain: "shop.example.com" },
    actor: "master",
  },
  {
    id: "b2",
    type: "deploy",
    name: "Deploy admin.example.com",
    description: "Deploying admin.example.com",
    status: "failed",
    progress: 40,
    total_steps: 100,
    current_step: "",
    created_at: "2026-09-25T08:00:00Z",
    started_at: "2026-09-25T08:00:01Z",
    completed_at: "2026-09-25T08:00:12Z",
    result: null,
    error: "Build failed: exit code 1",
    logs: [],
    metadata: { domain: "admin.example.com" },
    actor: "token:ci-deploy",
  },
];

const AUDIT_ENTRIES = [
  {
    timestamp: "2026-09-25T09:30:00+00:00",
    action: "auth.login",
    result: "success",
    actor: "9f8e7d6c5b4a",
    client_ip: "203.0.113.1",
    resource: "/api/auth/login",
    detail: null,
  },
  {
    timestamp: "2026-09-25T07:00:00+00:00",
    action: "auth.scope",
    result: "denied",
    actor: "token:ci-deploy",
    client_ip: "203.0.113.7",
    resource: "/api/apps/blog.example.com",
    detail: "scope 'deploy' below required 'admin'",
  },
];

function jobsHandler(all: typeof JOBS) {
  return ({ search }: RecordedCall) => {
    const status = search.get("status");
    const matched = status ? all.filter((job) => job.status === status) : all;
    return json(200, { jobs: matched, total: matched.length, active: 0 });
  };
}

function auditHandler(all: typeof AUDIT_ENTRIES) {
  return ({ search }: RecordedCall) => {
    const result = search.get("result");
    const matched = result ? all.filter((entry) => entry.result === result) : all;
    return json(200, { items: matched, next_before: null });
  };
}

async function activityAt(routes: Record<string, RouteHandler> = {}, path = "/activity?kind=all") {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/jobs": jobsHandler(JOBS),
    "GET /api/audit": auditHandler(AUDIT_ENTRIES),
    ...routes,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Activity" });
  const table = await screen.findByRole("region", { name: /Activity/ });
  return { ...harness, backend, table };
}

describe("the activity timeline", () => {
  it("merges the jobs history and the audit log, newest first", async () => {
    const { table } = await activityAt();
    await within(table).findByText("shop.example.com");
    expect(within(table).getByText("admin.example.com")).toBeInTheDocument();
    expect(within(table).getByText("Sign-in")).toBeInTheDocument();
    expect(within(table).getByText("Request beyond a token's scope refused")).toBeInTheDocument();
  });

  it("opens on operations: what was done, not who signed in", async () => {
    const { table } = await activityAt({}, "/activity");
    await within(table).findByText("shop.example.com");
    expect(within(table).queryByText("Sign-in")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Operations" })).toBeChecked();
  });

  it("asks the server for the view's categories, so a page of sign-ins never hides the jobs", async () => {
    // A log whose newest page is all sign-ins, with more behind it: read unfiltered, the
    // Operations view would show nothing of it, and every job older than the page would wait
    // behind "Load more". Asked for its own categories, the page is the view's.
    const asked: string[][] = [];
    const { table } = await activityAt(
      {
        "GET /api/audit": ({ search }: RecordedCall) => {
          const categories = search.getAll("categories");
          asked.push(categories);
          if (categories.length > 0) return json(200, { items: [], next_before: null });
          return json(200, { items: [AUDIT_ENTRIES[0]], next_before: AUDIT_ENTRIES[0]?.timestamp });
        },
      },
      "/activity",
    );
    await within(table).findByText("shop.example.com");
    expect(within(table).getByText("admin.example.com")).toBeInTheDocument();
    expect(asked).toContainEqual(["change", "config", "account"]);
  });

  it("offers no \"Load more\" until both sources have answered", async () => {
    // Before an answer neither source is complete, which the merge reads as "more": a button
    // drawn under the skeleton would be pushed down the page when the rows arrive.
    let answer: (response: Response) => void = () => undefined;
    const { table } = await activityAt({ "GET /api/audit": () => new Promise<Response>((resolve) => (answer = resolve)) });
    expect(table.querySelector("table")).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    await act(async () => {
      answer(json(200, { items: [AUDIT_ENTRIES[0]], next_before: AUDIT_ENTRIES[0]?.timestamp }));
      await Promise.resolve();
    });
    await within(table).findByText("Sign-in");
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();
  });

  it("names what an action was done to, not the API path", async () => {
    const { table } = await activityAt();
    await within(table).findByText("shop.example.com");
    // The refused scope check was on /api/apps/blog.example.com: its target is the application.
    expect(within(table).getByText("blog.example.com")).toBeInTheDocument();
    expect(within(table).queryByText("/api/apps/blog.example.com")).not.toBeInTheDocument();
  });

  it("leads with the jobs running now, each with its step and its log", async () => {
    const running = { ...JOBS[0], id: "r1", name: "Deploy shop.example.com", status: "running", current_step: "npm ci", completed_at: null };
    const { user } = await activityAt({
      "GET /api/jobs/active": () => json(200, { jobs: [running], total: 1, active: 1 }),
      "GET /api/jobs/r1/log": () => json(200, { job_id: "r1", content: "[12:00:00] [INFO] npm ci", truncated: false, lines: 1 }),
    });
    const progress = (await screen.findByText("Deploy shop.example.com")).closest("div");
    if (!progress) throw new Error("no job in hand");
    expect(within(progress).getByText("npm ci")).toBeInTheDocument();
    await user.click(within(progress).getByRole("button", { name: "View log" }));
    expect(await screen.findByRole("dialog", { name: "Deploy shop.example.com" })).toBeInTheDocument();
  });

  it("shows a quiet note instead of an error when the session cannot read the audit log", async () => {
    const { table } = await activityAt({ "GET /api/audit": () => problem(403, "forbidden", "admin scope required") });
    await within(table).findByText("shop.example.com");
    expect(screen.getByText(/audit log is not open to this session/)).toBeInTheDocument();
    expect(screen.queryByText(/^Could not load/)).not.toBeInTheDocument();
    expect(within(table).queryByText("Sign-in")).not.toBeInTheDocument();
  });

  it("switches views through the URL: sign-ins and access leave the jobs out", async () => {
    const { table, location, user } = await activityAt({}, "/activity");
    await within(table).findByText("shop.example.com");
    await user.click(screen.getByRole("radio", { name: "Sign-ins and access" }));
    await waitFor(() => {
      expect(location().search).toEqual({ kind: "access" });
    });
    await waitFor(() => {
      expect(within(table).queryByText("shop.example.com")).not.toBeInTheDocument();
    });
    expect(within(table).getByText("Sign-in")).toBeInTheDocument();
  });

  it("searches by who, what and to what, through the URL", async () => {
    const { table, location, user } = await activityAt({}, "/activity");
    await within(table).findByText("shop.example.com");
    await user.type(screen.getByRole("searchbox", { name: "Search activity" }), "master");
    await waitFor(() => {
      expect(location().search).toEqual({ q: "master" });
    });
    await waitFor(() => {
      expect(within(table).queryByText("admin.example.com")).not.toBeInTheDocument();
    });
    expect(within(table).getByText("shop.example.com")).toBeInTheDocument();
  });

  it("invites nothing to create on an empty machine: activity has no wizard", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
      "GET /api/audit": () => json(200, { items: [], next_before: null }),
    });
    renderConsole("/activity");
    expect(await screen.findByRole("heading", { level: 2, name: "Nothing has run yet" })).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    const { table } = await activityAt();
    await within(table).findByText("shop.example.com");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has no accessibility violations on the admin-required note", async () => {
    const { table } = await activityAt({ "GET /api/audit": () => problem(403, "forbidden", "admin scope required") });
    await within(table).findByText("shop.example.com");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("reads in Spanish", async () => {
    await act(() => setLocale("es"));
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/jobs": jobsHandler(JOBS),
      "GET /api/audit": auditHandler(AUDIT_ENTRIES),
    });
    renderConsole("/activity?kind=all");
    await screen.findByRole("heading", { level: 1, name: "Actividad" });
    const table = await screen.findByRole("region", { name: /Actividad/ });
    await within(table).findByText("shop.example.com");
    expect(within(table).getByText("Inicio de sesión")).toBeInTheDocument();
    expect(within(table).getByText("Solicitud fuera del alcance de un token rechazada")).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Vista" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Buscar en la actividad" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
