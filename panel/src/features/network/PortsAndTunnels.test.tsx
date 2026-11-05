import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { ITEMS, softwareJob } from "../server/software/testFixtures";
import { FIREWALL, PORT_CLAIMS } from "../server/testing";
import type { Firewall, PortClaim } from "../server/queries";

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

/** The first of several matches: the header's primary action, which an empty state repeats. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

const STREAM_SUPPORT = { available: true, module: "dynamic", loader: "installable", problem: null, hint: null };

const MINECRAFT = {
  name: "minecraft",
  protocol: "tcp",
  listen_port: 25565,
  target: "127.0.0.1:25565",
  target_host: "127.0.0.1",
  target_port: 25565,
  sources: ["203.0.113.0/24"],
  timeout_seconds: 3600,
  app_domain: null,
  firewall: { backend: "ufw", active: true, made: [{ port: 25565, proto: "tcp", source: "203.0.113.0/24" }], note: "" },
  state: "open",
  created_at: "2026-10-12T10:00:00+00:00",
  created_by: "ana",
};

/** What the registry says of the connector on a server that does not have it. */
const CLOUDFLARED_MISSING = { ...(ITEMS.find((item) => item.id === "cloudflared") ?? {}), id: "cloudflared", installed: false, version: null, origin: null, dependents: [], packages: [] };

const TUNNEL_SUPPORT = { available: true, cloudflared: "/usr/bin/cloudflared", sealed: false, accounts: ["cf"], problem: null, hint: null };

const HOME = {
  name: "home",
  provider: "cf",
  account_id: "acc-1",
  tunnel_id: "tun-1",
  unit: "noust-tunnel-home",
  target: "tun-1.cfargotunnel.com",
  state: "ready",
  active: "active",
  routes: [{ tunnel: "home", hostname: "shop.example.com", service: "http://localhost:80", app_domain: "shop.example.com", created_at: "2026-10-12T10:00:00+00:00" }],
  created_at: "2026-10-12T10:00:00+00:00",
  created_by: "ana",
};

/** A program that listens only inside the machine: what nginx has to carry for the outside to reach it. */
const REDIS: PortClaim = { port: 6379, protocol: "tcp", kind: "database", owner: "redis@main", address: "127.0.0.1", detail: "", processes: ["redis-server"] };
const INSIDE: Firewall = { ...FIREWALL, ports: [...FIREWALL.ports, { proto: "tcp", port: 6379, address: "127.0.0.1", process: "redis-server", verdict: "local", sources: [], risky: "", baseline: false, reachable: false }] };

function network(path: string, routes: Record<string, RouteHandler>) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/dns/providers": () => json(200, { accounts: [], kinds: [] }),
    "GET /api/tunnels": () => json(200, { tunnels: [], support: TUNNEL_SUPPORT }),
    "GET /api/streams": () => json(200, { exposures: [], support: STREAM_SUPPORT }),
    "GET /api/server/security/changes": () => json(200, []),
    "GET /api/server/security/firewall": () => json(200, INSIDE),
    "GET /api/server/ports": () => json(200, { claims: [...PORT_CLAIMS, REDIS] }),
    ...routes,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

/** Opens the dialog that opens a port from the header, and asks for the port nginx must carry (Redis, which listens inside). */
async function openRedis(user: ReturnType<typeof renderConsole>["user"], labels: { open: string; dialog: string; port: string; outside: string }) {
  await user.click(first(await screen.findAllByRole("button", { name: labels.open })));
  const dialog = await screen.findByRole("alertdialog", { name: labels.dialog });
  const port = within(dialog).getByLabelText(labels.port);
  await user.clear(port);
  await user.type(port, "6379");
  await user.type(await within(dialog).findByLabelText(labels.outside), "16379");
  return dialog;
}

