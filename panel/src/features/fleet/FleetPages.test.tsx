import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { APPS_VIEW, APPS_VIEWS, JOB, PLAN, SSH_TIMEOUT, SUMMARY_VIEW, centralSession, fleetRoutes } from "./testFixtures";

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

/** A widget of the fleet's panel: the slab under its title. */
async function widget(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name: title, level: 2 });
  const slab = heading.closest("article");
  if (slab === null) throw new Error(`no widget ${title}`);
  return slab;
}

function rows(caption: string): Promise<HTMLElement> {
  return screen.findByRole("list", { name: caption });
}

describe("the fleet's Home", () => {
  it("is titled by the panel of the fleet, puts what needs attention on a line that names its server and opens the fix there, and draws the fleet in courses", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    // The panel that opens at the fleet's address titles the page: there is no "Home" over it.
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Home" })).not.toBeInTheDocument();

    // What needs attention on a server is a line that names the server and opens the fix there.
    const attention = await widget("Needs attention");
    expect(await within(attention).findByText("api.example.com")).toBeInTheDocument();
    expect(within(attention).getByText("web-2")).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: "Diagnose api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com/diagnose");

    // The fleet in courses: a course for each server, each name opening that server, and the one that does not answer says so.
    const courses = await within(await widget("Servers of the fleet")).findByRole("list", { name: "Servers and their applications" });
    expect(within(courses).getByRole("link", { name: "web-2" })).toHaveAttribute("href", "/n/web-2/server");
    expect(within(courses).getByRole("link", { name: /^api\.example\.com\s*Failed$/ })).toHaveAttribute("href", "/n/web-2/apps/api.example.com");
    expect(within(courses).getByText("Not answering")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("counts an alert that fires on a server among what needs attention, with the server it fires on, and lists it among the alerts", { timeout: 30_000 }, async () => {
    // The fleet reads each server's own list, so what the server lists is what the fleet counts:
    // an alert is there because the server says so (managers/overview.py), not because a console added it.
    const alert = {
      id: "alert:disk-high:",
      title: "disk-high",
      severity: "warn",
      subject: { kind: "alert", rule: "disk-high", subject: "" },
      reasons: [{ code: "alert_firing", kind: "alert", severity: "warn", actions: ["acknowledge_alert"], params: { rule: "disk-high", subject: "", since: 1_790_000_000 } }],
    };
    const view = {
      ...SUMMARY_VIEW,
      items: SUMMARY_VIEW.items.map((row) => (row.node === "db-1" ? { ...row, overview: { attention: [alert], attention_total: 1 } } : row)),
    };
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/summary": () => json(200, view) });
    renderConsole("/fleet");
    const attention = await widget("Needs attention");
    expect(await within(attention).findByText("disk-high")).toBeInTheDocument();
    expect(within(attention).getByText("db-1")).toBeInTheDocument();
    expect(within(attention).getByText("This alert is firing.")).toBeInTheDocument();
    expect(within(attention).getByRole("button", { name: "Acknowledge disk-high" })).toBeInTheDocument();
    // It is among the alerts that fire as well, with the server it fires on.
    const firing = await widget("Active alerts");
    expect(await within(firing).findByText("disk-high")).toBeInTheDocument();
    expect(within(firing).getByText("db-1")).toBeInTheDocument();
  });

  it("names the context: All servers in the selector, the fleet in the strip, and no server over the title", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    renderConsole("/fleet");
    expect(await screen.findByRole("button", { name: "Server: all servers" })).toBeInTheDocument();
    // Its name starts with the words the strip draws, in its order (label in name): "4 servers", "3 answering", "1 not answering".
    expect(await screen.findByRole("link", { name: /^In the fleet: 4 servers, 3 answering, 1 not answering/ })).toHaveAttribute("href", "/fleet");
    expect(screen.queryByText(/^Server /)).not.toBeInTheDocument();
  });

  it("does not count the central as a server of the fleet when it is all there is: the strip and the page say the same", { timeout: 20_000 }, async () => {
    // A central that runs applications of its own is a row of the summary (`local`), but a fleet of nothing else is not a
    // fleet of one server: the page under the strip says "No servers in this fleet yet" (item 145).
    const centralOnly = { ...SUMMARY_VIEW, nodes: SUMMARY_VIEW.nodes.filter((node) => node.local), items: SUMMARY_VIEW.items.filter((row) => row["local"] === true) };
    fakeBackend({ ...fleetRoutes(centralSession(), []), "GET /api/fleet/summary": () => json(200, centralOnly) });
    renderConsole("/fleet");

    expect(await screen.findByRole("heading", { level: 2, name: "No servers in this fleet yet" })).toBeInTheDocument();
    await screen.findByRole("heading", { level: 2, name: "No servers in this fleet yet" });
    expect(await screen.findByRole("link", { name: /^In the fleet: no servers added yet/ })).toHaveAttribute("href", "/fleet");
    expect(screen.queryByText("1 server")).not.toBeInTheDocument();
    expect(screen.queryByText(/1 answering/)).not.toBeInTheDocument();
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
  it("says who did not answer in the servers' own words, on Servers, and shows the last answer rather than a blank", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet/servers");
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
    await expectNoAxeViolations(container, { page: true });
  });

  it("lists every server's applications, each naming its server and opening there, with the server as the first filter", { timeout: 30_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/apps/types": () => json(200, { types: [{ type: "python", name: "Python", default_port: 8000 }] }) });
    const { container, user, location } = renderConsole("/fleet/apps");
    const table = await rows("Applications");
    const api = await rowOf(table, "api.example.com");
    expect(within(api).getByRole("link", { name: "api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com");
    expect(within(api).getByRole("link", { name: "Open web-2" })).toBeInTheDocument();
    const reports = await rowOf(table, "reports.example.com");
    // db-1's row is its last good answer: said so, with its age.
    expect(within(reports).getByText(/Last answer/)).toBeInTheDocument();
    // One primary for the view: New application, in the header; Add a server belongs to the other views.
    expect(screen.getAllByRole("link", { name: "New application" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "New application" })).toHaveAttribute("href", "/apps/new");
    expect(screen.queryByRole("button", { name: "Add a server" })).not.toBeInTheDocument();
    // The same words as the local list: the type's name.
    expect(within(api).getByText("Python")).toBeInTheDocument();
    expect(within(api).queryByText("python")).not.toBeInTheDocument();

    // The server is chosen on a strip that says how many each runs: choosing filters, it does not move to another page.
    const strip = screen.getByRole("radiogroup", { name: "Server" });
    expect(within(strip).getByRole("radio", { name: "The whole fleet 3" })).toBeInTheDocument();
    await user.click(within(strip).getByRole("radio", { name: "web-2 1" }));
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
    fakeBackend({ ...fleetRoutes(), "GET /api/views/apps": () => json(200, { ...APPS_VIEWS, items: many }) });
    const { user } = renderConsole("/fleet/apps");
    const table = await rows("Applications");
    // The kit's paging, the same as every other list: 20 to a page until the operator says otherwise.
    expect(await within(table).findByText("app19.example.com")).toBeInTheDocument();
    expect(within(table).queryByText("app20.example.com")).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–20 of 30")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await within(table).findByText("app29.example.com")).toBeInTheDocument();
    expect(screen.getByText("Showing 21–30 of 30")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("says once which servers did not let this account read the applications, and still lists the others'", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...fleetRoutes(),
      "GET /api/views/apps": () =>
        json(200, {
          ...APPS_VIEWS,
          refusals: [
            {
              reason: "permission",
              code: "fleet.refused.permission",
              permission: "apps.read",
              resource: "apps",
              servers: ["db-1"],
              count: 1,
              includes_central: false,
              message: "db-1 does not let your role read apps",
              hint: "Give the role apps.read there.",
              coded: { code: "fleet.refused.permission", params: { role: "viewer", permission: "apps.read" } },
            },
          ],
        }),
    });
    renderConsole("/fleet/apps");
    expect(await screen.findByText("1 server does not let you read applications")).toBeInTheDocument();
    expect(await rowOf(await rows("Applications"), "api.example.com")).toBeInTheDocument();
  });

  it("lists certificates soonest first, backups gaps first and activity, with no violations", { timeout: 40_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container, router } = renderConsole("/fleet/certificates");
    const certificates = await rows("Certificates on every server");
    expect(within(await rowOf(certificates, "www.example.com")).getByText("Expires in 5 days")).toBeInTheDocument();

    await act(() => router.navigate({ to: "/fleet/backups" }));
    const backups = await rows("Backups of every application");
    expect(within(await rowOf(backups, "api.example.com")).getAllByText("No backup").length).toBeGreaterThan(0);
    // The gap leads the list: the application with nothing backed up says so in its own state.
    expect(within(backups).getAllByRole("listitem")[0]).toHaveTextContent("api.example.com");

    await act(() => router.navigate({ to: "/fleet/activity" }));
    const activity = await rows("Activity");
    // The view opens on the changes: the update of web-2 and the central's restart, not the refused sign-in.
    expect(within(activity).getAllByRole("listitem")).toHaveLength(2);
    await expectNoAxeViolations(container, { page: true });
  });

  it("reaches the servers with an update from the column of Server, since the bar has no item for them", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { user } = renderConsole("/fleet/servers");
    const views = await screen.findByRole("navigation", { name: "Server sections" });
    expect(within(views).getByRole("link", { name: "Servers" })).toHaveAttribute("aria-current", "page");
    await user.click(within(views).getByRole("link", { name: "With an update" }));
    const updates = await rows("Noust and system updates of every server");
    // What waits on a server is what it says of itself: its packages, how many are for security, and a restart that is due.
    expect(within(await rowOf(updates, "web-2")).getByText("3 for security")).toBeInTheDocument();
    // The column stays beside the view, with the other one to go back to; the page draws no strip of them.
    expect(within(screen.getByRole("navigation", { name: "Server sections" })).getByRole("link", { name: "With an update" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("navigation", { name: "Fleet server views" })).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Fleet application views" })).not.toBeInTheDocument();
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

  it("updates Noust to the version the operator types, and says the step of each server", { timeout: 40_000 }, async () => {
    const plan = {
      ...PLAN,
      action: "noust_update",
      title: "Update Noust",
      options: { target_version: "3.4.0" },
      nodes: [{ ...PLAN.nodes[0], step: "Noust 2.0.0 -> 3.4.0" }],
      summary: { run: 1, skipped: 0 },
    };
    const backend = fakeBackend({
      ...fleetRoutes(),
      "POST /api/fleet/actions": () => json(200, { plan, job: null }),
    });
    const { user } = renderConsole("/fleet/servers");
    const table = await rows("Servers of this fleet");
    await user.click(within(await rowOf(table, "web-2")).getByRole("checkbox", { name: "Select web-2" }));
    await user.click(screen.getByRole("button", { name: /^Run an action on 1 server/ }));
    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    await user.click(within(dialog).getByRole("radio", { name: "Update Noust" }));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    // A version that is not one is said before anything is asked of the central.
    const target = within(dialog).getByLabelText(/^Update to this version/);
    await user.type(target, "latest");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    expect(await within(dialog).findByText("Use a version such as 3.4.0.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/actions")).toHaveLength(0);

    await user.clear(target);
    await user.type(target, "3.4.0");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    expect(await within(dialog).findByText("Noust 2.0.0 to 3.4.0")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({ action: "noust_update", options: { target_version: "3.4.0" }, plan: true });
  });

  it("sends no version when the field is empty, so each server takes the newest its source offers", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "POST /api/fleet/actions": () => json(200, { plan: { ...PLAN, action: "noust_update", title: "Update Noust", options: { target_version: null } }, job: null }),
    });
    const { user } = renderConsole("/fleet/servers");
    const table = await rows("Servers of this fleet");
    await user.click(within(await rowOf(table, "web-2")).getByRole("checkbox", { name: "Select web-2" }));
    await user.click(screen.getByRole("button", { name: /^Run an action on 1 server/ }));
    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    await user.click(within(dialog).getByRole("radio", { name: "Update Noust" }));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/actions")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({ action: "noust_update", options: { target_version: null } });
  });

  it("renews the package lists of a server whose release is behind, from its row", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "POST /api/fleet/actions": () => json(200, { plan: { ...PLAN, action: "index_refresh", title: "Refresh the package lists", options: {} }, job: null }),
    });
    const { user } = renderConsole("/fleet/servers");
    const updates = await rows("Servers of this fleet");
    await user.click(within(await rowOf(updates, "old-1")).getByRole("button", { name: "Actions for old-1" }));
    await user.click(await screen.findByRole("menuitem", { name: /package lists/i }));
    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/actions")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({ action: "index_refresh", targets: { nodes: ["old-1"] } });
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

    await user.click(screen.getByRole("button", { name: "Retry failed" }));
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

  it("keeps the labels of a server on one line, the first two and +N for the rest, so a row is the one height whatever it carries (item 122)", { timeout: 30_000 }, async () => {
    // The labels are a column of the table; a phone's card leaves them to the menu.
    screenWidth(1440);
    fakeBackend(fleetRoutes());
    renderConsole("/fleet/servers");
    const table = await screen.findByRole("region", { name: "Servers of this fleet" });
    const labelsCell = async (server: string): Promise<HTMLElement> => {
      const row = (await within(table).findByText(server)).closest("tr");
      const cell = row ? within(row).getAllByText(/^(env|role)=/)[0]?.closest("td") : undefined;
      if (!cell) throw new Error(`no labels cell for ${server}`);
      return cell;
    };
    // db-1 carries two labels and web-2 one: each is on the one line of its cell, and no cell asks for room above and below.
    for (const server of ["db-1", "web-2"]) {
      const cell = await labelsCell(server);
      expect(cell).not.toHaveClass("py-1.5");
      expect(cell.querySelector("[data-slot=name-with-tags]"), `${server}'s labels are on one line`).not.toBeNull();
    }
    expect((await labelsCell("db-1")).querySelectorAll("[data-slot=name-with-tags] > span > span")).toHaveLength(2);
  });

  it("says why a view could not be put together at all, in the central's words", { timeout: 20_000 }, async () => {
    fakeBackend({
      ...fleetRoutes(),
      "GET /api/views/apps": () => problem(500, "internal_error", "The aggregator could not start its threads", { hint: "Restart the console on the central." }),
    });
    renderConsole("/fleet/apps");
    expect(await screen.findByText("Could not load the fleet")).toBeInTheDocument();
    expect(screen.getByText("Restart the console on the central.")).toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("speaks Spanish around the servers' own words, with no violations", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet/servers");
    await screen.findByText("2 of 4 servers did not answer fully");
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Servidores" })).toBeInTheDocument();
    expect(screen.getByText("2 de 4 servidores no respondieron del todo")).toBeInTheDocument();
    // The sentence is Spanish; the system's own words, under it, are never translated.
    expect(screen.getByText("db-1 no respondió: sus filas son su última respuesta.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Lo que dijo db-1" })).toHaveTextContent("The tunnel to db-1 did not open");
    await expectNoAxeViolations(container, { page: true });
  });

  it("titles the fleet's panel Flota and speaks Spanish on what needs attention, with no violations", { timeout: 30_000 }, async () => {
    fakeBackend(fleetRoutes());
    const { container } = renderConsole("/fleet");
    await screen.findByRole("heading", { level: 1, name: "Fleet" });
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Flota" })).toBeInTheDocument();
    const attention = await widget("Requiere atención");
    expect(await within(attention).findByText("api.example.com")).toBeInTheDocument();
    expect(within(attention).getByRole("link", { name: "Diagnosticar api.example.com" })).toHaveAttribute("href", "/n/web-2/apps/api.example.com/diagnose");
    expect(within(await widget("Servidores de la flota")).getByText("No responde")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });
});
