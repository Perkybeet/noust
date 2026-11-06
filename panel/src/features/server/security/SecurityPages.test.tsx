import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { ITEMS, softwareRoutes } from "../software/testFixtures";
import { fleetRoutes } from "../../fleet/testFixtures";
import { FAIL2BAN, SSH, onDesktop, serverRoutes } from "../testing";

beforeEach(onDesktop);

function page(path: string, extra: Record<string, RouteHandler> = {}, session: typeof SESSION = SESSION) {
  const backend = fakeBackend({ ...signedInRoutes(session), ...serverRoutes(), ...softwareRoutes(), ...extra });
  return { ...renderConsole(path), backend };
}

const BANS = {
  ...FAIL2BAN,
  installed: true,
  running: true,
  web_available: true,
  jails: [
    { name: "sshd", currently_failed: 3, total_failed: 1284, currently_banned: 2, total_banned: 97, banned: ["203.0.113.45", "192.0.2.77"], reads: "journal" },
    { name: "nginx-botsearch", currently_failed: 0, total_failed: 10, currently_banned: 1, total_banned: 12, banned: ["198.51.100.23"], reads: "file" },
  ],
};

describe("Security > Checks", () => {
  it("is titled by itself, says the counts under the title and puts the finding's reason behind a ?", async () => {
    const { user } = page("/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Checks" })).toBeInTheDocument();
    expect(await screen.findByText("2 critical, 1 warnings, 22 passed", { exact: false })).toBeInTheDocument();
    const findings = await screen.findByRole("list", { name: "Open findings" });
    const row = within(findings).getByText("SSH accepts passwords").closest("li");
    if (row === null) throw new Error("no row");
    // The sentence is not on the page: it is asked for, and the system's own words are in it.
    expect(within(row).queryByText("passwordauthentication is yes.")).not.toBeInTheDocument();
    await user.click(within(row).getByRole("button", { name: "Why: SSH accepts passwords" }));
    expect(await screen.findByText("passwordauthentication is yes.")).toBeInTheDocument();
    expect(screen.getByText("passwordauthentication yes")).toBeInTheDocument();
  });

  it("gives each finding the one action that deals with it, the way to its page where the fix is a page", async () => {
    page("/security");
    const findings = await screen.findByRole("list", { name: "Open findings" });
    const updates = within(findings).getByText("Security updates are pending").closest("li");
    if (updates === null) throw new Error("no row");
    expect(within(updates).getByRole("link", { name: "Open" })).toHaveAttribute("href", "/server/updates");
    const docker = within(findings).getByText("Docker publishes ports around the firewall").closest("li");
    if (docker === null) throw new Error("no row");
    expect(within(docker).getByRole("button", { name: "How to fix" })).toBeInTheDocument();
    const passwords = within(findings).getByText("SSH accepts passwords").closest("li");
    if (passwords === null) throw new Error("no row");
    expect(within(passwords).getByRole("button", { name: "Fix…" })).toBeInTheDocument();
  });

  it("says where what is well is kept, and where a framework is chosen", async () => {
    page("/security");
    const well = await screen.findByRole("region", { name: "Well" });
    const empty = within(well).getByText("Accounts can log in with an empty password").closest("li");
    if (empty === null) throw new Error("no row");
    expect(within(empty).getByRole("link", { name: "See SSH" })).toHaveAttribute("href", "/security/ssh");
    const eol = within(well).getByText("The operating system is out of support").closest("li");
    if (eol === null) throw new Error("no row");
    expect(within(eol).getByRole("link", { name: "See the updates" })).toHaveAttribute("href", "/server/updates");
    expect(screen.getByRole("link", { name: "Compliance" })).toHaveAttribute("href", "/settings/compliance");
  });

  it("runs the checks again from the header", async () => {
    const { user, backend } = page("/security", { "POST /api/server/security/checks/refresh": () => json(202, { job_id: "j7", status: "pending", message: "queued" }) });
    await screen.findByRole("list", { name: "Open findings" });
    await user.click(screen.getByRole("button", { name: "Run the checks again" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/checks/refresh")).toHaveLength(1);
    });
  });

  it("draws no way to accept a risk for a role that cannot", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "security.manage") };
    page("/security", {}, reader);
    const findings = await screen.findByRole("list", { name: "Open findings" });
    expect(within(findings).queryByRole("button", { name: /^Actions for/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Security > SSH", () => {
  it("lists the keys that can sign in and two switches for how people do", async () => {
    page("/security/ssh");
    expect(await screen.findByRole("heading", { level: 1, name: "SSH" })).toBeInTheDocument();
    const keys = await screen.findByRole("region", { name: "Authorized SSH keys" });
    expect(await within(keys).findByText("yago@laptop")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Allow signing in with a password" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Allow root to sign in directly" })).toBeChecked();
    // Root with a key only is a sentence, not a state of the switch.
    expect(screen.getByText("Only with a key, never with a password.")).toBeInTheDocument();
  });

  it("takes passwords away only after the host's name is typed, and sends the one fix", async () => {
    const { user, backend } = page("/security/ssh", { "POST /api/server/security/ssh/fixes/disable-passwords": () => json(202, { job_id: "j8", status: "pending", message: "queued" }) });
    await user.click(await screen.findByRole("switch", { name: "Allow signing in with a password" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Turn off password logins" });
    expect(within(dialog).getByText(/undoes itself in 5 minutes/)).toBeInTheDocument();
    const apply = within(dialog).getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "web-01");
    await user.click(apply);
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/ssh/fixes/disable-passwords")).toHaveLength(1);
    });
  });

  it("says where a way in that is off is turned on again, and does not offer to do it", async () => {
    page("/security/ssh", { "GET /api/server/security/ssh": () => json(200, { ...SSH, passwords_accepted: false, effective: { ...SSH.effective, passwordauthentication: "no" } }) });
    const passwords = await screen.findByRole("switch", { name: "Allow signing in with a password" });
    expect(passwords).not.toBeChecked();
    expect(passwords).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(/Turning it on again is done on the server/)).toBeInTheDocument();
  });

  it("leaves the switches where they are, off limits and saying why, for a role that may not change how the server is reached", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "server.host_access") };
    page("/security/ssh", {}, reader);
    const passwords = await screen.findByRole("switch", { name: "Allow signing in with a password" });
    expect(passwords).toBeChecked();
    expect(passwords).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByText("server.host_access").length).toBeGreaterThan(0);
    // The primary action stays, disabled, with its lock.
    expect(screen.getByRole("button", { name: "Add a key" })).toBeDisabled();
  });

  it("passes axe", async () => {
    page("/security/ssh");
    await screen.findByRole("region", { name: "Authorized SSH keys" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Security > Blocks", () => {
  it("puts what is blocked now first, one row per address and jail", async () => {
    page("/security/blocks", { "GET /api/server/security/fail2ban": () => json(200, BANS) });
    expect(await screen.findByRole("heading", { level: 1, name: "Blocks" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Addresses blocked now" });
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByText("198.51.100.23")).toBeInTheDocument();
    expect(within(table).getAllByText("SSH access")).toHaveLength(2);
    expect(within(table).getByText("Web server")).toBeInTheDocument();
    // What each guard watches, below the list that answers the question people come with.
    expect(screen.getByRole("heading", { level: 2, name: "Blocked now" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "When it blocks" })).toBeInTheDocument();
  });

  it("finds an address by a part of it", async () => {
    const { user } = page("/security/blocks", { "GET /api/server/security/fail2ban": () => json(200, BANS) });
    const table = await screen.findByRole("table", { name: "Addresses blocked now" });
    await user.type(screen.getByRole("searchbox", { name: "Search an address" }), "113.45");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("203.0.113.45")).toBeInTheDocument();
    await user.clear(screen.getByRole("searchbox", { name: "Search an address" }));
    await user.type(screen.getByRole("searchbox", { name: "Search an address" }), "10.9.9.9");
    expect(await screen.findByText("No blocked address matches.")).toBeInTheDocument();
  });

  it("lifts a block with the address and the jail that holds it", async () => {
    const { user, backend } = page("/security/blocks", {
      "GET /api/server/security/fail2ban": () => json(200, BANS),
      "POST /api/server/security/fail2ban/unban": () => json(202, { job_id: "j9", status: "pending", message: "queued" }),
    });
    await user.click(await screen.findByRole("button", { name: "Unblock 198.51.100.23" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/fail2ban/unban")[0]?.body).toEqual({ address: "198.51.100.23", jail: "nginx-botsearch" });
    });
  });

  it("says nobody is blocked, and draws no search, when nobody is", async () => {
    page("/security/blocks", { "GET /api/server/security/fail2ban": () => json(200, { ...BANS, jails: [{ ...BANS.jails[0], banned: [], currently_banned: 0 }] }) });
    expect(await screen.findByText("Nobody is blocked now.")).toBeInTheDocument();
    expect(screen.queryByRole("searchbox", { name: "Search an address" })).not.toBeInTheDocument();
  });

  it("draws no Unblock for a role that cannot", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "server.manage") };
    page("/security/blocks", { "GET /api/server/security/fail2ban": () => json(200, BANS) }, reader);
    await screen.findByRole("table", { name: "Addresses blocked now" });
    expect(screen.queryByRole("button", { name: /^Unblock/ })).not.toBeInTheDocument();
  });

  it("says fail2ban is missing with the one notice every missing piece uses, and installs it from there", async () => {
    const without = ITEMS.map((item) => (item.id === "fail2ban" ? { ...item, installed: false, version: null, origin: null } : item));
    const { user, backend } = page("/security/blocks", {
      ...softwareRoutes({ list: { managed: true, reason: null, family: "apt", items: without } }),
      "POST /api/server/software/fail2ban/install": () => json(202, { job_id: "5e1f0c2a", status: "pending", message: "queued" }),
    });
    expect(await screen.findByText("fail2ban is missing")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See it in Software" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Install" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/fail2ban/install")).toHaveLength(1);
    });
    // No bare install button of the page's own beside the notice's.
    expect(screen.queryByRole("button", { name: "Install fail2ban" })).not.toBeInTheDocument();
  });

  it("asks before it watches the web server as well, and sends the choice", async () => {
    const { user, backend } = page("/security/blocks", {
      "GET /api/server/security/fail2ban": () => json(200, BANS),
      "POST /api/server/security/fail2ban/web": () => json(202, { job_id: "j9", status: "pending", message: "queued" }),
    });
    await user.click(await screen.findByRole("switch", { name: "Watch nginx's logs" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Start fail2ban watching nginx" });
    await user.click(within(dialog).getByRole("button", { name: "Watch nginx" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/fail2ban/web")[0]?.body).toEqual({ enable: true });
    });
  });

  it("passes axe", async () => {
    page("/security/blocks", { "GET /api/server/security/fail2ban": () => json(200, BANS) });
    await screen.findByRole("table", { name: "Addresses blocked now" });
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("Security > Web firewall", () => {
  const WAF = { anchor: true, available: true, hint: "", installable: true, module_installed: true, module_loaded: true, packages: [], problem: "", rules: true, webserver: "nginx" };

  it("says whether the server can run it and the level of each site, with the way to change it", async () => {
    page("/security/waf", {
      "GET /api/apps/shop.example.com/waf": () => json(200, WAF),
      "GET /api/apps/shop.example.com/policy": () => json(200, { policy: { waf: "block", waf_exclusions: [], routes: [] } }),
      "GET /api/apps/shop.example.com/waf/events": () =>
        json(200, { events: [{ blocked: true, would_block: true, client: "198.51.100.9", host: "shop.example.com", method: "GET", uri: "/.env", status: 403, time: new Date().toISOString(), rules: [] }], noisy: [] }),
    });
    expect(await screen.findByRole("heading", { level: 1, name: /Web firewall \(WAF\)/ })).toBeInTheDocument();
    expect(await screen.findByText("Active")).toBeInTheDocument();
    const sites = await screen.findByRole("region", { name: "By site" });
    const row = (await within(sites).findByText("shop.example.com")).closest("tr");
    if (row === null) throw new Error("no row");
    expect(await within(row).findByText("Block")).toBeInTheDocument();
    expect(await within(row).findByText("1")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Change the level of shop.example.com" })).toHaveAttribute("href", expect.stringContaining("/apps/shop.example.com/access#protection"));
  });

  it("offers to turn it on where it is off, and says what stands in the way when the server cannot run it", async () => {
    page("/security/waf", {
      "GET /api/apps/shop.example.com/waf": () => json(200, { ...WAF, available: false, module_installed: false, module_loaded: false, problem: "ModSecurity is not installed", hint: "Install it from Software." }),
      "GET /api/apps/shop.example.com/policy": () => json(200, { policy: { waf: "", waf_exclusions: [], routes: [] } }),
    });
    expect(await screen.findByText("Not available")).toBeInTheDocument();
    const sites = await screen.findByRole("region", { name: "By site" });
    expect(await within(sites).findByRole("link", { name: "Turn it on for shop.example.com" })).toBeInTheDocument();
    expect(await within(sites).findByText("Off")).toBeInTheDocument();
  });
});

describe("Security > Access and sessions", () => {
  it("lists who is connected, and who signed in over SSH with how, newest first", async () => {
    page("/security/access", { "GET /api/terminal/recordings": () => json(200, { recordings: [] }) });
    expect(await screen.findByRole("heading", { level: 1, name: "Access and sessions" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Accesses to this machine, newest first" });
    const rows = within(table).getAllByRole("row").slice(1);
    // The session open now first, then the journal's login.
    expect(rows[0]).toHaveTextContent("Now");
    expect(rows[1]).toHaveTextContent("SSH (publickey)");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("opens a terminal session's recording in a panel, as it was printed", async () => {
    const { user } = page("/security/access", {
      "GET /api/terminal/recordings": () =>
        json(200, { recordings: [{ id: "rec-1", status: "complete", actor: "marta", account: "marta", client_ip: "88.12.4.90", started_at: 1_758_000_000, duration: 61 }] }),
      "GET /api/terminal/recordings/rec-1": () =>
        json(200, { events: [], intact: true, transcript: "$ ls\nreleases shared\n", recording: { id: "rec-1", status: "complete", actor: "marta", started_at: 1_758_000_000, duration: 61 } }),
    });
    await user.click(await screen.findByRole("button", { name: "Watch the recording of marta" }));
    const drawer = await screen.findByRole("dialog", { name: "Recording of a session" });
    expect(await within(drawer).findByText(/releases shared/)).toBeInTheDocument();
  });

  it("says the terminal's recordings take audit.read, and lists what it can read, for a role without it", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "audit.read") };
    page("/security/access", {}, reader);
    expect(await screen.findByText(/recordings take the audit\.read permission/)).toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Accesses to this machine, newest first" })).toBeInTheDocument();
  });

  it("names a recordings' refusal, and still lists SSH", async () => {
    page("/security/access", { "GET /api/terminal/recordings": () => problem(500, "internal_error", "The recordings could not be read") });
    expect(await screen.findByRole("table", { name: "Accesses to this machine, newest first" })).toBeInTheDocument();
    expect(await screen.findByText("Could not read the terminal's recordings")).toBeInTheDocument();
  });
});

describe("Security from All servers", () => {
  it("is the list of servers, each with its own checks, blocks and password sign-in, and a way to open it", async () => {
    // A central with its servers: the list is the central's own machine and each node it manages.
    fakeBackend({
      ...serverRoutes(),
      ...fleetRoutes(),
      "GET /api/server/security/fail2ban": () => json(200, BANS),
    });
    renderConsole("/fleet/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Security of each server" });
    const central = within(table).getByText("web-01").closest("tr");
    if (central === null) throw new Error("no row");
    expect(await within(central).findByText("3 notices")).toBeInTheDocument();
    expect(await within(central).findByText("3")).toBeInTheDocument();
    expect(within(central).getByRole("link", { name: "Open Security on web-01" })).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "Open Security on web-2" })).toBeInTheDocument();
  });
});