describe("the published ports, and the dialog that opens a port through nginx", () => {
  it("lists what is published, from where, and the state of each", { timeout: 20_000 }, async () => {
    network("/network", { "GET /api/streams": () => json(200, { exposures: [MINECRAFT], support: STREAM_SUPPORT }) });

    const table = await screen.findByRole("region", { name: "Published TCP and UDP ports" });
    const row = (await within(table).findByText("minecraft")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("25565")).toBeInTheDocument();
    expect(within(row).getByText("127.0.0.1:25565")).toBeInTheDocument();
    expect(within(row).getByText("203.0.113.0/24")).toBeInTheDocument();
    expect(within(row).getByText("Published")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("shows what is missing, in the server's words, and offers no way to open a port until it is there", async () => {
    network("/network", {
      "GET /api/streams": () =>
        json(200, {
          exposures: [],
          support: { available: false, module: "missing", loader: "manual", problem: "This nginx was built without the stream module", hint: "apt install libnginx-mod-stream" },
        }),
    });

    expect(await screen.findByText("This nginx was built without the stream module")).toBeInTheDocument();
    expect(screen.getByText("apt install libnginx-mod-stream")).toBeInTheDocument();
  });

  it("asks what carrying the port would do, shows it, and only then does it", { timeout: 20_000 }, async () => {
    const plan = {
      exposure: { ...MINECRAFT, state: "opening" },
      config: "server {}",
      file: "/etc/nginx/noust-stream/redis-server-16379.conf",
      loader: "write",
      support: STREAM_SUPPORT,
      warnings: ["Nothing listens on 127.0.0.1:6379 yet"],
      firewall: [{ port: 16379, proto: "tcp", source: "any" }],
    };
    const { user, backend } = network("/network", {
      "POST /api/streams/plan": () => json(200, plan),
      "POST /api/streams": () => json(201, { exposure: MINECRAFT, warnings: [], firewall: "ufw now allows 1 rule(s) for it" }),
    });

    const dialog = await openRedis(user, { open: "Open a port", dialog: "Open a port", port: "Port", outside: "Port to answer on" });
    expect(await within(dialog).findByText("Nothing listens on 127.0.0.1:6379 yet")).toBeInTheDocument();
    expect(within(dialog).getByText("The block that makes nginx read these files is added as well.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/streams")).toHaveLength(0);
    expect(backend.callsTo("POST /api/streams/plan")[0]?.body).toEqual({
      name: "redis-server-16379",
      protocol: "tcp",
      listen_port: 16379,
      target_port: 6379,
      target_host: "127.0.0.1",
      sources: [],
      timeout_seconds: 3600,
    });

    await user.click(within(dialog).getByRole("button", { name: "Open 6379" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/streams")).toHaveLength(1);
    });
  });

  it("changing a field asks nginx again instead of doing what was not asked", async () => {
    const { user, backend } = network("/network", {
      "POST /api/streams/plan": () => json(200, { exposure: MINECRAFT, config: "", file: "/x.conf", loader: "present", support: STREAM_SUPPORT, warnings: [], firewall: [] }),
    });

    const dialog = await openRedis(user, { open: "Open a port", dialog: "Open a port", port: "Port", outside: "Port to answer on" });
    await waitFor(() => {
      expect(within(dialog).getByRole("button", { name: "Open 6379" })).toBeEnabled();
    });
    const asked = backend.callsTo("POST /api/streams/plan").length;

    await user.type(within(dialog).getByLabelText("Port to answer on"), "{Backspace}");

    await waitFor(() => {
      expect(backend.callsTo("POST /api/streams/plan").length).toBeGreaterThan(asked);
    });
    expect(backend.callsTo("POST /api/streams")).toHaveLength(0);
  });

  it("shows a port that cannot be had in the server's words, and does not offer to open it", async () => {
    const { user } = network("/network", {
      "POST /api/streams/plan": () => problem(400, "validation_error", "Port 16379 is the web server's", { fields: { listen_port: "Port 16379 is the web server's" } }),
    });

    const dialog = await openRedis(user, { open: "Open a port", dialog: "Open a port", port: "Port", outside: "Port to answer on" });

    expect(await within(dialog).findByText(/Port 16379 is the web server's/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Open 6379" })).toBeDisabled();
  });

  it("shows nginx's own words when it refuses the configuration", async () => {
    const { user } = network("/network", {
      "POST /api/streams/plan": () => json(200, { exposure: MINECRAFT, config: "", file: "/x.conf", loader: "present", support: STREAM_SUPPORT, warnings: [], firewall: [] }),
      "POST /api/streams": () =>
        problem(400, "validation_error", "nginx refused the configuration for this port, so nothing was changed", {
          output: 'nginx: [emerg] "listen" directive is not allowed here in minecraft.conf:4',
        }),
    });

    const dialog = await openRedis(user, { open: "Open a port", dialog: "Open a port", port: "Port", outside: "Port to answer on" });
    await waitFor(() => {
      expect(within(dialog).getByRole("button", { name: "Open 6379" })).toBeEnabled();
    });
    await user.click(within(dialog).getByRole("button", { name: "Open 6379" }));

    expect(await within(dialog).findByText(/directive is not allowed here in minecraft\.conf:4/)).toBeInTheDocument();
  });
});

describe("the published ports in Spanish", () => {
  it("says what nginx lacks in Spanish, and its install command as it is", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    network("/network", {
      "GET /api/streams": () =>
        json(200, {
          exposures: [],
          support: {
            available: false,
            module: "missing",
            loader: "manual",
            problem: "This nginx was built without the stream module",
            hint: "Install the module with: apt install libnginx-mod-stream",
            coded: { code: "streams.support.no_stream_module", params: { command: "apt install libnginx-mod-stream" } },
          },
        }),
    });

    expect(await screen.findByText("Este nginx se compiló sin el módulo stream")).toBeInTheDocument();
    expect(screen.getByText("apt install libnginx-mod-stream")).toBeInTheDocument();
    expect(screen.getByText(/Instala el módulo con:/)).toBeInTheDocument();
    // No English of the server's is left on the page.
    expect(screen.queryByText(/built without the stream module/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Install the module with/)).not.toBeInTheDocument();
  });

  it("says nginx's own output as it came when that is the fix", async () => {
    await act(() => setLocale("es"));
    network("/network", {
      "GET /api/streams": () =>
        json(200, {
          exposures: [],
          support: {
            available: false,
            module: "dynamic",
            loader: "manual",
            problem: "nginx cannot read its own configuration",
            hint: "nginx: [emerg] unexpected end of file",
            coded: { code: "streams.support.config_unreadable", params: {} },
          },
        }),
    });

    expect(await screen.findByText("nginx no puede leer su propia configuración")).toBeInTheDocument();
    expect(screen.getByText("nginx: [emerg] unexpected end of file")).toBeInTheDocument();
  });

  it("says what the check warns of, and what the firewall came to, in Spanish", { timeout: 20_000 }, async () => {
    await act(() => setLocale("es"));
    const plan = {
      exposure: { ...MINECRAFT, state: "opening" },
      config: "server {}",
      file: "/etc/nginx/noust-stream/redis-server-16379.conf",
      loader: "present",
      support: STREAM_SUPPORT,
      warnings: ["Nothing listens on 127.0.0.1:6379 yet: visitors will be refused until the service is running"],
      warnings_coded: [{ code: "streams.plan.nothing_listens", params: { target: "127.0.0.1:6379" } }],
      firewall: [],
    };
    const { user } = network("/network", {
      "POST /api/streams/plan": () => json(200, plan),
      "POST /api/streams": () =>
        json(201, {
          exposure: MINECRAFT,
          warnings: [],
          firewall: "ufw now allows 1 rule for it",
          firewall_coded: { code: "streams.firewall.allowed_one", params: { backend: "ufw" } },
        }),
    });

    const dialog = await openRedis(user, { open: "Abrir un puerto", dialog: "Abrir un puerto", port: "Puerto", outside: "Puerto desde fuera" });

    expect(await within(dialog).findByText(/Todavía no escucha nada en 127\.0\.0\.1:6379/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Nothing listens on/)).not.toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Abrir el 6379" }));
    expect(await screen.findByText("ufw ahora permite 1 regla para este puerto")).toBeInTheDocument();
  });
});

describe("Tunnels", () => {
  it("shows each tunnel as a card with its connector and what it carries", { timeout: 20_000 }, async () => {
    network("/network/tunnels", { "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }) });

    const card = await screen.findByRole("article", { name: "home" });
    expect(within(card).getByText("shop.example.com")).toBeInTheDocument();
    expect(within(card).getByText("active")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says what a tunnel gives when there is none, and has no command to type", async () => {
    network("/network/tunnels", {});

    expect(await screen.findByText(/A tunnel reaches an application without a public address/)).toBeInTheDocument();
    expect(screen.queryByText(/noust tunnel/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New tunnel" })).toBeEnabled();
  });

  it("says the connector is missing the way every missing piece is said, with the install beside it, and installs it as a task", { timeout: 20_000 }, async () => {
    const installs: unknown[] = [];
    const { user } = network("/network/tunnels", {
      "GET /api/tunnels": () =>
        json(200, {
          tunnels: [],
          support: {
            available: false,
            cloudflared: null,
            sealed: false,
            accounts: ["cf"],
            problem: "cloudflared is not installed",
            hint: "English hint from the server",
            coded: { code: "tunnels.cloudflared_missing", params: { url: "https://pkg.cloudflare.com/" } },
          },
        }),
      "GET /api/server/software": () => json(200, { managed: true, reason: null, family: "apt", items: [CLOUDFLARED_MISSING] }),
      "POST /api/server/software/cloudflared/install": (call) => {
        installs.push(call.body);
        return json(202, { job_id: "j9", status: "queued", message: "queued" });
      },
      "GET /api/jobs/j9": () => json(200, softwareJob("running", { id: "j9", metadata: { component: "cloudflared", action: "install" } })),
    });

    // The notice above says what this server lacks, once, with the install: not a form's error.
    expect(await screen.findByText("cloudflared is missing")).toBeInTheDocument();
    expect(screen.getByText(/Cloudflare tunnels go through it: without it none can be made\./)).toBeInTheDocument();
    expect(screen.queryByText("English hint from the server")).not.toBeInTheDocument();
    // The page's action waits, and its "?" says what for.
    expect(screen.getByRole("button", { name: "New tunnel" })).toBeDisabled();
    // One place to install it: the notice's.
    expect(screen.getAllByRole("button", { name: "Install" })).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Install" }));
    await waitFor(() => expect(installs).toEqual([{ epel: false }]));
    expect(await screen.findByText("Installing cloudflared")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("leads to the DNS section when there is no Cloudflare account yet", async () => {
    network("/network/tunnels", {
      "GET /api/tunnels": () =>
        json(200, {
          tunnels: [],
          support: { available: false, cloudflared: "/usr/bin/cloudflared", sealed: false, accounts: [], problem: "There is no Cloudflare account", hint: null, coded: { code: "tunnels.no_account", params: {} } },
        }),
    });
    expect(await screen.findByText("It also needs a Cloudflare account to manage it.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a Cloudflare account" })).toHaveAttribute("href", "/domains/dns");
  });

  it("creates a tunnel through the only Cloudflare account", async () => {
    const { user, backend } = network("/network/tunnels", {
      "POST /api/tunnels": () => json(201, { tunnel: HOME, warnings: [], released: [] }),
    });

    await user.click(await screen.findByRole("button", { name: "New tunnel" }));
    const dialog = await screen.findByRole("dialog", { name: "Create a tunnel" });
    await user.type(within(dialog).getByLabelText("Name"), "home");
    await user.click(within(dialog).getByRole("button", { name: "Create tunnel" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/tunnels")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/tunnels")[0]?.body).toEqual({ name: "home", account: "cf" });
  });

  it("carries a name for an application, and says what happened to its record", { timeout: 20_000 }, async () => {
    const { user, backend } = network("/network/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "POST /api/tunnels/home/routes": () =>
        json(201, {
          tunnel: HOME,
          route: { tunnel: "home", hostname: "admin.example.com", service: "http://localhost:80", app_domain: "admin.example.com", created_at: "2026-10-12T10:00:00+00:00" },
          ingress: [],
          dns: { outcome: "created", type: "CNAME", content: "tun-1.cfargotunnel.com", hostname: "admin.example.com", account: "cf" },
        }),
    });

    const card = await screen.findByRole("article", { name: "home" });
    await user.click(within(card).getByRole("button", { name: "Actions for home" }));
    await user.click(await screen.findByRole("menuitem", { name: "Add a name" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a name to home" });
    await user.type(within(dialog).getByLabelText("Public name"), "admin.example.com");
    await user.click(within(dialog).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "admin.example.com" }));
    await user.click(within(dialog).getByRole("button", { name: "Add a name" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/tunnels/home/routes")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/tunnels/home/routes")[0]?.body).toEqual({ hostname: "admin.example.com", dns: true, app_domain: "admin.example.com" });
  });

  it("asks before it replaces a record somebody else made, naming it, and then sends the confirmation", { timeout: 20_000 }, async () => {
    let asked = 0;
    const route = { tunnel: "home", hostname: "x.example.com", service: "http://localhost:3001", app_domain: null, created_at: "2026-10-12T10:00:00+00:00" };
    const { user, backend } = network("/network/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "POST /api/tunnels/home/routes": (call) => {
        asked += 1;
        const body = call.body as Record<string, unknown>;
        if (body["replace_record"] === "rec-7") return json(201, { tunnel: HOME, route, ingress: [], dns: { outcome: "replaced", type: "CNAME", content: "tun-1.cfargotunnel.com", hostname: "x.example.com", account: "cf" } });
        return json(201, {
          tunnel: HOME,
          route,
          ingress: [],
          dns: { outcome: "conflict", type: "CNAME", content: "tun-1.cfargotunnel.com", hostname: "x.example.com", record_id: "rec-7", current: "elsewhere.example.net", detail: "x.example.com already has a record" },
        });
      },
    });

    const card = await screen.findByRole("article", { name: "home" });
    await user.click(within(card).getByRole("button", { name: "Actions for home" }));
    await user.click(await screen.findByRole("menuitem", { name: "Add a name" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a name to home" });
    await user.type(within(dialog).getByLabelText("Public name"), "x.example.com");
    await user.click(within(dialog).getByRole("radio", { name: /A local service/ }));
    await user.type(within(dialog).getByLabelText("Service"), "http://localhost:3001");
    await user.click(within(dialog).getByRole("button", { name: "Add a name" }));

    const confirm = await screen.findByRole("alertdialog", { name: "Replace the record of x.example.com?" });
    expect(within(confirm).getByText("elsewhere.example.net")).toBeInTheDocument();
    expect(within(confirm).getByText("rec-7")).toBeInTheDocument();
    expect(asked).toBe(1);

    await user.click(within(confirm).getByRole("button", { name: "Replace the record" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/tunnels/home/routes")).toHaveLength(2);
    });
    expect(backend.callsTo("POST /api/tunnels/home/routes")[1]?.body).toMatchObject({
      hostname: "x.example.com",
      service: "http://localhost:3001",
      replace_record: "rec-7",
      replace_current: "elsewhere.example.net",
    });
  });

  it("opens a tunnel to show what it carries, and stops carrying a name after asking", async () => {
    const { user, backend } = network("/network/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "DELETE /api/tunnels/home/routes/shop.example.com": () =>
        json(200, { tunnel: HOME, route: HOME.routes[0], ingress: [], dns: { outcome: "deleted" } }),
    });

    const card = await screen.findByRole("article", { name: "home" });
    await user.click(within(card).getByRole("button", { name: "Actions for home" }));
    await user.click(await screen.findByRole("menuitem", { name: "See what it carries" }));
    const drawer = await screen.findByRole("dialog", { name: "home" });
    const routes = within(drawer).getByRole("list", { name: "Names carried by home" });
    expect(within(routes).getByText("http://localhost:80")).toBeInTheDocument();
    await user.click(within(routes).getByRole("button", { name: "Stop carrying shop.example.com" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop carrying shop.example.com" });
    await user.click(within(dialog).getByRole("button", { name: "Stop carrying it" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/tunnels/home/routes/shop.example.com")).toHaveLength(1);
    });
  });

  it("deletes a tunnel only once its name is typed", async () => {
    const { user, backend } = network("/network/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "DELETE /api/tunnels/home": () => json(200, { tunnel: HOME, warnings: [], released: [] }),
    });

    const card = await screen.findByRole("article", { name: "home" });
    await user.click(within(card).getByRole("button", { name: "Actions for home" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete tunnel" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete home" });
    expect(within(dialog).getByRole("button", { name: "Delete tunnel" })).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "home");
    await user.click(within(dialog).getByRole("button", { name: "Delete tunnel" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/tunnels/home")).toHaveLength(1);
    });
  });
});
