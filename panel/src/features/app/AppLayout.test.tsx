import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { APPS, FakeEventSource, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "./testRoutes";

const DOMAIN = "shop.example.com";

const JOB = {
  id: "96bad296",
  type: "update",
  name: `Update ${DOMAIN}`,
  description: `Updating the application at ${DOMAIN}`,
  status: "running",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-25T19:21:13",
  logs: [{ timestamp: "2026-09-25T19:21:13", level: "info", message: "Job started", step: 0 }],
  metadata: { domain: DOMAIN },
};

const CERT = {
  domain: DOMAIN,
  domains: [DOMAIN, `www.${DOMAIN}`],
  days_remaining: 29,
  expires_on: "2026-10-24",
  valid_until: "2026-10-24 19:20:49+00:00 (VALID: 28 days)",
  auto_renew: true,
};

async function appAt(extra: Record<string, RouteHandler> = {}, path = `/apps/${DOMAIN}`, width = 1440) {
  screenWidth(width);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [CERT], total: 1 }),
    "GET /api/sites": () =>
      json(200, { sites: [{ name: DOMAIN, webserver: "nginx", enabled: true, config_path: "/etc/nginx", has_ssl: true }], total: 1, webserver: "nginx" }),
    "GET /api/deployments": () =>
      json(200, {
        items: [
          { id: 12, domain: DOMAIN, status: "failed", triggered_by: "panel", git_commit: "c07d5e3", git_branch: "main", has_log: true, started_at: "2026-09-25T19:20:35", finished_at: "2026-09-25T19:21:00", error: "npm ERR!" },
          { id: 11, domain: DOMAIN, status: "success", triggered_by: "cli", git_commit: "9f2c41a", git_branch: "main", has_log: true, started_at: "2026-09-25T18:20:35", finished_at: "2026-09-25T18:21:00" },
        ],
        total: 2,
        next_before_id: null,
      }),
    [`GET /api/apps/${DOMAIN}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/domains`]: () =>
      json(200, {
        app: DOMAIN,
        domains: [
          { domain: DOMAIN, kind: "primary", created_at: "2026-09-20T10:00:00+00:00" },
          { domain: `www.${DOMAIN}`, kind: "redirect", created_at: "2026-09-20T10:00:00+00:00" },
          { domain: `blog.${DOMAIN}`, kind: "alias", created_at: "2026-09-24T10:00:00+00:00" },
        ],
      }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "POST /api/jobs/update": () => json(202, { message: "Update job created", job: JOB }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

/** The nth element of a list the test has just proved is long enough. */
function item(items: HTMLElement[], index: number): HTMLElement {
  const found = items[index];
  if (found === undefined) throw new Error(`no element at ${String(index)}`);
  return found;
}

function header() {
  const h1 = screen.getByRole("heading", { level: 1, name: DOMAIN });
  const found = h1.closest("header");
  if (!found) throw new Error("no page header");
  return found;
}

describe("an application's page", () => {
  it("heads the page with its state, type, port and a link to the live site", async () => {
    await appAt({ "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }) });
    const top = header();
    expect(await within(top).findByText("Running")).toBeInTheDocument();
    expect(await within(top).findByText("Next.js")).toBeInTheDocument();
    expect(within(top).getByText("3000").parentElement).toHaveTextContent("Port 3000");
    const live = within(top).getByRole("link", { name: /shop\.example\.com/ });
    await waitFor(() => {
      expect(live).toHaveAttribute("href", `https://${DOMAIN}`);
    });
    expect(live).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("navigation", { name: "Application sections" })).toBeInTheDocument();
    // Update is the one primary action; the other daily verbs sit beside it, never behind a menu.
    expect(within(top).getByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
    expect(within(top).getByRole("button", { name: "Restart" })).toHaveAttribute("data-variant", "secondary");
    expect(within(top).getByRole("button", { name: "Stop" })).toHaveAttribute("data-variant", "secondary");
    expect(within(top).getByRole("button", { name: "Roll back" })).toHaveAttribute("data-variant", "secondary");
    expect(within(top).queryByRole("button", { name: "Start" })).not.toBeInTheDocument();
    // A split button is one control of two parts, so it counts once.
    const primaries = new Set(
      within(top)
        .getAllByRole("button")
        .filter((button) => button.getAttribute("data-variant") === "primary")
        .map((button) => button.closest('[data-slot="split-button"]') ?? button),
    );
    expect(primaries.size).toBe(1);
  });

  it("does Update from the main part and its variations from the menu beside it", async () => {
    const { user, backend } = await appAt({
      "POST /api/jobs/update": () => json(202, { message: "Update job created", job: JOB }),
      [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], layout: "releases" }),
    });
    const top = header();
    await user.click(await within(top).findByRole("button", { name: "More options for Update" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Update without cache", expect.stringMatching(/^Build without serving/)]);
    await user.click(within(menu).getByRole("menuitem", { name: "Update without cache" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: DOMAIN, force: true, no_cache: true });
    });
  });

  it("offers Build without serving only to an app that keeps releases", async () => {
    const { user } = await appAt();
    await user.click(await within(header()).findByRole("button", { name: "More options for Update" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Update without cache"]);
  });

  it("keeps only what is rare behind More actions", async () => {
    const { user } = await appAt();
    const top = header();
    await user.click(await within(top).findByRole("button", { name: "More actions" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Diagnose", expect.stringMatching(/^Clone application/), "Delete application"]);
  });

  it("offers Start, not Stop, while the app is stopped", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], status: "stopped", active: false }) });
    const top = header();
    expect(await within(top).findByRole("button", { name: "Start" })).toHaveAttribute("data-variant", "secondary");
    expect(within(top).queryByRole("button", { name: "Stop" })).not.toBeInTheDocument();
    expect(within(top).getByRole("button", { name: "Restart" })).toBeInTheDocument();
    expect(within(top).getByRole("button", { name: "Roll back" })).toBeInTheDocument();
  });

  it("starts a stopped app in one click, with nothing to confirm", async () => {
    const { user, backend } = await appAt({
      [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], status: "stopped", active: false }),
      [`POST /api/apps/${DOMAIN}/start`]: () => json(200, { success: true, message: "Application started", domain: DOMAIN }),
    });
    await user.click(await within(header()).findByRole("button", { name: "Start" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/start`)).toHaveLength(1);
    });
  });

  it("restarts in one click, with nothing to confirm", async () => {
    const { user, backend } = await appAt({
      [`POST /api/apps/${DOMAIN}/restart`]: () => json(200, { success: true, message: "Application restarted", domain: DOMAIN }),
    });
    await user.click(await within(header()).findByRole("button", { name: "Restart" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/restart`)).toHaveLength(1);
    });
  });

  it("offers only Roll back where there is no service to restart or stop (a static site)", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...APPS[0], app_type: "static", status: "static", active: false }) });
    const top = header();
    expect(await within(top).findByRole("button", { name: "Roll back" })).toBeInTheDocument();
    for (const name of ["Restart", "Stop", "Start"]) expect(within(top).queryByRole("button", { name })).not.toBeInTheDocument();
  });

  it("has no accessibility violations with the daily verbs in the header", async () => {
    await appAt();
    await within(header()).findByRole("button", { name: "Roll back" });
    await expectNoAxeViolations(document.body, { page: true });
  });

  it("keeps eight sections on the strip: Diagnose is reached from the menu, the banner and the Overview", async () => {
    const { user, location } = await appAt({ [`GET /api/apps/${DOMAIN}/diagnose`]: () => new Promise<Response>(() => undefined) });
    const tabs = await screen.findByRole("navigation", { name: "Application sections" });
    const names = within(tabs)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(names.length).toBeLessThanOrEqual(8);
    expect(within(tabs).queryByRole("link", { name: "Diagnose" })).not.toBeInTheDocument();
    await user.click(await within(header()).findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Diagnose" }));
    await waitFor(() => {
      expect(location().pathname).toBe(`/apps/${DOMAIN}/diagnose`);
    });
  });

  it("switches sections where the operator is: the page does not jump to the top", async () => {
    const { user, location } = await appAt();
    const tabs = await screen.findByRole("navigation", { name: "Application sections" });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    try {
      await user.click(within(tabs).getByRole("link", { name: "Deployments" }));
      await waitFor(() => {
        expect(location().pathname).toBe(`/apps/${DOMAIN}/deployments`);
      });
      expect(scrollTo).not.toHaveBeenCalled();
    } finally {
      scrollTo.mockRestore();
    }
  });

  it("keeps every daily verb in view on a phone: Update, then Restart, Stop and Roll back as named icons", async () => {
    const { user } = await appAt({}, `/apps/${DOMAIN}`, 390);
    const top = header();
    expect(await within(top).findByRole("button", { name: "Update" })).toHaveAttribute("data-variant", "primary");
    for (const name of ["Restart", "Stop", "Roll back"]) expect(within(top).getByRole("button", { name })).toBeInTheDocument();
    await user.click(within(top).getByRole("button", { name: "More actions" }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: "Restart" })).not.toBeInTheDocument();
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Diagnose", expect.stringMatching(/^Clone application/), "Delete application"]);
  });

  it("has no accessibility violations on a phone with the verbs as icons", async () => {
    await appAt({}, `/apps/${DOMAIN}`, 390);
    await within(header()).findByRole("button", { name: "Roll back" });
    await expectNoAxeViolations(document.body, { page: true });
  });

  it("queues an update, shows it running, and keeps its failure on screen from the job events", async () => {
    const { user, backend } = await appAt();
    const update = await within(header()).findByRole("button", { name: "Update" });
    await user.click(update);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: DOMAIN, force: false });
    });
    expect(await within(header()).findByText("Updating")).toHaveAttribute("data-state", "deploying");
    expect(screen.getByText(`Updating ${DOMAIN}`)).toBeInTheDocument();

    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", {
        ...JOB,
        status: "failed",
        error: "Application not found: shop.example.com",
        logs: [{ message: "Job failed", level: "error" }],
      });
    });
    expect(await screen.findByText(`Update of ${DOMAIN} failed`)).toBeInTheDocument();
    expect(screen.getByText("Application not found: shop.example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(`Update of ${DOMAIN} failed`)).not.toBeInTheDocument();
  });

  it("asks before rebuilding when the branch has nothing new, in the backend's words, and forces only when told", async () => {
    const detail = "No new commits on main since 9f2c41a, which is live";
    const hint = "Rebuilding the same commit still makes sense when the environment or the dependencies changed, or when the last build broke.";
    const { user, backend } = await appAt({
      "POST /api/jobs/update": (call) =>
        (call.body as { force?: boolean }).force === true
          ? json(202, { message: "Update job created", job: JOB })
          : problem(409, "nothing_new", detail, { hint }),
    });
    await user.click(await within(header()).findByRole("button", { name: "Update" }));
    const dialog = await screen.findByRole("dialog", { name: `Nothing new to deploy to ${DOMAIN}` });
    expect(within(dialog).getByText(detail)).toBeInTheDocument();
    expect(within(dialog).getByText(hint)).toBeInTheDocument();
    // A question, not a failure: no error toast.
    expect(screen.queryByText(`Update of ${DOMAIN} could not be queued`)).not.toBeInTheDocument();
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Rebuild anyway" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/jobs/update").map((call) => call.body)).toEqual([
        { domain: DOMAIN, force: false },
        { domain: DOMAIN, force: true },
      ]);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: `Nothing new to deploy to ${DOMAIN}` })).not.toBeInTheDocument();
    });
    expect(await within(header()).findByText("Updating")).toBeInTheDocument();
  });

  it("leaves the app as it is when the operator does not rebuild the same commit", async () => {
    const { user, backend } = await appAt({
      "POST /api/jobs/update": () => problem(409, "nothing_new", "No new commits on main since 9f2c41a, which is live"),
    });
    await user.click(await within(header()).findByRole("button", { name: "Update" }));
    const dialog = await screen.findByRole("dialog", { name: `Nothing new to deploy to ${DOMAIN}` });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1);
  });

  it("follows the app event: a deploy elsewhere turns the header to deploying", async () => {
    const { queryClient } = await appAt();
    await within(header()).findByText("Running");
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("app", { domain: DOMAIN, status: "deploying" });
    });
    expect(await within(header()).findByText("Deploying")).toBeInTheDocument();
    expect(queryClient.getQueryData(["app", DOMAIN])).toMatchObject({ status: "deploying" });
  });

  it("deletes only once the domain is typed, keeping its files and certificate unless ticked, then returns to the list", async () => {
    const { user, backend, location } = await appAt({
      // Already confirmed it's them: the typed confirmation opens straight away.
      "GET /api/auth/session": () => json(200, { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" }),
      [`DELETE /api/apps/${DOMAIN}`]: () =>
        json(202, { job_id: JOB.id, status: "pending", message: `Deletion queued for ${DOMAIN}`, job: { ...JOB, type: "delete" } }),
    });
    await user.click(await within(header()).findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete application" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Delete ${DOMAIN}` });
    // Nothing that cannot be made again is chosen for the operator.
    const files = within(dialog).getByRole("checkbox", { name: "Also delete its files" });
    const certificate = within(dialog).getByRole("checkbox", { name: "Also delete its certificate" });
    expect(files).not.toBeChecked();
    expect(certificate).not.toBeChecked();
    expect(within(dialog).getByText(/Its files, its certificate and its backups are kept\./)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "Delete application" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), DOMAIN);
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    await waitFor(() => {
      expect(location().pathname).toBe("/apps");
    });
    const call = backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0];
    expect(Object.fromEntries(call?.search ?? [])).toEqual({ remove_files: "false", remove_ssl: "false" });
    expect(backend.callsTo("POST /api/jobs/delete")).toHaveLength(0);
  });

  it("deletes the files and the certificate too only when both are ticked", async () => {
    const { user, backend } = await appAt({
      "GET /api/auth/session": () => json(200, { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" }),
      [`DELETE /api/apps/${DOMAIN}`]: () =>
        json(202, { job_id: JOB.id, status: "pending", message: `Deletion queued for ${DOMAIN}`, job: { ...JOB, type: "delete" } }),
    });
    await user.click(await within(header()).findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete application" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Delete ${DOMAIN}` });
    await user.click(within(dialog).getByRole("checkbox", { name: "Also delete its files" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "Also delete its certificate" }));
    expect(within(dialog).getByText(/its files and its certificate are removed\. Its backups are kept\./)).toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox"), DOMAIN);
    await user.click(within(dialog).getByRole("button", { name: "Delete application" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)).toHaveLength(1);
    });
    expect(Object.fromEntries(backend.callsTo(`DELETE /api/apps/${DOMAIN}`)[0]?.search ?? [])).toEqual({ remove_files: "true", remove_ssl: "true" });
  });

  it("asks before stopping", async () => {
    const { user, backend } = await appAt({
      [`POST /api/apps/${DOMAIN}/stop`]: () => json(200, { success: true, message: "Application stopped", domain: DOMAIN }),
    });
    await user.click(await within(header()).findByRole("button", { name: "Stop" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Stop ${DOMAIN}?` });
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/stop`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Stop application" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/stop`)).toHaveLength(1);
    });
  });

  it("opens one question about going back, from the header, with what it does in it", async () => {
    const { user } = await appAt({
      "GET /api/deployments": () =>
        json(200, {
          items: [
            { id: 12, domain: DOMAIN, status: "success", triggered_by: "cli", git_commit: "c07d5e3", git_branch: "main", has_log: true, started_at: "2026-09-25T19:20:35", rollback_available: false },
            { id: 11, domain: DOMAIN, status: "success", triggered_by: "cli", git_commit: "9f2c41a", git_branch: "main", has_log: true, started_at: "2026-09-25T18:20:35", commit_message: "Add gift cards", rollback_available: true, rollback_method: "instant" },
          ],
          total: 2,
          next_before_id: null,
        }),
    });
    // One click from the header, not two through a menu.
    await user.click(await within(header()).findByRole("button", { name: "Roll back" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Roll back ${DOMAIN}` });
    expect(await within(dialog).findByText("What goes back")).toBeInTheDocument();
    expect(within(dialog).getByText("What stays as it is")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Go back to #11" })).toBeInTheDocument();
  });

  it("says plainly when there is no such application", async () => {
    await appAt({ "GET /api/apps/gone.example.com": () => problem(404, "not_found", "Application not found: gone.example.com") }, "/apps/gone.example.com");
    expect(await screen.findByRole("heading", { level: 2, name: "No application at this domain" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Application sections" })).not.toBeInTheDocument();
  });

  describe("its overview", () => {
    it("says what serves now and what changed, then the last deploys, and which version kept serving through a failure", async () => {
      await appAt();
      const live = await screen.findByRole("region", { name: "Live now" });
      expect(await within(live).findByText("9f2c41a")).toBeInTheDocument();
      const list = await screen.findByRole("list", { name: `Last deployments of ${DOMAIN}` });
      const rows = within(list).getAllByRole("listitem");
      expect(rows).toHaveLength(2);
      expect(within(item(rows, 0)).getByText("Failed")).toBeInTheDocument();
      expect(within(item(rows, 0)).getByText("Version #11 kept serving")).toBeInTheDocument();
      // The live one says so in words and a shape, not only in green.
      expect(within(item(rows, 1)).getByText("Live")).toHaveAttribute("data-state", "running");
      expect(within(list).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual([`/apps/${DOMAIN}/deployments/12`, `/apps/${DOMAIN}/deployments/11`]);
      expect(within(live).queryByRole("button")).not.toBeInTheDocument();
    });

    it("says the last version that served, and marks none Live, while the app's service is down", async () => {
      const app = { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "failed", active: false, enabled: true, port: 3000, layout: "inplace" };
      await appAt({ [`GET /api/apps/${DOMAIN}`]: () => json(200, app) });
      const last = await screen.findByRole("region", { name: "Last version that served" });
      expect(await within(last).findByText("9f2c41a")).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Live now" })).not.toBeInTheDocument();
      const list = await screen.findByRole("list", { name: `Last deployments of ${DOMAIN}` });
      expect(within(list).queryByText("Live")).not.toBeInTheDocument();
    });

    it("leaves what the configuration says to Settings: nothing of how it runs is repeated here", async () => {
      await appAt();
      await screen.findByRole("region", { name: "Live now" });
      expect(screen.queryByRole("region", { name: "How it runs" })).not.toBeInTheDocument();
      expect(screen.queryByText("Process ID")).not.toBeInTheDocument();
      expect(screen.queryByRole("list", { name: /^Last \d+ deploys, oldest first$/ })).not.toBeInTheDocument();
    });

    it("reads its health in one place: uptime, certificate, CPU and memory, with the way to change the limits", async () => {
      await appAt();
      const health = await screen.findByRole("region", { name: "Health" });
      expect(await within(health).findByText("29 days left")).toBeInTheDocument();
      expect(within(health).getByText("Uptime")).toBeInTheDocument();
      expect(within(health).getByText("CPU")).toBeInTheDocument();
      expect(within(health).getByText("Memory")).toBeInTheDocument();
      expect(within(health).getByRole("link", { name: "Edit limits" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/resources`);
    });

    it("says where it answers and whether a push deploys it, each with its way on", async () => {
      await appAt();
      const reach = await screen.findByRole("list", { name: `Where ${DOMAIN} answers and what deploys it` });
      const reachItems = within(reach).getAllByRole("listitem");
      const [answers, push] = reachItems;
      // Three names, one of them a redirect: the one it is said by, and how many more.
      await waitFor(() => {
        expect(answers).toHaveTextContent(`Answers on ${DOMAIN} over HTTPS, and on 2 more names.`);
      });
      expect(within(item(reachItems, 0)).getByRole("link", { name: "Manage domains" })).toHaveAttribute("href", `/apps/${DOMAIN}/domains`);
      expect(push).toHaveTextContent("Pushes to the repository do not deploy this app.");
      expect(within(item(reachItems, 1)).getByRole("link", { name: "Set it up" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/deploy-on-push`);
    });

    it("says deploy on push is on and waiting for its first push", async () => {
      const app = { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace", branch: "main" };
      await appAt({ [`GET /api/apps/${DOMAIN}`]: () => json(200, { ...app, webhook_enabled: true }) });
      const reach = await screen.findByRole("list", { name: `Where ${DOMAIN} answers and what deploys it` });
      const push = item(within(reach).getAllByRole("listitem"), 1);
      await waitFor(() => {
        expect(push).toHaveTextContent("Pushes to main deploy this app. No push has arrived yet.");
      });
      expect(within(push).getByRole("link", { name: "Settings" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/deploy-on-push`);
    });

    it("warns, in one line, that a running app will not come back with the server", async () => {
      const app = { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "running", active: true, enabled: false, port: 3000, layout: "inplace", unit: "shop-example-com.service" };
      await appAt({ [`GET /api/apps/${DOMAIN}`]: () => json(200, app) });
      expect(await screen.findByText("It runs now, but it does not start when the server boots.")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Open the service" })).toHaveAttribute("href", "/services/shop-example-com");
    });

    it("lists the previews of an app that has them on, each with its state, its address and its logs", async () => {
      await appAt({
        [`GET /api/apps/${DOMAIN}/previews`]: () =>
          json(200, {
            settings: { base_domain: "preview.example.com", max_previews: 3, ttl_hours: 168 },
            previews: [
              {
                number: 123,
                kind: "pr",
                provider: "github",
                repository: "acme/shop",
                branch: "feat/cart",
                head_sha: "abc1234def",
                domain: "pr-123-shop.preview.example.com",
                url: "https://pr-123-shop.preview.example.com",
                status: "ready",
                expires_at: "2026-10-01T10:00:00+00:00",
                databases: [],
              },
            ],
          }),
      });
      const previews = await screen.findByRole("region", { name: "Previews" });
      const list = within(previews).getByRole("list", { name: `Previews of ${DOMAIN}` });
      expect(within(list).getByText("#123")).toBeInTheDocument();
      expect(within(list).getByText("feat/cart")).toBeInTheDocument();
      expect(within(list).getByRole("link", { name: /^Open the preview #123/ })).toHaveAttribute("href", "https://pr-123-shop.preview.example.com");
      expect(within(list).getByRole("button", { name: "Logs of the preview #123" })).toBeInTheDocument();
    });

    it("has no section of previews when there are none", async () => {
      await appAt({ [`GET /api/apps/${DOMAIN}/previews`]: () => json(200, { settings: null, previews: [] }) });
      await screen.findByRole("region", { name: "Live now" });
      expect(screen.queryByRole("region", { name: "Previews" })).not.toBeInTheDocument();
    });

    it("has no accessibility violations", async () => {
      await appAt();
      await screen.findByText("29 days left");
      await expectNoAxeViolations(screen.getByRole("main"));
    });
  });
});

describe("a job that ends before its queueing answer arrives", () => {
  it("keeps the newer state the stream already delivered", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { user } = await appAt({
      "POST /api/jobs/update": () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
      // The job's own read never answers: only the cache can say how it ended.
      [`GET /api/jobs/${JOB.id}`]: () => new Promise<Response>(() => undefined),
    });
    await user.click(await within(header()).findByRole("button", { name: "Update" }));
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...JOB, status: "failed", error: "boom" });
    });
    await act(async () => {
      answer(json(202, { message: "Update job created", job: JOB }));
      await Promise.resolve();
    });
    expect(await screen.findByText(`Update of ${DOMAIN} failed`)).toBeInTheDocument();
    expect(within(header()).queryByText("Updating")).not.toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("renders the header, actions and overview in Spanish", async () => {
    await act(() => setLocale("es"));
    await appAt();
    const top = header();
    expect(await within(top).findByText("En ejecución")).toBeInTheDocument();
    expect(within(top).getByText("Puerto")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Secciones de la aplicación" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Actualizar" })).toBeInTheDocument();
    expect(await screen.findByText("Quedan 29 días")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Ahora en producción" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Salud" })).toBeInTheDocument();
    // The last deploys, each named by its number and, when it failed, by the version that kept serving.
    const list = await screen.findByRole("list", { name: `Últimos despliegues de ${DOMAIN}` });
    expect(within(list).getByText("La versión #11 siguió sirviendo")).toBeInTheDocument();
    expect(within(list).getByText("En producción")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("while the app is read", () => {
  it("holds the tabs and the tab until it knows whether a banner goes above them", async () => {
    let answer: (response: Response) => void = () => undefined;
    await appAt({
      [`GET /api/apps/${DOMAIN}`]: () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    });
    expect(screen.getByText(`Loading ${DOMAIN}`)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Application sections" })).not.toBeInTheDocument();
    answer(json(200, { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "running", active: true, enabled: true, port: 3000, layout: "inplace" }));
    expect(await screen.findByRole("navigation", { name: "Application sections" })).toBeInTheDocument();
    expect(screen.queryByText(`Loading ${DOMAIN}`)).not.toBeInTheDocument();
  });
});

describe("an app whose last deploy failed", () => {
  it("says so between the header and the tabs, verbatim, with the log one step away", async () => {
    await appAt();
    const line = await screen.findByText("npm ERR!");
    expect(line).toHaveAttribute("translate", "no");
    const title = screen.getByText(/^The last deploy failed/);
    const tabs = screen.getByRole("navigation", { name: "Application sections" });
    // Above the tabs, so every tab shows it.
    expect(title.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("The version that was live before is still serving.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View log" })).toHaveAttribute("href", `/apps/${DOMAIN}/deployments/12`);
  });

  it("keeps the banner on every tab", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}/env`]: () => json(200, { variables: {}, secrets: {} }) }, `/apps/${DOMAIN}/environment`);
    expect(await screen.findByText(/^The last deploy failed/)).toBeInTheDocument();
  });
});

describe("an app whose service failed", () => {
  it("says what is wrong and offers the diagnosis, on every tab", async () => {
    await appAt({
      [`GET /api/apps/${DOMAIN}`]: () =>
        json(200, { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "failed", active: false, enabled: true, port: 3000, layout: "inplace" }),
    });
    const title = await screen.findByText("The service has stopped with an error");
    const banner = title.closest("[data-tone]");
    expect(banner).toHaveAttribute("data-tone", "error");
    await waitFor(() => {
      expect(banner).toHaveTextContent(/It stopped after the last deploy failed, .+\./);
    });
    if (!(banner instanceof HTMLElement)) throw new Error("no banner");
    expect(within(banner).getByRole("link", { name: "Diagnose" })).toHaveAttribute("href", `/apps/${DOMAIN}/diagnose`);
  });

  it("does not offer the diagnosis on the Diagnose tab, where it already is", async () => {
    await appAt(
      {
        [`GET /api/apps/${DOMAIN}`]: () =>
          json(200, { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "failed", active: false, enabled: true, port: 3000, layout: "inplace" }),
        [`GET /api/apps/${DOMAIN}/diagnose`]: () => new Promise<Response>(() => undefined),
      },
      `/apps/${DOMAIN}/diagnose`,
    );
    const banner = (await screen.findByText("The service has stopped with an error")).closest("[data-tone]");
    if (!(banner instanceof HTMLElement)) throw new Error("no banner");
    expect(within(banner).queryByRole("link", { name: "Diagnose" })).not.toBeInTheDocument();
  });

  it("says nothing answers on its port when the service runs but the app is silent", async () => {
    await appAt({
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      [`GET /api/apps/${DOMAIN}`]: () =>
        json(200, { domain: DOMAIN, name: "shop", app_type: "nextjs", status: "no_answer", active: true, enabled: true, port: 3000, layout: "inplace" }),
    });
    expect(await screen.findByText("Nothing answers on port 3000")).toBeInTheDocument();
  });
});

describe("a Compose stack running outside its unit", { timeout: 20_000 }, () => {
  const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };
  const STACK = { domain: DOMAIN, name: "shop", app_type: "docker-compose", status: "running_unmanaged", active: false, enabled: false, port: 3000, layout: "inplace" };
  const DRY_RUN = " Container shop-web-1  Running\n Container shop-db-1  Running";
  const FOUND = { domain: DOMAIN, unit: "shop-example-com", project: "shop-example-com", containers: ["shop-db-1", "shop-web-1"], enabled: false, dry_run: DRY_RUN, changes: [], reclaimed: false };

  function stackRoutes(reclaim: RouteHandler): Record<string, RouteHandler> {
    return {
      "GET /api/auth/session": () => json(200, ELEVATED),
      "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
      [`GET /api/apps/${DOMAIN}`]: () => json(200, STACK),
      [`POST /api/apps/${DOMAIN}/reclaim`]: reclaim,
    };
  }

  it("says so in amber with a label, and hands it back after showing Compose's rehearsal", async () => {
    const { backend, user } = await appAt(
      stackRoutes((call) => {
        const body = call.body as { preview: boolean };
        return json(200, body.preview ? FOUND : { ...FOUND, reclaimed: true });
      }),
    );
    expect(await screen.findByText("It runs outside Noust")).toBeInTheDocument();
    expect(within(header()).getByText("Running outside Noust")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Hand it back to Noust" }));
    const dialog = await screen.findByRole("dialog", { name: `Hand ${DOMAIN} back to Noust` });
    expect(await within(dialog).findByText((_, element) => element?.tagName === "PRE" && element.textContent === DRY_RUN)).toBeInTheDocument();
    expect(within(dialog).getByText("shop-db-1, shop-web-1")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/reclaim`).map((call) => call.body)).toEqual([{ preview: true, accept_recreate: false }]);
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Hand it back" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/reclaim`).at(-1)?.body).toEqual({ preview: false, accept_recreate: false });
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: `Hand ${DOMAIN} back to Noust` })).not.toBeInTheDocument());
    expect(await screen.findByText(`${DOMAIN} runs under its unit again`)).toBeInTheDocument();
  });

  it("shows Compose's output when the start would recreate, and goes on only once that is accepted", async () => {
    const output = " Container shop-web-1  Recreate\n Container shop-web-1  Recreated";
    const { backend, user } = await appAt(
      stackRoutes((call) => {
        const body = call.body as { preview: boolean; accept_recreate: boolean };
        if (!body.accept_recreate) {
          return json(409, { error: "adoptionrefusederror", detail: `Starting ${DOMAIN}'s stack as Noust would changes what runs`, hint: "Hand it back with accept_recreate.", fields: null, output });
        }
        return json(200, { ...FOUND, changes: ["Container shop-web-1  Recreated"], reclaimed: !body.preview });
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Hand it back to Noust" }));
    const dialog = await screen.findByRole("dialog", { name: `Hand ${DOMAIN} back to Noust` });
    expect(await within(dialog).findByText("Starting the unit would recreate containers")).toBeInTheDocument();
    expect(within(dialog).getByText((_, element) => element?.tagName === "PRE" && element.textContent === output)).toBeInTheDocument();
    const go = within(dialog).getByRole("button", { name: "Hand it back" });
    expect(go).toBeDisabled();

    await user.click(within(dialog).getByRole("checkbox", { name: /Hand it back anyway/ }));
    await user.click(go);
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/reclaim`).at(-1)?.body).toEqual({ preview: false, accept_recreate: true });
    });
  });

  it("is said in Spanish too", async () => {
    await act(() => setLocale("es"));
    await appAt(stackRoutes(() => json(200, FOUND)));
    expect(await screen.findByText("Está en marcha fuera de Noust")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Devolverla a Noust" })).toBeInTheDocument();
  });
});

const PENDING = {
  domain: DOMAIN,
  pending: true,
  needs: "restart",
  since: "2026-10-13T10:00:00",
  deployment_id: 41,
  baseline_created: false,
  counts: { restart: 2, rebuild: 0, next_deploy: 0 },
  items: [
    { area: "limits", key: "memory_max_mb", change: "changed", before: "256", after: "512", needs: "restart" },
    { area: "environment", key: "FEATURE_FLAG", change: "added", before: null, after: null, needs: "restart" },
  ],
};

const ROLLOUT = {
  domain: DOMAIN,
  history_keep: null,
  history_keep_server: 20,
  history_keep_effective: 20,
  stop_timeout: null,
  stop_signal: null,
  stop_supported: true,
  pause_on_rollback: true,
  paused: null,
  held: 0,
  automatic: [],
  freeze: null,
};

describe("what is true of the whole app, on the header's state line", () => {
  it("says how many settings are not applied, beside the banner of a failed deploy and on every tab", async () => {
    const { user } = await appAt({ [`GET /api/apps/${DOMAIN}/pending`]: () => json(200, PENDING) });
    const chip = await within(header()).findByRole("button", { name: "2 not applied" });
    // The failed deploy's banner is there too: the chip does not wait for the page to be calm.
    expect(screen.getByText(/^The last deploy failed/)).toBeInTheDocument();
    await user.click(chip);
    const dialog = await screen.findByRole("dialog", { name: `Settings not applied to ${DOMAIN}` });
    expect(within(dialog).getByRole("button", { name: "Restart now" })).toBeInTheDocument();
  });

  it("counts the environment's unapplied settings on its tab, and nothing where there is nothing", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}/pending`]: () => json(200, PENDING) });
    const tabs = await screen.findByRole("navigation", { name: "Application sections" });
    expect(within(tabs).getByRole("link", { name: /^Environment\s*1$/ })).toBeInTheDocument();
    expect(within(tabs).getByRole("link", { name: "Deployments" })).toBeInTheDocument();
  });

  it("draws no chip for an app that runs with what it is set to", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}/pending`]: () => json(200, { ...PENDING, pending: false, needs: null, items: [], counts: {} }) });
    await screen.findByRole("navigation", { name: "Application sections" });
    expect(within(header()).queryByRole("button", { name: /not applied/ })).not.toBeInTheDocument();
  });

  it("says deploys are paused, and leads to where they are resumed", async () => {
    const { user, location } = await appAt({
      [`GET /api/apps/${DOMAIN}/rollout`]: () => json(200, { ...ROLLOUT, paused: { at: "2026-10-13T10:00:00+00:00", by: "yago", reason: "Looking into the cart" } }),
    });
    const chip = await within(header()).findByRole("link", { name: "Deploys paused" });
    await user.click(chip);
    await waitFor(() => {
      expect(location().pathname).toBe(`/apps/${DOMAIN}/deployments`);
    });
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("says deploys are frozen when a freeze holds them", async () => {
    await appAt({
      [`GET /api/apps/${DOMAIN}/rollout`]: () =>
        json(200, { ...ROLLOUT, freeze: { id: 1, scope: "app", reason: "Sale weekend", starts_at: "2026-10-13T10:00:00+00:00", ends_at: null, created_by: "yago", active: true } }),
    });
    expect(await within(header()).findByRole("link", { name: "Deploys frozen" })).toBeInTheDocument();
  });

  it("draws neither when the server has no such setting", async () => {
    await appAt({ [`GET /api/apps/${DOMAIN}/rollout`]: () => problem(404, "not_found", "No such route") });
    await screen.findByRole("navigation", { name: "Application sections" });
    expect(within(header()).queryByRole("link", { name: /Deploys (paused|frozen)/ })).not.toBeInTheDocument();
  });
});

describe("the job in hand", () => {
  const RUNNING_JOB = { ...JOB, status: "running", current_step: "npm ci", logs: [{ timestamp: "2026-09-25T19:21:14", level: "info", message: "Installing dependencies", step: 1 }] };

  it("opens the deployment it made, once there is one, from View log", async () => {
    await appAt({
      "GET /api/jobs/active": () => json(200, { jobs: [RUNNING_JOB], total: 1, active: 1 }),
      "GET /api/deployments": () =>
        json(200, {
          items: [{ id: 13, domain: DOMAIN, status: "running", triggered_by: "panel", job_id: JOB.id, git_commit: "c0ffee1", git_branch: "main", has_log: true, started_at: "2026-09-25T19:21:14" }],
          total: 1,
          next_before_id: null,
        }),
    });
    const link = await screen.findByRole("link", { name: "View log" });
    expect(link).toHaveAttribute("href", `/apps/${DOMAIN}/deployments/13`);
    expect(screen.getByRole("button", { name: /^Cancel/ })).toBeInTheDocument();
    // The Deployments tab counts what is building.
    const tabs = screen.getByRole("navigation", { name: "Application sections" });
    expect(within(tabs).getByRole("link", { name: /^Deployments\s*1$/ })).toBeInTheDocument();
  });

  it("opens the job's own output beside the page while it has no deployment yet", async () => {
    // The log viewer sizes its window from offsetHeight, which jsdom reports as 0.
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
    const { user } = await appAt({ "GET /api/jobs/active": () => json(200, { jobs: [RUNNING_JOB], total: 1, active: 1 }) });
    await user.click(await screen.findByRole("button", { name: "View log" }));
    const drawer = await screen.findByRole("dialog", { name: `Updating ${DOMAIN}` });
    expect(await within(drawer).findByText(/Installing dependencies/)).toBeInTheDocument();
  });

  it("offers no log to a job that is still in line: the queue says its place", async () => {
    await appAt({ "GET /api/jobs/active": () => json(200, { jobs: [{ ...RUNNING_JOB, status: "pending" }], total: 1, active: 1 }) });
    await screen.findByText(`Updating ${DOMAIN}`);
    expect(screen.queryByRole("button", { name: "View log" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "View log" })).not.toBeInTheDocument();
  });
});
