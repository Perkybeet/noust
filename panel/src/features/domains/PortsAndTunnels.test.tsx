import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

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

function network(path: string, routes: Record<string, RouteHandler>) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/dns/providers": () => json(200, { accounts: [], kinds: [] }),
    "GET /api/tunnels": () => json(200, { tunnels: [], support: TUNNEL_SUPPORT }),
    "GET /api/streams": () => json(200, { exposures: [], support: STREAM_SUPPORT }),
    ...routes,
  });
  const harness = renderConsole(path);
  return { ...harness, backend };
}

describe("the Ports tab", () => {
  it("lists what is published, from where, and the state of each", { timeout: 20_000 }, async () => {
    network("/domains/ports", { "GET /api/streams": () => json(200, { exposures: [MINECRAFT], support: STREAM_SUPPORT }) });

    const table = await screen.findByRole("region", { name: "Published TCP and UDP ports" });
    const row = (await within(table).findByText("minecraft")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("TCP 25565")).toBeInTheDocument();
    expect(within(row).getByText("127.0.0.1:25565")).toBeInTheDocument();
    expect(within(row).getByText("203.0.113.0/24")).toBeInTheDocument();
    expect(within(row).getByText("Published")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says what a port is for when none is published", async () => {
    network("/domains/ports", {});

    expect(await screen.findByText("No TCP or UDP ports published")).toBeInTheDocument();
    expect(screen.getByText("noust stream add minecraft --port 25565 --to 25565")).toBeInTheDocument();
  });

  it("shows what is missing, in the server's words, and offers no way to publish until it is there", async () => {
    network("/domains/ports", {
      "GET /api/streams": () =>
        json(200, {
          exposures: [],
          support: { available: false, module: "missing", loader: "manual", problem: "This nginx was built without the stream module", hint: "apt install libnginx-mod-stream" },
        }),
    });

    expect(await screen.findByText("This nginx was built without the stream module")).toBeInTheDocument();
    expect(screen.getByText("apt install libnginx-mod-stream")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish a port" })).toBeDisabled();
  });

  it("asks what publishing would do, shows it, and only then publishes", { timeout: 20_000 }, async () => {
    let published: object[] = [];
    const plan = {
      exposure: { ...MINECRAFT, state: "opening" },
      config: "server {}",
      file: "/etc/nginx/noust-stream/minecraft.conf",
      loader: "write",
      support: STREAM_SUPPORT,
      warnings: ["Nothing listens on 127.0.0.1:25565 yet"],
      firewall: [{ port: 25565, proto: "tcp", source: "203.0.113.0/24" }],
    };
    const { user, backend } = network("/domains/ports", {
      "GET /api/streams": () => json(200, { exposures: published, support: STREAM_SUPPORT }),
      "POST /api/streams/plan": () => json(200, plan),
      "POST /api/streams": () => {
        published = [MINECRAFT];
        return json(201, { exposure: MINECRAFT, warnings: [], firewall: "ufw now allows 1 rule(s) for it" });
      },
    });

    await user.click((await screen.findAllByRole("button", { name: "Publish a port" }))[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Publish a port" });
    await user.type(within(dialog).getByLabelText("Name"), "minecraft");
    await user.type(within(dialog).getByLabelText("Public port"), "25565");
    await user.type(within(dialog).getByLabelText("Backend port"), "25565");
    await user.type(within(dialog).getByLabelText(/^Allowed addresses/), "203.0.113.0/24");

    await user.click(within(dialog).getByRole("button", { name: "Check" }));
    expect(await within(dialog).findByText("This is what publishing it does")).toBeInTheDocument();
    expect(within(dialog).getByText("/etc/nginx/noust-stream/minecraft.conf")).toBeInTheDocument();
    expect(within(dialog).getByText("tcp 25565 <- 203.0.113.0/24")).toBeInTheDocument();
    expect(within(dialog).getByText("Nothing listens on 127.0.0.1:25565 yet")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/streams")).toHaveLength(0);
    expect(backend.callsTo("POST /api/streams/plan")[0]?.body).toEqual({
      name: "minecraft",
      protocol: "tcp",
      listen_port: 25565,
      target_port: 25565,
      target_host: "127.0.0.1",
      sources: ["203.0.113.0/24"],
      timeout_seconds: 3600,
    });

    await user.click(within(dialog).getByRole("button", { name: "Publish" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/streams")).toHaveLength(1);
    });
    const table = await screen.findByRole("region", { name: "Published TCP and UDP ports" });
    expect(await within(table).findByText("minecraft")).toBeInTheDocument();
  });

  it("changing a field after the check asks again instead of publishing what was not checked", async () => {
    const { user, backend } = network("/domains/ports", {
      "POST /api/streams/plan": () =>
        json(200, { exposure: MINECRAFT, config: "", file: "/x.conf", loader: "present", support: STREAM_SUPPORT, warnings: [], firewall: [] }),
    });

    await user.click((await screen.findAllByRole("button", { name: "Publish a port" }))[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Publish a port" });
    await user.type(within(dialog).getByLabelText("Name"), "minecraft");
    await user.type(within(dialog).getByLabelText("Public port"), "25565");
    await user.type(within(dialog).getByLabelText("Backend port"), "25565");
    await user.click(within(dialog).getByRole("button", { name: "Check" }));
    await within(dialog).findByText("This is what publishing it does");

    await user.type(within(dialog).getByLabelText("Public port"), "1");

    expect(within(dialog).queryByText("This is what publishing it does")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Check" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/streams")).toHaveLength(0);
  });

  it("shows a port that cannot be had beside the field, in the server's words", async () => {
    const { user } = network("/domains/ports", {
      "POST /api/streams/plan": () => problem(400, "validation_error", "Port 443 is the web server's", { fields: { listen_port: "Port 443 is the web server's" } }),
    });

    await user.click((await screen.findAllByRole("button", { name: "Publish a port" }))[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Publish a port" });
    await user.type(within(dialog).getByLabelText("Name"), "web");
    await user.type(within(dialog).getByLabelText("Public port"), "443");
    await user.type(within(dialog).getByLabelText("Backend port"), "8080");
    await user.click(within(dialog).getByRole("button", { name: "Check" }));

    expect(await within(dialog).findByText("Port 443 is the web server's")).toBeInTheDocument();
  });

  it("shows nginx's own words when it refuses the configuration", async () => {
    const { user } = network("/domains/ports", {
      "POST /api/streams/plan": () =>
        json(200, { exposure: MINECRAFT, config: "", file: "/x.conf", loader: "present", support: STREAM_SUPPORT, warnings: [], firewall: [] }),
      "POST /api/streams": () =>
        problem(400, "validation_error", "nginx refused the configuration for this port, so nothing was changed", {
          output: 'nginx: [emerg] "listen" directive is not allowed here in minecraft.conf:4',
        }),
    });

    await user.click((await screen.findAllByRole("button", { name: "Publish a port" }))[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Publish a port" });
    await user.type(within(dialog).getByLabelText("Name"), "minecraft");
    await user.type(within(dialog).getByLabelText("Public port"), "25565");
    await user.type(within(dialog).getByLabelText("Backend port"), "25565");
    await user.click(within(dialog).getByRole("button", { name: "Check" }));
    await within(dialog).findByText("This is what publishing it does");
    await user.click(within(dialog).getByRole("button", { name: "Publish" }));

    expect(await within(dialog).findByText(/directive is not allowed here in minecraft\.conf:4/)).toBeInTheDocument();
  });

  it("closes a port after asking, and says the rules that were already there stay", async () => {
    const { user, backend } = network("/domains/ports", {
      "GET /api/streams": () => json(200, { exposures: [MINECRAFT], support: STREAM_SUPPORT }),
      "DELETE /api/streams/minecraft": () => json(200, { exposure: MINECRAFT, warnings: [], firewall: "" }),
    });

    const table = await screen.findByRole("region", { name: "Published TCP and UDP ports" });
    const row = (await within(table).findByText("minecraft")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for minecraft" }));
    await user.click(await screen.findByRole("menuitem", { name: "Close the port" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Close minecraft" });
    expect(within(dialog).getByText(/A rule that was already there stays/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Close the port" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/streams/minecraft")).toHaveLength(1);
    });
  });
});

describe("the Tunnels tab", () => {
  it("lists the tunnels with their connector and what each carries", { timeout: 20_000 }, async () => {
    network("/domains/tunnels", { "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }) });

    const table = await screen.findByRole("region", { name: "Cloudflare Tunnels" });
    const row = (await within(table).findByText("home")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("shop.example.com")).toBeInTheDocument();
    expect(within(row).getByText("active")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says what a tunnel is for when there is none", async () => {
    network("/domains/tunnels", {});

    expect(await screen.findByText("No tunnels yet")).toBeInTheDocument();
    expect(screen.getByText("noust tunnel create home")).toBeInTheDocument();
  });

  it("names where the connector comes from when it is not installed, and offers no way to create one", async () => {
    network("/domains/tunnels", {
      "GET /api/tunnels": () =>
        json(200, {
          tunnels: [],
          support: { available: false, cloudflared: null, sealed: false, accounts: ["cf"], problem: "cloudflared is not installed", hint: "Install it from Cloudflare's own package repository, https://pkg.cloudflare.com/" },
        }),
    });

    expect(await screen.findByText("cloudflared is not installed")).toBeInTheDocument();
    expect(screen.getByText(/https:\/\/pkg\.cloudflare\.com\//)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create a tunnel" })).toBeDisabled();
  });

  it("creates a tunnel through the only Cloudflare account", async () => {
    const { user, backend } = network("/domains/tunnels", {
      "POST /api/tunnels": () => json(201, { tunnel: HOME, warnings: [], released: [] }),
    });

    await user.click((await screen.findAllByRole("button", { name: "Create a tunnel" }))[0] as HTMLElement);
    const dialog = await screen.findByRole("dialog", { name: "Create a tunnel" });
    await user.type(within(dialog).getByLabelText("Name"), "home");
    await user.click(within(dialog).getByRole("button", { name: "Create tunnel" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/tunnels")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/tunnels")[0]?.body).toEqual({ name: "home", account: "cf" });
  });

  it("carries a name for an application, and says what happened to its record", { timeout: 20_000 }, async () => {
    const { user, backend } = network("/domains/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "POST /api/tunnels/home/routes": () =>
        json(201, {
          tunnel: HOME,
          route: { tunnel: "home", hostname: "admin.example.com", service: "http://localhost:80", app_domain: "admin.example.com", created_at: "2026-10-12T10:00:00+00:00" },
          ingress: [],
          dns: { outcome: "created", type: "CNAME", content: "tun-1.cfargotunnel.com", hostname: "admin.example.com", account: "cf" },
        }),
    });

    const table = await screen.findByRole("region", { name: "Cloudflare Tunnels" });
    const row = (await within(table).findByText("home")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for home" }));
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
    const { user, backend } = network("/domains/tunnels", {
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

    const table = await screen.findByRole("region", { name: "Cloudflare Tunnels" });
    const row = (await within(table).findByText("home")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for home" }));
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
    const { user, backend } = network("/domains/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "DELETE /api/tunnels/home/routes/shop.example.com": () =>
        json(200, { tunnel: HOME, route: HOME.routes[0], ingress: [], dns: { outcome: "deleted" } }),
    });

    const table = await screen.findByRole("region", { name: "Cloudflare Tunnels" });
    await user.click(await within(table).findByText("home"));
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
    const { user, backend } = network("/domains/tunnels", {
      "GET /api/tunnels": () => json(200, { tunnels: [HOME], support: TUNNEL_SUPPORT }),
      "DELETE /api/tunnels/home": () => json(200, { tunnel: HOME, warnings: [], released: [] }),
    });

    const table = await screen.findByRole("region", { name: "Cloudflare Tunnels" });
    const row = (await within(table).findByText("home")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for home" }));
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
