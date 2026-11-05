import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, onNode, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { fleetRoutes, WEB2, DB1 } from "../fleet/testFixtures";
import { FIREWALL, PORT_CLAIMS, onDesktop, serverRoutes } from "../server/testing";
import type { Firewall, PortClaim } from "../server/queries";

beforeEach(onDesktop);

function network(path: string, extra: Record<string, RouteHandler> = {}, session = SESSION) {
  const backend = fakeBackend({ ...signedInRoutes(session), ...serverRoutes(), ...extra });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

const REDIS: PortClaim = { port: 6379, protocol: "tcp", kind: "database", owner: "redis@main", address: "127.0.0.1", detail: "", processes: ["redis-server"] };
const MINECRAFT: PortClaim = { port: 25565, protocol: "tcp", kind: "process", owner: "java", address: "0.0.0.0", detail: "", processes: ["java"] };

function firewallWith(ports: Firewall["ports"], over: Partial<Firewall["firewall"]> = {}): Firewall {
  return { ...FIREWALL, firewall: { ...FIREWALL.firewall, ...over }, ports: [...FIREWALL.ports, ...ports] };
}

/** The names of the columns of a table: the one of its row actions is named Actions. */
const headers = (table: HTMLElement): string[] => within(table).getAllByRole("columnheader").map((header) => header.textContent).filter((text) => text !== "Actions");

/** The row of a table, or a failure that says which one the page lacks. */
function at(rows: readonly HTMLElement[], index: number): HTMLElement {
  const row = rows[index];
  if (row === undefined) throw new Error(`no row ${String(index)}`);
  return row;
}

const queued = () => json(202, { job_id: "j1", message: "queued", status: "pending" });
const READ_ONLY = { ...SESSION, role: "viewer", permissions: ["self", "server.read", "apps.read"] };

describe("Listening ports, the one table of who listens", () => {
  it("has one row for each port: the program the kernel names, what it belongs to in Noust and who can reach it", async () => {
    network("/network/listening");
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });
    const rows = within(table).getAllByRole("row").slice(1);
    const text = rows.map((row) => row.textContent);

    expect(headers(table)).toEqual(["Port", "Program", "Belongs to", "From outside"]);
    expect(text.find((row) => row.startsWith("22TCP"))).toContain("SSH");
    expect(text.find((row) => row.startsWith("443TCP"))).toContain("The web server");
    // The kernel's own report of an application's port is the same port, said once, as the application.
    const app = text.find((row) => row.startsWith("3000TCP"));
    expect(app).toContain("Application");
    expect(app).toContain("shop.example.net");
    expect(app).not.toContain("Process");
    expect(within(table).getByRole("link", { name: "shop.example.net" })).toHaveAttribute("href", "/apps/shop.example.net");
    // A port nothing in Noust accounts for says which program holds it, and that Docker publishes it around the firewall.
    const database = text.find((row) => row.startsWith("5432TCP"));
    expect(database).toContain("docker-proxy");
    expect(database).toContain("Around the firewall");
  });

  it("lists a port only the kernel reports as one more row, with its program and nothing of Noust's holding it", async () => {
    const wireguard: Firewall["ports"][number] = { proto: "udp", port: 51820, address: "0.0.0.0", process: "wg-quick", verdict: "open", sources: [], risky: "", baseline: false, reachable: true };
    network("/network/listening", { "GET /api/server/security/firewall": () => json(200, firewallWith([wireguard])) });
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });

    await waitFor(() => {
      expect(within(table).getAllByRole("row").some((row) => row.textContent.startsWith("51820UDP"))).toBe(true);
    });
    const row = within(table).getAllByRole("row").find((entry) => entry.textContent.startsWith("51820UDP"));
    expect(row?.textContent).toContain("wg-quick");
    expect(row?.textContent).toContain("Not one of Noust's");
  });

  it("answers who uses a port when its number is typed, and says it is free when nothing does", async () => {
    const { user } = network("/network/listening");
    await user.type(await screen.findByRole("searchbox", { name: "Search ports" }), "5432");
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });
    await waitFor(() => {
      expect(within(table).getAllByRole("row")).toHaveLength(2);
    });

    await user.clear(screen.getByRole("searchbox", { name: "Search ports" }));
    await user.type(screen.getByRole("searchbox", { name: "Search ports" }), "25565");
    expect(await screen.findByText("Nothing on this server uses port 25565.")).toBeInTheDocument();
  });

  it("opens the dialog that opens a port from a free port, with its number in, and says what it will do before it does it", async () => {
    const { user, backend } = network("/network/listening?q=25565", { "POST /api/server/security/firewall/rules": queued });
    await user.click(await screen.findByRole("button", { name: "Open port 25565" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });
    expect(within(dialog).getByLabelText("Port")).toHaveValue("25565");
    expect(await within(dialog).findByText(/Nothing listens on 25565 now/)).toBeInTheDocument();
    expect(within(dialog).getByText("Server firewall: allow TCP 25565 from anyone.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Open 25565" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/security/firewall/rules")[0]?.body).toEqual({ action: "allow", port: 25565, proto: "tcp", source: "any", comment: "" });
  });

  it("offers to open to the outside a port the firewall keeps shut, which is the same dialog, filled in", async () => {
    const { user } = network("/network/listening", {
      "GET /api/server/ports": () => json(200, { claims: [...PORT_CLAIMS, MINECRAFT] }),
      "GET /api/server/security/firewall": () => json(200, firewallWith([{ proto: "tcp", port: 25565, address: "0.0.0.0", process: "java", verdict: "blocked", sources: [], risky: "", baseline: false, reachable: false }])),
    });
    const table = await screen.findByRole("table", { name: "Ports and what uses them" });
    const row = within(table).getAllByRole("row").find((entry) => entry.textContent.startsWith("25565TCP"));
    if (row === undefined) throw new Error("no row for 25565");

    await user.click(within(row).getByRole("button", { name: "Open to the outside…" }));

    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });
    expect(within(dialog).getByLabelText("Port")).toHaveValue("25565");
    expect(await within(dialog).findByText("java")).toBeInTheDocument();
    expect(within(dialog).getByText("Server firewall: allow TCP 25565 from anyone.")).toBeInTheDocument();
  });

  it("draws no way to open a port for a role that cannot, and says once that the page can only be read", async () => {
    network(
      "/network/listening",
      {
        "GET /api/server/ports": () => json(200, { claims: [...PORT_CLAIMS, MINECRAFT] }),
        "GET /api/server/security/firewall": () => json(200, firewallWith([{ proto: "tcp", port: 25565, address: "0.0.0.0", process: "java", verdict: "blocked", sources: [], risky: "", baseline: false, reachable: false }])),
      },
      READ_ONLY,
    );
    await screen.findByRole("table", { name: "Ports and what uses them" });

    expect(screen.queryByRole("button", { name: "Open to the outside…" })).toBeNull();
    expect(screen.getByText(/can see this page and not change it/)).toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    network("/network/listening");
    await screen.findByRole("table", { name: "Ports and what uses them" });

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the one dialog that opens a port", () => {
  it("reads a program that listens only inside as something a rule cannot reach, and has nginx answer on another port", async () => {
    const { user, backend } = network("/network", {
      "GET /api/server/ports": () => json(200, { claims: [...PORT_CLAIMS, REDIS] }),
      "GET /api/server/security/firewall": () => json(200, firewallWith([{ proto: "tcp", port: 6379, address: "127.0.0.1", process: "redis-server", verdict: "local", sources: [], risky: "", baseline: false, reachable: false }])),
      "GET /api/streams": () => json(200, { exposures: [], support: { available: true, module: "static", loader: "present", install: null, coded: null, hint: null, problem: null } }),
      "POST /api/streams/plan": () =>
        json(200, { config: "", exposure: {}, file: "/etc/nginx/noust-stream/redis-server-16379.conf", firewall: [{ proto: "tcp", port: 16379, source: "any" }], loader: "present", support: {}, warnings: [], warnings_coded: [] }),
      "POST /api/streams": (call) =>
        json(201, { exposure: { ...(call.body as object), name: "redis-server-16379", app_domain: null, state: "open", target: "127.0.0.1:6379" }, firewall: "Allowed.", firewall_coded: null, warnings: [], warnings_coded: [] }),
    });
    await user.click(await screen.findByRole("button", { name: "Open a port" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });
    const port = within(dialog).getByLabelText("Port");
    await user.clear(port);
    await user.type(port, "6379");

    expect(await within(dialog).findByText(/listens only inside this machine, where no rule/)).toBeInTheDocument();
    // What listens is said as soon as the port is typed, before the port nginx answers on is: that is the news that changes the form.
    expect(within(dialog).getByText(/listens only inside this machine \(127\.0\.0\.1\)/)).toBeInTheDocument();
    expect(within(dialog).getByText("redis-server")).toBeInTheDocument();
    // The action waits for the port nginx answers on.
    expect(within(dialog).getByRole("button", { name: "Open 6379" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Port to answer on"), "16379");
    await waitFor(() => {
      expect(within(dialog).getByRole("button", { name: "Open 6379" })).toBeEnabled();
    });
    expect(within(dialog).getByText(/nginx receives TCP 16379 and passes it to/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Open 6379" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/streams")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/streams")[0]?.body).toMatchObject({ listen_port: 16379, target_port: 6379, target_host: "127.0.0.1", protocol: "tcp", sources: [] });
    expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(0);
  });

  it("restricts the rule to one address or network when asked, and refuses a typo before asking the server", async () => {
    const { user, backend } = network("/network/listening?q=25565", { "POST /api/server/security/firewall/rules": queued });
    await user.click(await screen.findByRole("button", { name: "Open port 25565" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });

    await user.click(within(dialog).getByRole("radio", { name: "Only these addresses" }));
    expect(within(dialog).getByRole("button", { name: "Open 25565" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Address or network"), "10.0.0");
    expect(await within(dialog).findByText("That is not an address or a network.")).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText("Address or network"));
    await user.type(within(dialog).getByLabelText("Address or network"), "10.0.0.0/24");
    await user.click(within(dialog).getByRole("radio", { name: "Both" }));
    await user.click(within(dialog).getByRole("button", { name: "Open 25565" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/security/firewall/rules")[0]?.body).toMatchObject({ port: 25565, proto: "any", source: "10.0.0.0/24" });
  });

  it("does not offer a rule while the firewall is off, and leads to where it is turned on", async () => {
    const { user } = network("/network/listening?q=25565", {
      "GET /api/server/security/firewall": () => json(200, firewallWith([], { active: false, status: "inactive" })),
    });
    await user.click(await screen.findByRole("button", { name: "Open port 25565" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });

    expect(await within(dialog).findByText("The server firewall is off")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Go to the firewall" })).toHaveAttribute("href", "/network/firewall");
    expect(within(dialog).getByRole("button", { name: "Open 25565" })).toBeDisabled();
  });

  it("does not open a database here: it leads to its Connect section, which asks for the account and the TLS", async () => {
    const { user, backend, location } = network("/network/listening?q=25565", {
      "GET /api/databases/databases": () => json(200, { total: 1, problems: [], databases: [{ engine: "postgresql", name: "tienda_prod", app: null, apps: [], detected_apps: [], tags: [], tracked: true, unverified: false, missing: false, noust_state: "ok", actions: [] }] }),
    });
    await user.click(await screen.findByRole("button", { name: "Open port 25565" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Open a port" });

    await user.click(within(dialog).getByRole("radio", { name: /A database/ }));
    expect(within(dialog).getByRole("button", { name: "Go to Connect" })).toBeDisabled();
    await user.click(within(dialog).getByRole("combobox", { name: "Database" }));
    await user.click(await screen.findByRole("option", { name: /tienda_prod/ }));
    await user.click(within(dialog).getByRole("button", { name: "Go to Connect" }));

    await waitFor(() => {
      expect(location().pathname).toBe("/databases/postgresql/tienda_prod/connect");
    });
    expect(backend.callsTo("POST /api/server/security/firewall/rules")).toHaveLength(0);
  });
});

describe("Published ports", () => {
  const STREAM = { name: "minecraft", protocol: "tcp", listen_port: 25565, target_host: "127.0.0.1", target_port: 25566, target: "127.0.0.1:25566", sources: [], state: "open", app_domain: null, created_at: "2026-10-01T10:00:00Z", created_by: "admin", timeout_seconds: 3600, firewall: { active: true, backend: "ufw", made: [], note: "" } };
  const SUPPORT = { available: true, module: "static", loader: "present", install: null, coded: null, hint: null, problem: null };

  it("lists what is published with where it goes, who may come in and what it is for", async () => {
    network("/network", {
      "GET /api/streams": () => json(200, { exposures: [STREAM, { ...STREAM, name: "git", listen_port: 2222, target: "gitea:22", sources: ["10.0.0.0/24"], app_domain: "git.example.com" }], support: SUPPORT }),
    });
    const table = await screen.findByRole("table", { name: "Published TCP and UDP ports" });

    expect(headers(table)).toEqual(["Port", "Goes to", "Who can reach it", "Used for", "State"]);
    expect(await within(table).findByText("minecraft")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "git.example.com" })).toHaveAttribute("href", "/apps/git.example.com");
    expect(within(table).getByText("10.0.0.0/24")).toBeInTheDocument();
  });

  it("says what a published port gives when there is none, without a command to type", async () => {
    network("/network", { "GET /api/streams": () => json(200, { exposures: [], support: SUPPORT }) });

    expect(await screen.findByText(/Open a port and a game server/)).toBeInTheDocument();
    expect(screen.queryByText(/noust stream/)).toBeNull();
    expect(screen.getByRole("button", { name: "Open a port" })).toBeEnabled();
  });

  it("opens the one dialog from its header", async () => {
    const { user } = network("/network", { "GET /api/streams": () => json(200, { exposures: [], support: SUPPORT }) });
    await user.click(await screen.findByRole("button", { name: "Open a port" }));

    expect(await screen.findByRole("alertdialog", { name: "Open a port" })).toBeInTheDocument();
  });

  it("opens the dialog when the link asks for it, and spends the link", async () => {
    const { location } = network("/network?new=true", { "GET /api/streams": () => json(200, { exposures: [], support: SUPPORT }) });

    expect(await screen.findByRole("alertdialog", { name: "Open a port" })).toBeInTheDocument();
    await waitFor(() => {
      expect(location().search).toEqual({});
    });
  });

  it("keeps the primary action, locked and with its reason, for a role that cannot open a port", async () => {
    network("/network", { "GET /api/streams": () => json(200, { exposures: [STREAM], support: SUPPORT }) }, READ_ONLY);
    const table = await screen.findByRole("table", { name: "Published TCP and UDP ports" });

    expect(screen.getByRole("button", { name: "Open a port" })).toBeDisabled();
    expect(within(table).queryByRole("button", { name: /Actions for/ })).toBeNull();
    expect(screen.getByText(/can see this page and not change it/)).toBeInTheDocument();
  });

  it("closes a port after asking, and says the rules that were already there stay", async () => {
    const { user, backend } = network("/network", {
      "GET /api/streams": () => json(200, { exposures: [STREAM], support: SUPPORT }),
      "DELETE /api/streams/minecraft": () => json(200, {}),
    });
    await user.click(await screen.findByRole("button", { name: "Actions for minecraft" }));
    await user.click(await screen.findByRole("menuitem", { name: "Close the port" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Close minecraft" });
    expect(within(dialog).getByText(/A rule that was already there stays/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close the port" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/streams/minecraft")).toHaveLength(1);
    });
  });

  it("has no accessibility violations", async () => {
    network("/network", { "GET /api/streams": () => json(200, { exposures: [STREAM], support: SUPPORT }) });
    await screen.findByRole("table", { name: "Published TCP and UDP ports" });

    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the server's firewall, named apart from the other two", () => {
  const RULES: Firewall["firewall"]["rules"] = [
    { id: "1", backend: "ufw", action: "allow", ports: [[22, 22]], proto: "tcp", source: "any", spec: "22/tcp ALLOW IN Anywhere", comment: "noust: ssh", noust: true, known: true, service: "OpenSSH" },
    { id: "2", backend: "ufw", action: "allow", ports: [[5432, 5432]], proto: "tcp", source: "10.0.0.0/24", spec: "5432/tcp ALLOW IN 10.0.0.0/24", comment: "noust: db postgresql tienda_prod 5432", noust: true, known: true, service: null },
    { id: "3", backend: "ufw", action: "allow", ports: [[2222, 2222]], proto: "tcp", source: "any", spec: "2222/tcp ALLOW IN Anywhere", comment: "noust: noust stream git", noust: true, known: true, service: null },
    { id: "4", backend: "ufw", action: "allow", ports: [[9000, 9000]], proto: "tcp", source: "any", spec: "9000/tcp ALLOW IN Anywhere", comment: "office vpn", noust: false, known: true, service: null },
  ];

  it("says what each rule is for and who made it, with a link to where it is changed", async () => {
    network("/network/firewall", { "GET /api/server/security/firewall": () => json(200, { ...FIREWALL, firewall: { ...FIREWALL.firewall, rules: RULES } }) });
    const table = await screen.findByRole("table", { name: "Firewall rules" });
    const rows = within(table).getAllByRole("row").slice(1);

    expect(headers(table)).toEqual(["Used for", "Port", "From", "Made by"]);
    expect(rows[0]?.textContent).toContain("OpenSSH");
    expect(rows[0]?.textContent).toContain("Noust: it is never closed");
    expect(within(at(rows, 1)).getByRole("link", { name: "Connect of tienda_prod" })).toHaveAttribute("href", "/databases/postgresql/tienda_prod/connect");
    expect(rows[1]?.textContent).toContain("10.0.0.0/24");
    expect(within(at(rows, 2)).getByRole("link", { name: "Published ports" })).toHaveAttribute("href", "/network");
    expect(rows[3]?.textContent).toContain("office vpn");
  });

  it("names the three firewalls apart: this machine's, the web's and the provider's", async () => {
    network("/network/firewall");

    expect(await screen.findByRole("heading", { level: 1, name: "Server firewall" })).toBeInTheDocument();
    expect(await screen.findByText("ufw. Everything that comes in is refused except what is on this list.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "It is not the only firewall" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Web (WAF)" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Provider's" })).toBeInTheDocument();
    expect(screen.getByText(/Noust does not see it/)).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Security" })).toHaveAttribute("href", "/security");
  });

  it("draws no table of the listening ports, which are one table elsewhere, and says what Docker published around it", async () => {
    network("/network/firewall");

    expect(await screen.findByText("Docker publishes 1 port around the firewall")).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: "Ports that listen" })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).parentElement?.textContent).toContain("Active");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("opens the same dialog from its header, and closes a port after asking", async () => {
    const { user, backend } = network("/network/firewall", {
      "GET /api/server/security/firewall": () => json(200, { ...FIREWALL, firewall: { ...FIREWALL.firewall, rules: RULES } }),
      "DELETE /api/server/security/firewall/rules/4": queued,
    });
    await user.click(await screen.findByRole("button", { name: "Open a port" }));
    expect(await screen.findByRole("alertdialog", { name: "Open a port" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Actions for 9000/tcp" }));
    await user.click(await screen.findByRole("menuitem", { name: "Close the port" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete the rule for 9000/tcp?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete rule" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/server/security/firewall/rules/4")).toHaveLength(1);
    });
  });

  it("does not offer to close a port Noust keeps open", async () => {
    network("/network/firewall", { "GET /api/server/security/firewall": () => json(200, { ...FIREWALL, firewall: { ...FIREWALL.firewall, rules: RULES } }) });
    await screen.findByRole("table", { name: "Firewall rules" });

    expect(screen.queryByRole("button", { name: "Actions for 22/tcp" })).toBeNull();
    expect(screen.getByRole("button", { name: "Actions for 9000/tcp" })).toBeInTheDocument();
  });

  it("makes turning the firewall on the primary action while it is off, and says it is off", async () => {
    network("/network/firewall", { "GET /api/server/security/firewall": () => json(200, firewallWith([], { active: false, status: "inactive", rules: [] })) });

    expect(await screen.findByRole("button", { name: "Turn on the firewall" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open a port" })).toBeNull();
    expect(screen.getByText("Turned off")).toBeInTheDocument();
  });
});

describe("Tunnels", () => {
  const SUPPORT = { available: true, cloudflared: "2026.9.0", accounts: ["main"], sealed: false, coded: null, hint: null, problem: null };
  const TUNNEL = { name: "oficina", provider: "main", state: "ready", active: "active", target: "web", unit: "noust-tunnel-oficina.service", account_id: "a", tunnel_id: "t", created_at: "2026-10-01T10:00:00Z", created_by: "admin", routes: [{ hostname: "panel.example.net", service: "http://localhost:80", tunnel: "oficina", app_domain: null, created_at: "2026-10-01T10:00:00Z" }] };

  it("shows each tunnel as a card with its state and what it carries", async () => {
    network("/network/tunnels", { "GET /api/tunnels": () => json(200, { tunnels: [TUNNEL], support: SUPPORT }) });
    const card = await screen.findByRole("article", { name: "oficina" });

    expect(within(card).getByText("panel.example.net")).toBeInTheDocument();
    expect(within(card).getByText("active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tunnel" })).toBeEnabled();
  });

  it("says what it needs first, beside the action and not as a form that fails", async () => {
    network("/network/tunnels", { "GET /api/tunnels": () => json(200, { tunnels: [], support: { ...SUPPORT, available: false, cloudflared: null, problem: "cloudflared is missing", hint: null } }) });

    expect(await screen.findByText("cloudflared is missing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tunnel" })).toBeDisabled();
    expect(screen.queryByText(/noust tunnel/)).toBeNull();
  });
});

/** One published port, as the server lists it. */
const published = (name: string) => ({ name, protocol: "tcp", listen_port: 25565, target: "127.0.0.1:25565", target_host: "127.0.0.1", target_port: 25565, sources: [], state: "open", app_domain: null });

describe("Network across the servers", () => {
  it("lists the servers with what each one's network is, and opening one is choosing it", async () => {
    fakeBackend({
      ...fleetRoutes(),
      ...serverRoutes(),
      "GET /api/streams": () => json(200, { exposures: ["minecraft", "git", "voice"].map(published), support: { available: true } }),
      "GET /api/tunnels": () => json(200, { tunnels: [{ name: "oficina" }, { name: "nas" }], support: { available: true } }),
      ...onNode("web-2", {
        "GET /api/streams": () => json(200, { exposures: [published("game")], support: { available: true } }),
        "GET /api/tunnels": () => json(200, { tunnels: [], support: { available: true } }),
        "GET /api/server/security/firewall": () => json(200, { ...FIREWALL, firewall: { ...FIREWALL.firewall, active: false, installed: false, backend: "none" } }),
      }),
    });
    const { user, location } = renderConsole("/fleet/network");
    const table = await screen.findByRole("table", { name: "Server" });
    const rowOf = (name: string): HTMLElement => {
      const row = within(table).getAllByRole("row").find((candidate) => candidate.textContent.startsWith(name));
      if (row === undefined) throw new Error(`no row for ${name}`);
      return row;
    };

    expect(headers(table)).toEqual(["Server", "State", "Published ports", "Tunnels", "Firewall"]);
    // This central is a server of the list, with its own counts and its firewall on.
    await waitFor(() => {
      expect(rowOf("web-01").textContent).toContain("3");
    });
    expect(rowOf("web-01").textContent).toContain("2");
    expect(rowOf("web-01").textContent).toContain("Active");
    // A node is read through the central's proxy, and a firewall it does not have is said.
    await waitFor(() => {
      expect(rowOf("web-2").textContent).toContain("No firewall");
    });
    // One that does not answer is not asked, and says nothing in its cells but its state.
    expect(rowOf(DB1.name).textContent).toContain("Unreachable");
    expect(rowOf(WEB2.name)).toBeDefined();

    await user.click(within(rowOf("web-2")).getByRole("link", { name: "Open Network on web-2" }));
    await waitFor(() => {
      expect(location().search).toMatchObject({ node: "web-2" });
    });
    expect(location().pathname).toBe("/network");
  });
});
