/**
 * Acting on several applications from the list (checklist M-08): rows are chosen with a checkbox,
 * the action's plan is read before anything is sent (what it will do to each, what it leaves out and
 * why), and then it is done one application at a time, each outcome in the server's own words, the
 * failed ones retried alone.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";

const LIST = {
  total: 4,
  apps: [
    { domain: "shop.example.com", name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace" },
    { domain: "admin.example.com", name: "admin", app_type: "python", status: "failed", active: false, enabled: true, port: 8000, layout: "inplace" },
    { domain: "blog.example.com", name: "blog", app_type: "nextjs", status: "stopped", active: false, enabled: true, port: 3001, layout: "inplace" },
    { domain: "landing.example.com", name: "landing", app_type: "static", status: "static", active: false, enabled: false, layout: "inplace" },
  ],
};

const JOB = {
  id: "96bad296",
  type: "update",
  name: "Update",
  description: "Updating",
  status: "running",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:21:13",
  logs: [],
  metadata: {},
};

const ok = (domain: string, message = "Application restarted") => json(200, { success: true, message, domain });

async function appsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, LIST),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    ...extra,
  });
  const harness = renderConsole("/apps");
  await screen.findByRole("heading", { level: 1, name: "Applications" });
  const table = await screen.findByRole("region", { name: /Applications/ });
  await within(table).findByRole("link", { name: "landing.example.com" });
  return { ...harness, backend, table };
}

async function choose(user: ReturnType<typeof renderConsole>["user"], ...domains: string[]) {
  for (const domain of domains) await user.click(screen.getByRole("checkbox", { name: `Select ${domain}` }));
}

describe("choosing applications", () => {
  it("gives each row a checkbox, counts what is chosen, and lets it all go at once", async () => {
    const { user } = await appsAt();
    expect(screen.queryByRole("button", { name: /^Act on/ })).not.toBeInTheDocument();

    await choose(user, "shop.example.com", "admin.example.com");

    expect(screen.getByRole("button", { name: "Act on 2 selected" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Select all 4" }));
    expect(screen.getByRole("button", { name: "Act on 4 selected" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByRole("button", { name: /^Act on/ })).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Select shop.example.com" })).not.toBeChecked();
  });

  it("forgets a choice the filters have since hidden", async () => {
    const { user } = await appsAt();
    await choose(user, "shop.example.com", "blog.example.com");

    await user.type(screen.getByRole("searchbox", { name: "Search applications" }), "shop");

    expect(await screen.findByRole("button", { name: "Act on 1 selected" })).toBeInTheDocument();
  });
});

describe("the plan", () => {
  it("is read before anything is sent: what is done to each, and what is left out and why", async () => {
    const { user, backend } = await appsAt();
    await choose(user, "shop.example.com", "admin.example.com", "blog.example.com", "landing.example.com");

    await user.click(screen.getByRole("button", { name: "Act on 4 selected" }));

    const dialog = await screen.findByRole("dialog", { name: "Act on 4 applications" });
    expect(within(dialog).getByRole("radio", { name: "Restart" })).toBeChecked();
    const plan = within(dialog).getByRole("list", { name: "What it will do" });
    const item = (domain: string) => within(plan).getByText(domain).closest("li") as HTMLElement;
    expect(within(item("shop.example.com")).getByText("Will restart")).toBeInTheDocument();
    expect(within(item("admin.example.com")).getByText("Will restart")).toBeInTheDocument();
    expect(within(item("landing.example.com")).getByText("Skipped: a static site has no process")).toBeInTheDocument();
    expect(within(dialog).getByText("To do: 3 of 4. Skipped: 1.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("radio", { name: "Stop" }));
    expect(within(item("blog.example.com")).getByText("Skipped: already stopped")).toBeInTheDocument();
    expect(within(dialog).getByText("To do: 2 of 4. Skipped: 2.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: "Start" }));
    expect(within(item("shop.example.com")).getByText("Skipped: already running")).toBeInTheDocument();

    expect(backend.calls.filter((call) => call.method === "POST")).toHaveLength(0);
    await expectNoAxeViolations(dialog);
  });

  it("offers nothing to run when everything is left out", async () => {
    const { user } = await appsAt();
    await choose(user, "landing.example.com");

    await user.click(screen.getByRole("button", { name: "Act on 1 selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Act on 1 application" });

    expect(within(dialog).getByText("To do: 0 of 1. Skipped: 1.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Restart 0 applications" })).toBeDisabled();
  });
});

describe("doing it", () => {
  it("restarts one application at a time, in the order chosen, and says what each did", { timeout: 20_000 }, async () => {
    const { user, backend } = await appsAt({
      "POST /api/apps/shop.example.com/restart": () => ok("shop.example.com"),
      "POST /api/apps/admin.example.com/restart": () => ok("admin.example.com"),
    });
    await choose(user, "shop.example.com", "admin.example.com", "landing.example.com");
    await user.click(screen.getByRole("button", { name: "Act on 3 selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Act on 3 applications" });

    await user.click(within(dialog).getByRole("button", { name: "Restart 2 applications" }));

    await waitFor(() => {
      expect(within(dialog).getByText("Done: 2 of 2")).toBeInTheDocument();
    });
    expect(backend.calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual([
      "/api/apps/shop.example.com/restart",
      "/api/apps/admin.example.com/restart",
    ]);
    const plan = within(dialog).getByRole("list", { name: "What it will do" });
    expect(within(plan).getAllByText("Restarted")).toHaveLength(2);
    expect(within(plan).getByText("Skipped: a static site has no process")).toBeInTheDocument();
  });

  it("says what the server said when one fails, and retries only that one", { timeout: 20_000 }, async () => {
    let attempts = 0;
    const { user, backend } = await appsAt({
      "POST /api/apps/shop.example.com/restart": () => ok("shop.example.com"),
      "POST /api/apps/admin.example.com/restart": () => {
        attempts += 1;
        return attempts === 1 ? problem(500, "service_error", "systemctl restart admin-example-com failed: Unit entered failed state") : ok("admin.example.com");
      },
      "POST /api/apps/blog.example.com/restart": () => ok("blog.example.com"),
    });
    await choose(user, "shop.example.com", "admin.example.com", "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Act on 3 selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Act on 3 applications" });
    await user.click(within(dialog).getByRole("button", { name: "Restart 3 applications" }));

    // One failing does not stop the others.
    expect(await within(dialog).findByText("Done: 2 of 3. Failed: 1")).toBeInTheDocument();
    expect(within(dialog).getByText(/systemctl restart admin-example-com failed: Unit entered failed state/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/blog.example.com/restart")).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "Retry failed" }));

    expect(await within(dialog).findByText("Done: 3 of 3")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/admin.example.com/restart")).toHaveLength(2);
    expect(backend.callsTo("POST /api/apps/shop.example.com/restart")).toHaveLength(1);
    expect(backend.callsTo("POST /api/apps/blog.example.com/restart")).toHaveLength(1);
  });

  it("queues an update for each, and does not call an update with nothing new a failure", { timeout: 20_000 }, async () => {
    const { user, backend } = await appsAt({
      "POST /api/jobs/update": (call) => {
        const body = call.body as { domain: string };
        return body.domain === "admin.example.com"
          ? problem(409, "nothing_new", "The branch has nothing the live build does not have")
          : json(202, { message: "Update job created", job: { ...JOB, metadata: { domain: body.domain } } });
      },
    });
    await choose(user, "shop.example.com", "admin.example.com");
    await user.click(screen.getByRole("button", { name: "Act on 2 selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Act on 2 applications" });
    await user.click(within(dialog).getByRole("radio", { name: "Update" }));
    expect(within(dialog).getByText("Updates are queued and built one at a time.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Update 2 applications" }));

    expect(await within(dialog).findByText("Done: 2 of 2")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/jobs/update").map((call) => call.body)).toEqual([
      { domain: "shop.example.com", force: false },
      { domain: "admin.example.com", force: false },
    ]);
    const plan = within(dialog).getByRole("list", { name: "What it will do" });
    expect(within(plan).getByText("Update queued")).toBeInTheDocument();
    expect(within(plan).getByText("Nothing new to deploy")).toBeInTheDocument();
  });

  it("stops what runs and leaves what is stopped alone", { timeout: 20_000 }, async () => {
    const { user, backend } = await appsAt({ "POST /api/apps/shop.example.com/stop": () => ok("shop.example.com", "Application stopped") });
    await choose(user, "shop.example.com", "blog.example.com");
    await user.click(screen.getByRole("button", { name: "Act on 2 selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Act on 2 applications" });
    await user.click(within(dialog).getByRole("radio", { name: "Stop" }));

    await user.click(within(dialog).getByRole("button", { name: "Stop 1 application" }));

    expect(await within(dialog).findByText("Done: 1 of 1")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "POST").map((call) => call.path)).toEqual(["/api/apps/shop.example.com/stop"]);
    expect(within(dialog).getByText("Stopped")).toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("names the selection, the plan and the outcomes", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    screenWidth(1440);
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/apps": () => json(200, LIST),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      "POST /api/apps/shop.example.com/restart": () => ok("shop.example.com"),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Aplicaciones" });
    await user.click(await screen.findByRole("checkbox", { name: "Seleccionar shop.example.com" }));
    await user.click(screen.getByRole("checkbox", { name: "Seleccionar landing.example.com" }));
    await user.click(screen.getByRole("button", { name: "Actuar sobre 2 seleccionadas" }));
    const dialog = await screen.findByRole("dialog", { name: "Actuar sobre 2 aplicaciones" });
    expect(within(dialog).getByText("Omitida: un sitio estático no tiene proceso")).toBeInTheDocument();
    expect(within(dialog).getByText("Por hacer: 1 de 2. Omitidas: 1.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Reiniciar 1 aplicación" }));

    expect(await within(dialog).findByText("Hechas: 1 de 1")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/shop.example.com/restart")).toHaveLength(1);
  });
});
