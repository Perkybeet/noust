import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import {
  fakeBackend,
  json,
  onNode,
  problem,
  signedInRoutes,
} from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import {
  PLAN,
  POWER,
  SECURITY,
  SUMMARY,
  onDesktop,
  pendingChange,
  serverRoutes,
} from "./testing";

beforeEach(onDesktop);

function server(path: string, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    ...extra,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

const JOB = {
  id: "j1",
  type: "os_update",
  name: "Apply security updates",
  description: "",
  status: "running",
  progress: 1,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-29T10:00:00Z",
  logs: [{ message: "Unpacking openssl (3.0.13-0ubuntu3.4) ..." }],
};

describe("the Server area", () => {
  it("names the machine, judges it, and counts what is pending on its tabs", async () => {
    server("/server");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Server" }),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("Critical").length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText("web-01").length).toBeGreaterThan(0);
    const tabs = screen.getByRole("navigation", { name: "Server sections" });
    expect(
      within(tabs)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual([
      "Overview",
      "Updates23",
      "Security3",
      "Storage",
      "Services",
      "Logs",
      "System",
    ]);
  });

  it("puts what needs attention first, each with its way out", async () => {
    server("/server");
    const list = await screen.findByRole("list", {
      name: "What needs attention",
    });
    const [first, second] = within(list).getAllByRole("listitem");
    if (first === undefined || second === undefined) throw new Error("fewer than two rows");
    // The critical finding first; pending security updates are a warning, as on the Overview.
    expect(first).toHaveTextContent("Docker publishes ports around the firewall");
    expect(within(first).getByRole("link", { name: "Review" })).toHaveAttribute("href", "/server/security?view=firewall");
    expect(second).toHaveTextContent("4 security updates are pending.");
    expect(within(second).getByRole("link", { name: "Install" })).toHaveAttribute("href", "/server/updates");
    expect(within(second).getByText("Warning")).toHaveClass("sr-only");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("schedules a reboot with its pre-checks in view, and says it read the warnings", async () => {
    const { user, backend } = server("/server", {
      "POST /api/server/power/reboot": () =>
        json(200, {
          id: 1,
          action: "reboot",
          scheduled_for: "2026-09-29T10:01:00+00:00",
          requested_at: "2026-09-29T10:00:00+00:00",
          boot_id: "b",
          status: "scheduled",
        }),
    });
    const list = await screen.findByRole("list", {
      name: "What needs attention",
    });
    await user.click(
      within(list).getByRole("button", { name: "Schedule reboot" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Reboot the server",
    });
    const checks = await within(dialog).findByRole("list", {
      name: "Checks before rebooting",
    });
    expect(
      within(checks).getByText(POWER.checks[2]?.message ?? ""),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Reboot anyway" }),
    );
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/power/reboot")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/power/reboot")[0]?.body).toEqual({
      force: true,
    });
  });

  it("shows a scheduled reboot on every tab, and cancels it without asking twice", async () => {
    const scheduled = {
      id: 1,
      action: "reboot",
      scheduled_for: "2026-09-29T04:00:00+00:00",
      requested_at: "2026-09-28T20:00:00+00:00",
      requested_by: "yago",
      boot_id: "b",
      status: "scheduled",
    };
    const { user, backend } = server("/server/storage", {
      "GET /api/server/summary": () =>
        json(200, { ...SUMMARY, power: { scheduled } }),
      "DELETE /api/server/power/scheduled": () =>
        json(200, { cancelled: true }),
    });
    expect(await screen.findByText(/Reboot scheduled for/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel reboot" }));
    await waitFor(() => {
      expect(
        backend.callsTo("DELETE /api/server/power/scheduled"),
      ).toHaveLength(1);
    });
  });

  it("asks calmly to keep a change to SSH, with the time left and the new login it needs", async () => {
    const change = pendingChange();
    const { user } = server("/server", {
      "GET /api/server/security/changes": () => json(200, [change]),
      "POST /api/server/security/changes/c1a2b3/confirm": () =>
        problem(
          400,
          "accessguarderror",
          "No SSH login since the change was applied",
          { hint: "Log in over SSH from a new terminal, then confirm again." },
        ),
    });
    expect(
      await screen.findByText(
        /An SSH change is waiting for you: it undoes itself in 1:\d\d/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Turn off password logins")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep the change" }));
    expect(
      await screen.findByText(
        "Log in over SSH from a new terminal, then confirm again.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("No SSH login since the change was applied"),
    ).toBeInTheDocument();
  });

  it("says in Spanish what it says in English", async () => {
    await act(async () => {
      await setLocale("es");
    });
    server("/server");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Servidor" }),
    ).toBeInTheDocument();
    const list = await screen.findByRole("list", {
      name: "Lo que requiere atención",
    });
    expect(within(list).getAllByRole("listitem")[1]).toHaveTextContent(
      "Hay 4 actualizaciones de seguridad pendientes.",
    );
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the Updates tab", () => {
  it("lists security updates first, marked, and installs them as a job after reading the plan", async () => {
    const { user, backend } = server("/server/updates", {
      "POST /api/server/updates/apply": () =>
        json(202, {
          job_id: "j1",
          status: "pending",
          message: "Update queued",
          job: JOB,
        }),
      "GET /api/jobs/j1": () => json(200, JOB),
    });
    const table = await screen.findByRole("region", {
      name: "Pending updates",
    });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("linux-image-6.8.0-47-generic");
    expect(rows[0]).toHaveTextContent("Security");
    expect(rows[2]).toHaveTextContent("curl");

    await user.click(
      screen.getByRole("button", { name: "Install security updates (2)" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Install security updates",
    });
    expect(await within(dialog).findByText(PLAN.command)).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Install 2 updates" }),
    );
    await waitFor(() => {
      expect(
        backend.callsTo("POST /api/server/updates/apply")[0]?.body,
      ).toEqual({ scope: "security", full: false, allow_removals: false });
    });
    expect(await screen.findByText("Installing updates")).toBeInTheDocument();
    expect(
      screen.getByText("Unpacking openssl (3.0.13-0ubuntu3.4) ..."),
    ).toBeInTheDocument();
  });

  it("lists what a full upgrade removes, and goes on only once it is ticked", async () => {
    const { user, backend } = server("/server/updates", {
      "GET /api/server/updates/plan": () =>
        json(200, { ...PLAN, scope: "all", removals: ["libfoo1"] }),
      "POST /api/server/updates/apply": () =>
        json(202, {
          job_id: "j1",
          status: "pending",
          message: "Update queued",
          job: JOB,
        }),
      "GET /api/jobs/j1": () => json(200, JOB),
    });
    await user.click(
      await screen.findByRole("button", { name: "Install all (3)" }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Install all updates",
    });
    expect(
      await within(dialog).findByText("This upgrade removes 1 package"),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Install 2 updates" }),
    );
    expect(
      await within(dialog).findByText(/Tick Remove these packages to go on/),
    ).toBeInTheDocument();
    expect(backend.callsTo("POST /api/server/updates/apply")).toHaveLength(0);
    await user.click(
      within(dialog).getByRole("checkbox", { name: "Remove these packages" }),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Install 2 updates" }),
    );
    await waitFor(() => {
      expect(
        backend.callsTo("POST /api/server/updates/apply")[0]?.body,
      ).toEqual({ scope: "all", full: false, allow_removals: true });
    });
  });

  it("shows what stands in the way of an update, verbatim, where it was asked for", async () => {
    const { user } = server("/server/updates", {
      "POST /api/server/updates/apply": () =>
        json(409, {
          error: "preflight_failed",
          detail: "An update cannot start now",
          hint: "Wait for what is running to finish, or free some space.",
          fields: null,
          output: null,
          blockers: ["Deploying shop.example.com"],
        }),
    });
    await user.click(
      await screen.findByRole("button", {
        name: "Install security updates (2)",
      }),
    );
    const dialog = await screen.findByRole("dialog", {
      name: "Install security updates",
    });
    await within(dialog).findByText(PLAN.command);
    await user.click(
      within(dialog).getByRole("button", { name: "Install 2 updates" }),
    );
    expect(
      await within(dialog).findByText(
        "Wait for what is running to finish, or free some space.",
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/- Deploying shop\.example\.com/),
    ).toBeInTheDocument();
  });

  it("restarts the services on outdated libraries as a job, saying what waits for a reboot and that the console restarts last", async () => {
    const RESTART_JOB = { ...JOB, id: "j2", type: "service_action", name: "Restart the services on replaced libraries", logs: [{ message: "Restarting nginx.service" }] };
    const { user, backend } = server("/server/updates", {
      "GET /api/server/updates/restarts": () =>
        json(200, {
          services: ["nginx.service", "cron.service", "dbus.service", "noust-web.service"],
          restart: ["nginx.service", "cron.service", "noust-web.service"],
          refused: [{ unit: "dbus.service", reason: "Restarting it ends every session on the server" }],
          restarts_console: true,
        }),
      "POST /api/server/updates/restarts": () =>
        json(202, {
          job_id: "j2",
          status: "pending",
          message: "Restart queued: the console restarts last, and this page reconnects by itself",
          job: RESTART_JOB,
          restart: ["nginx.service", "cron.service", "noust-web.service"],
          refused: [{ unit: "dbus.service", reason: "Restarting it ends every session on the server" }],
          restarts_console: true,
        }),
      "GET /api/jobs/j2": () => json(200, RESTART_JOB),
    });
    await user.click(await screen.findByRole("button", { name: "Restart these services" }));
    const dialog = await screen.findByRole("dialog", { name: "Restart the services on outdated libraries" });
    const order = await within(dialog).findByRole("list", { name: "Restarted, in this order" });
    expect(within(order).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["nginx.service", "cron.service", "noust-web.service"]);
    expect(within(dialog).getByText("dbus.service")).toBeInTheDocument();
    expect(within(dialog).getByText("Restarting it ends every session on the server")).toBeInTheDocument();
    expect(within(dialog).getByText(/The console restarts last/)).toBeInTheDocument();
    // An interruption that loses no data: one question, opened on Cancel.
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Restart 3 services" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/updates/restarts")[0]?.body).toEqual({});
    });
    expect(await screen.findByText("Restarting the services")).toBeInTheDocument();
  });

  it("offers nothing to press when none of the services can be restarted from here", async () => {
    const { user } = server("/server/updates", {
      "GET /api/server/updates/restarts": () =>
        json(200, {
          services: ["dbus.service"],
          restart: [],
          refused: [{ unit: "dbus.service", reason: "Restarting it ends every session on the server" }],
          restarts_console: false,
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Restart these services" }));
    const dialog = await screen.findByRole("dialog", { name: "Restart the services on outdated libraries" });
    expect(await within(dialog).findByText("None of them can be restarted from here: a reboot restarts them.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Restart services" })).toBeDisabled();
  });

  it("has no accessibility violations", async () => {
    server("/server/updates");
    await screen.findByRole("region", { name: "Pending updates" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the Security tab", () => {
  it("lists the open findings with their way out, and fixes one after the host name is typed", async () => {
    const { user, backend } = server("/server/security", {
      "GET /api/server/security/ssh/fixes/disable-passwords": () =>
        json(200, {
          fix: "disable-passwords",
          title: "Turn off password logins",
          check_id: "ssh.password_auth",
          changes: [
            { directive: "passwordauthentication", before: "yes", after: "no" },
          ],
          needed: true,
          allowed: true,
          blockers: [],
          guidance: [],
          proof: "root logged in with a key on 2026-09-27.",
          evidence: [],
        }),
      "POST /api/server/security/checks/ssh.password_auth/fix": () =>
        json(202, { job_id: "j2", status: "pending", message: "queued" }),
      "GET /api/jobs/j2": () =>
        json(200, {
          ...JOB,
          id: "j2",
          type: "server_security",
          name: "Turn off password logins",
        }),
    });
    const findings = await screen.findByRole("list", { name: "Open findings" });
    const row = within(findings)
      .getByText("SSH accepts passwords")
      .closest("li");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Fix…" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Fix: SSH accepts passwords",
    });
    expect(
      await within(dialog).findByText("passwordauthentication"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Undone unless you confirm it"),
    ).toBeInTheDocument();
    const apply = within(dialog).getByRole("button", { name: "Apply fix" });
    expect(apply).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "web-01");
    await user.click(apply);
    await waitFor(() => {
      expect(
        backend.callsTo(
          "POST /api/server/security/checks/ssh.password_auth/fix",
        ),
      ).toHaveLength(1);
    });
    expect(
      await screen.findByText("Changing the server's security"),
    ).toBeInTheDocument();
  });

  it("says a node refused host access, and the command that allows it there", async () => {
    const routes = { ...serverRoutes() };
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/nodes": () =>
        json(200, {
          nodes: [{ name: "web-2", status: "reachable", version: "3.1.0" }],
          total: 1,
        }),
      ...onNode("web-2", {
        ...routes,
        "GET /api/openapi.json": () =>
          json(200, { paths: { "/api/server/summary": { get: {} } } }),
        "GET /api/apps": () => json(200, { apps: [], total: 0 }),
        "POST /api/server/security/firewall/enable": () =>
          problem(
            403,
            "permission_denied",
            "This needs the 'server.host_access' permission, which admin does not hold",
            {
              hint: "Ask a security officer for an account with a role that holds it.",
            },
          ),
        "GET /api/server/security/firewall": () =>
          json(200, {
            firewall: {
              backend: "ufw",
              active: false,
              default_incoming: "deny",
              rules: [],
              installed: true,
              status: "inactive",
              others: [],
              warnings: [],
              error: "",
            },
            ports: [],
            protected_ports: [{ port: 22, reason: "SSH" }],
            session_sources: [],
          }),
      }),
    });
    const { user } = renderConsole("/n/web-2/server/security?view=firewall");
    await user.click(await screen.findByRole("button", { name: "Turn on" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Turn on the firewall",
    });
    await user.type(within(dialog).getByRole("textbox"), "web-01");
    await user.click(
      within(dialog).getByRole("button", { name: "Turn on the firewall" }),
    );
    expect(
      await within(dialog).findByText("web-2 did not allow host access"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(
        "noust fleet access --level admin --host-access on",
      ),
    ).toBeInTheDocument();
  });

  it("runs the checks nobody ran yet, says so while they run, then shows how long ago", async () => {
    let reads = 0;
    server("/server/security", {
      // The first read finds no report and starts the checks; the next finds them done.
      "GET /api/server/security": () => {
        reads += 1;
        return reads === 1
          ? json(200, { checked_at: null, counts: null, attention: [], pending: [], checking: true })
          : json(200, SECURITY);
      },
    });
    expect(await screen.findByText("Checking the server's security")).toBeInTheDocument();
    expect(screen.queryByText(/could not run/)).not.toBeInTheDocument();
    expect(await screen.findByText("2 critical, 1 warnings, 22 passed", { exact: false }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.queryByText("Checking the server's security")).not.toBeInTheDocument();
    expect(screen.getByText(/checked/)).toBeInTheDocument();
  });

  it("keeps the last counts on screen while the checks run again", async () => {
    server("/server/security", {
      "GET /api/server/security": () => json(200, { ...SECURITY, checking: true }),
    });
    expect(await screen.findByText("Checking again")).toBeInTheDocument();
    expect(screen.getByText("2 critical, 1 warnings, 22 passed", { exact: false })).toBeInTheDocument();
  });

  it("has no accessibility violations in any view", async () => {
    const { user } = server("/server/security");
    await screen.findByRole("list", { name: "Open findings" });
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("radio", { name: "SSH" }));
    await screen.findByRole("region", { name: "Authorized SSH keys" });
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("radio", { name: "Firewall and ports" }));
    expect(
      await screen.findByText("Docker publishes 1 port around the firewall"),
    ).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("radio", { name: "Brute force" }));
    expect(
      await screen.findByText("fail2ban is not installed."),
    ).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the Storage, Logs and System tabs", () => {
  it("show the disks and what takes space, and pass axe", async () => {
    server("/server/storage");
    expect(
      await screen.findByRole("list", { name: "Filesystems" }),
    ).toHaveTextContent("/dev/vda1");
    expect(
      await screen.findByRole("region", { name: "What takes space" }),
    ).toHaveTextContent("System logs");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("read the journal of the unit in the address", async () => {
    const { backend } = server("/server/logs?unit=nginx.service&priority=err", {
      "GET /api/server/logs": () =>
        json(200, {
          entries: [
            {
              timestamp: "2026-09-29T10:00:00+0000",
              priority: 3,
              unit: "nginx.service",
              message: "bind() failed",
              pid: 812,
              cursor: "c",
            },
          ],
          next_cursor: null,
          truncated: false,
        }),
    });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/server/logs")).toHaveLength(1);
    });
    const call = backend.callsTo("GET /api/server/logs")[0];
    expect(call?.search.get("unit")).toBe("nginx.service");
    expect(call?.search.get("priority")).toBe("err");
    expect(call?.search.get("since")).toBe("-1h");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("list more processes when asked, and sort them by a column's header (item 55)", async () => {
    const process = (pid: number) => ({
      pid,
      name: `worker-${pid}`,
      user: "www-data",
      unit: null,
      cpu_percent: 1,
      memory_mb: 10,
      command: `worker-${pid}`,
    });
    const { user, backend } = server("/server/system", {
      "GET /api/server/processes": (request) => {
        const limit = Number(request.search.get("limit"));
        return json(200, {
          processes: Array.from({ length: limit }, (_, index) => process(index + 1)),
          units: [],
          total: 213,
        });
      },
    });
    const table = await screen.findByRole("table", { name: "Processes" });
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(11);
    });
    // The sort is the column headers', not a control beside the table that read like tabs.
    expect(screen.queryByRole("radio", { name: "Memory" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show the first 50 of 213" }));

    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(51);
    });
    expect(backend.callsTo("GET /api/server/processes").at(-1)?.search.get("limit")).toBe("50");

    await user.click(within(table).getByRole("button", { name: "Memory" }));

    await waitFor(() => {
      expect(backend.callsTo("GET /api/server/processes").at(-1)?.search.get("sort_by")).toBe("memory");
    });
    expect(within(table).getByRole("columnheader", { name: "Memory" })).toHaveAttribute("aria-sort", "descending");
  });

  it("show the clock, the name, the system and the power, and pass axe", async () => {
    server("/server/system");
    expect(await screen.findByText("Europe/Madrid")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("Ubuntu 24.04.1 LTS").length).toBeGreaterThan(
        1,
      );
    });
    expect(
      screen.getByRole("heading", { name: "Reboot and shutdown" }),
    ).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
