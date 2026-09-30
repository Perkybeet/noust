import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import { APPS_VIEW, JOB, PLAN, SSH_TIMEOUT, centralSession, fleetRoutes } from "./testFixtures";

/**
 * A list's row by a text in it. The tests run at a phone's width (jsdom matches no media
 * query), where every fleet table is drawn as card rows: a list named by its caption.
 */
async function rowOf(list: HTMLElement, text: string): Promise<HTMLElement> {
  return waitFor(() => {
    const row = within(list)
      .queryAllByRole("listitem")
      .find((candidate) => within(candidate).queryAllByText(text).length > 0);
    if (row === undefined) throw new Error(`no row for ${text}`);
    return row;
  });
}

/** A card of the page, by its title. */
function card(title: string): HTMLElement {
  const heading = screen.getByRole("heading", { name: title, level: 2 });
  const section = heading.closest("section");
  if (section === null) throw new Error(`no card ${title}`);
  return section;
}

function rows(caption: string): Promise<HTMLElement> {
  return screen.findByRole("list", { name: caption });
}

describe("the fleet's summary", () => {
  it("adds every server up, says who did not answer in its own words, and opens problems on their server", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Fleet views" })).toBeInTheDocument();

    // Partial, never blank: db-1's last answer is shown, with why it is old.
    expect(await screen.findByText("2 of 4 servers did not answer fully")).toBeInTheDocument();
    // The console's own sentence and fix, then what the server said, verbatim, in mono: never
    // the backend's English prose standing in for the sentence.
    const troubled = screen.getByRole("list", { name: "Servers that did not answer fully" });
    expect(within(troubled).getByText("db-1 did not answer: its rows are its last answer.")).toBeInTheDocument();
    expect(troubled).toHaveTextContent("Check the connection with noust node test db-1.");
    expect(within(troubled).queryByText("Check it with 'noust node test db-1'.")).not.toBeInTheDocument();
    const said = within(troubled).getByRole("region", { name: "What db-1 said" });
    expect(said).toHaveTextContent("The tunnel to db-1 did not open");
    expect(said).toHaveTextContent(SSH_TIMEOUT);
    expect(screen.getByText("It runs an older Noust, which does not offer GET /api/overview.")).toBeInTheDocument();
    expect(troubled).toHaveTextContent("Update Noust on old-1 to see everything here.");

    expect(screen.getByText("3 of 4")).toBeInTheDocument();
    const attention = card("Needs attention");
    expect(within(attention).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com");
    expect(within(attention).getByText("web-2")).toBeInTheDocument();

    const actions = card("Recent actions");
    expect(within(actions).getByRole("link", { name: "Renew certificates" })).toHaveAttribute("href", "/fleet/jobs/fj-1");

    const table = await rows("The fleet's servers at a glance");
    expect(within(await rowOf(table, "web-2")).getByRole("link", { name: "Open web-2" })).toHaveAttribute("href", "/n/web-2");
    expect(within(await rowOf(table, "old-1")).getByText("Older Noust")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("names the context: All servers in the selector, the fleet in the strip, and no server over the title", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    renderConsole("/fleet");
    expect(await screen.findByRole("button", { name: "Server: all servers" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Servers in the fleet: 4\. Answering: 3\./ })).toHaveAttribute("href", "/fleet");
    expect(screen.queryByText(/^Server /)).not.toBeInTheDocument();
  });

  it("explains what a fleet is when there is no server yet", { timeout: 20_000 }, async () => {
    fakeBackend(fleetRoutes(centralSession(), []));
    renderConsole("/fleet");
    expect(await screen.findByRole("heading", { level: 2, name: "No servers in this fleet yet" })).toBeInTheDocument();
    expect(screen.getByText(/Each one authorizes this console once; it never gets a shell there/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a server" })).toBeInTheDocument();
  });
});

describe("the fleet's views", () => {
  it("lists every server's applications, each naming its server and opening there", { timeout: 30_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/apps/types": () => json(200, { types: [{ type: "python", name: "Python", default_port: 8000 }] }) });
    const { container, user, location } = renderConsole("/fleet/apps");
    const table = await rows("Applications on every server");
    const api = await rowOf(table, "api.example.com");
    expect(within(api).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com");
    expect(within(api).getByRole("link", { name: "Open web-2" })).toBeInTheDocument();
    const reports = await rowOf(table, "reports.example.com");
    // db-1's row is its last good answer: said so, with its age.
    expect(within(reports).getByText(/Last answer/)).toBeInTheDocument();
    // One primary for the view: New application, in the header; Add a server belongs to the
    // other views, and each action to run lives with the view it acts on, never twice.
    expect(screen.getAllByRole("link", { name: "New application" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "New application" })).toHaveAttribute("href", "/apps/new");
    expect(screen.queryByRole("button", { name: "Add a server" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run an action" })).not.toBeInTheDocument();
    // The same words as the local list: the type's name, the last deploy's state and age.
    expect(within(api).getByText("Python")).toBeInTheDocument();
    expect(within(api).queryByText("python")).not.toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Server" }));
    await user.click(await screen.findByRole("option", { name: "web-2" }));
    await waitFor(() => {
      expect(location().search).toMatchObject({ server: "web-2" });
    });
    expect(within(table).queryByText("shop.example.com")).not.toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("shows a long list of applications a page at a time", { timeout: 20_000 }, async () => {
    const many = Array.from({ length: 30 }, (_, i) => ({
      ...(APPS_VIEW.items[0] as Record<string, unknown>),
      domain: `app${String(i).padStart(2, "0")}.example.com`,
      page: `/apps/app${String(i)}.example.com`,
    }));
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/apps": () => json(200, { ...APPS_VIEW, items: many }) });
    const { user } = renderConsole("/fleet/apps");
    const table = await rows("Applications on every server");
    expect(await within(table).findByText("app24.example.com")).toBeInTheDocument();
    expect(within(table).queryByText("app25.example.com")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 25 of 30")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show 5 more" }));
    expect(await within(table).findByText("app29.example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Show \d+ more$/ })).not.toBeInTheDocument();
  });

  it("lists certificates soonest first, backups gaps first, updates and activity, with no violations", { timeout: 40_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container, router } = renderConsole("/fleet/certificates");
    const certificates = await rows("Certificates on every server");
    expect(within(await rowOf(certificates, "www.example.com")).getByText("Expires in 5 days")).toBeInTheDocument();

    await act(() => router.navigate({ to: "/fleet/backups" }));
    const backups = await rows("Backups of every application");
    expect(within(await rowOf(backups, "api.example.com")).getAllByText("No backup").length).toBeGreaterThan(0);
    expect(screen.getByText(/1 without a backup/)).toBeInTheDocument();

    await act(() => router.navigate({ to: "/fleet/updates" }));
    const updates = await rows("Updates of every server");
    expect(within(await rowOf(updates, "web-2")).getByText("3 for security")).toBeInTheDocument();
    expect(within(await rowOf(updates, "db-1")).getByText("Update available")).toBeInTheDocument();

    await act(() => router.navigate({ to: "/fleet/activity" }));
    const activity = await rows("What happened lately on every server");
    expect(within(activity).getAllByRole("listitem")).toHaveLength(2);
    await expectNoAxeViolations(container, { page: true });
  });
});

describe("a bulk action", () => {
  it("chooses servers by hand, shows the plan, runs it and opens its job", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "POST /api/fleet/actions": (call) => {
        const body = call.body as { plan: boolean };
        return body.plan ? json(200, { plan: PLAN, job: null }) : json(200, { plan: PLAN, job: { job_id: "fj-1", status: "pending", message: "queued" } });
      },
    });
    const { user, location } = renderConsole("/fleet/servers");
    const table = await rows("Servers of this fleet");
    await user.click(within(await rowOf(table, "web-2")).getByRole("checkbox", { name: "Select web-2" }));
    await user.click(within(await rowOf(table, "db-1")).getByRole("checkbox", { name: "Select db-1" }));
    await user.click(screen.getByRole("button", { name: "Run an action on 2 servers" }));

    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    // Continue is never disabled: it says what is missing.
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("Choose what to run.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("radio", { name: "Renew certificates" }));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(within(dialog).getByRole("checkbox", { name: "web-2" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "db-1" })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    expect(await within(dialog).findByText("“Renew certificates” will run on 1 server. 1 server is skipped, and says why below.")).toBeInTheDocument();
    expect(within(dialog).getByText("This server does not let this central do this")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({
      action: "certs_renew",
      targets: { nodes: ["web-2", "db-1"] },
      plan: true,
    });
    await user.click(within(dialog).getByRole("button", { name: "Run on 1 server" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/jobs/fj-1");
    });
    expect(backend.callsTo("POST /api/fleet/actions")[1]?.body).toMatchObject({ plan: false });
  });

  it("shows each server's state and words, and retries the failed ones after their plan", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "POST /api/fleet/jobs/fj-1/retry": (call) =>
        call.search.get("plan") === "true"
          ? json(200, { plan: { ...PLAN, nodes: [PLAN.nodes[0]], summary: { run: 1, skipped: 0 } }, job: null })
          : json(200, { plan: PLAN, job: { job_id: "fj-2", status: "pending", message: "queued" } }),
      "GET /api/fleet/jobs/fj-2": () => json(200, { ...JOB, job_id: "fj-2", status: "running", retry_of: "fj-1" }),
    });
    const { container, user, location } = renderConsole("/fleet/jobs/fj-1");
    expect(await screen.findByRole("heading", { level: 1, name: "Renew certificates" })).toBeInTheDocument();
    expect(screen.getByText("1 failed")).toBeInTheDocument();
    const table = await rows("Every server of this action");
    expect(within(await rowOf(table, "db-1")).getByText("This server does not let this central do this")).toBeInTheDocument();
    await user.click(within(await rowOf(table, "web-2")).getByRole("button", { name: "Details for web-2" }));
    const drawer = await screen.findByRole("dialog", { name: "This action on web-2" });
    expect(within(drawer).getByText("certbot failed on web-2")).toBeInTheDocument();
    expect(within(drawer).getByText("Certbot failed to authenticate some domains (authenticator: nginx).")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Retry the failed one" }));
    const retry = await screen.findByRole("dialog", { name: "Retry the servers that did not get done" });
    await user.click(await within(retry).findByRole("button", { name: "Run on 1 server" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/jobs/fj-2");
    });
    expect(backend.callsTo("POST /api/fleet/jobs/fj-1/retry").map((call) => call.search.get("plan"))).toEqual(["true", "false"]);
  });

  it("edits a server's labels, which aim actions at a group", { timeout: 30_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "PUT /api/fleet/servers/web-2/labels": (call) => json(200, { node: "web-2", labels: (call.body as { labels: Record<string, string> }).labels }),
    });
    const { user } = renderConsole("/fleet/servers");
    const table = await rows("Servers of this fleet");
    await user.click(within(await rowOf(table, "web-2")).getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit labels" }));
    const dialog = await screen.findByRole("dialog", { name: "Labels of web-2" });
    const field = within(dialog).getByLabelText(/^Labels/);
    expect(field).toHaveValue("env=prod");
    await user.clear(field);
    await user.type(field, "env prod");
    await user.click(within(dialog).getByRole("button", { name: "Save labels" }));
    expect(await within(dialog).findByText("Write each label as key=value, separated by commas.")).toBeInTheDocument();
    await user.clear(field);
    await user.type(field, "env=staging, team=web");
    await user.click(within(dialog).getByRole("button", { name: "Save labels" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/fleet/servers/web-2/labels").at(-1)?.body).toEqual({ labels: { env: "staging", team: "web" } });
    });
  });

  it("says why a view could not be put together at all, in the central's words", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...fleetRoutes(),
      "GET /api/fleet/apps": () => problem(500, "internal_error", "The aggregator could not start its threads", { hint: "Restart the console on the central." }),
    });
    renderConsole("/fleet/apps");
    expect(await screen.findByText("Could not load the fleet")).toBeInTheDocument();
    expect(screen.getByText("Restart the console on the central.")).toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("speaks Spanish around the servers' own words, with no violations", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    await screen.findByText("2 of 4 servers did not answer fully");
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Flota" })).toBeInTheDocument();
    expect(screen.getByText("2 de 4 servidores no respondieron del todo")).toBeInTheDocument();
    // The sentence is Spanish; the system's own words, under it, are never translated.
    expect(screen.getByText("db-1 no respondió: sus filas son su última respuesta.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Lo que dijo db-1" })).toHaveTextContent("The tunnel to db-1 did not open");
    await expectNoAxeViolations(container, { page: true });
  });
});
