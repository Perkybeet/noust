import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { APPS, SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { nasSession } from "../../test/nas";
import { screenWidth } from "./testRoutes";

const NAME = "vault.casa.example.org";

const APP = { ...APPS[0], domain: NAME, app_type: "container", port: 8081, unit: "", layout: "inplace", path: "/volume1/docker/noust/apps/vault-casa-example-org" };

const PROXY_HINT = {
  target: "http://127.0.0.1:8081",
  scheme: "http",
  host: "127.0.0.1",
  port: 8081,
  websocket: true,
  read_timeout_seconds: 60,
  headers: [
    { name: "Host", meaning: "public_host", nginx: "$host" },
    { name: "Upgrade", meaning: "upgrade_request", nginx: "$http_upgrade" },
  ],
};

async function overview(address: Record<string, unknown>, session = nasSession()) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(session),
    [`GET /api/apps/${NAME}`]: () => json(200, APP),
    [`GET /api/apps/${NAME}/address`]: () => json(200, address),
    [`GET /api/apps/${NAME}/runs`]: () => json(200, { domain: NAME, form: "container", via: "image", image: "vaultwarden/server:1.32" }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/apps/${NAME}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
  });
  const harness = renderConsole(`/apps/${NAME}`);
  await screen.findByRole("heading", { level: 1, name: NAME });
  return { ...harness, backend };
}

describe("how an application is reached on a machine that only has Docker (item 188)", () => {
  it("says the address the server says, a port of the machine, and not a domain it has not got", { timeout: 60_000 }, async () => {
    const { backend } = await overview({ domain: NAME, kind: "port", url: "http://nas-casa:8081", port: 8081, proxy_hint: PROXY_HINT });

    // The facts line opens the address of the machine; there are no names and no certificates to ask about.
    expect(await screen.findByRole("link", { name: /http:\/\/nas-casa:8081/ })).toHaveAttribute("href", "http://nas-casa:8081");
    expect(backend.callsTo(`GET /api/apps/${NAME}/domains`)).toHaveLength(0);
    expect(backend.callsTo("GET /api/certs")).toHaveLength(0);
    expect(screen.getByText(/Answers at/)).toBeInTheDocument();
    expect(screen.queryByText("Manage domains")).not.toBeInTheDocument();
    // How it runs says it is a container, and does not offer the other way, which this machine has not got.
    // The card holds its place until the server has answered, so what it says is waited for and the region read after it.
    expect(await screen.findByText("In a container, from an image of a registry.")).toBeInTheDocument();
    const how = screen.getByRole("region", { name: /How it is running/ });
    expect(within(how).queryByText(/not converted on the fly/)).not.toBeInTheDocument();
    // No certificate is read, and none is said.
    expect(backend.callsTo("GET /api/certs")).toHaveLength(0);
    expect(screen.queryByText("Certificate")).not.toBeInTheDocument();
  });

  it("explains the three ways to publish it outside: as it is, through a tunnel, and with the machine's own proxy", { timeout: 60_000 }, async () => {
    const { user } = await overview({ domain: NAME, kind: "port", url: "http://nas-casa:8081", port: 8081, proxy_hint: PROXY_HINT });
    const section = await screen.findByRole("region", { name: "Publish it outside" });

    expect(within(section).getByRole("button", { name: /As it is, on your network/ })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: /Through an outbound tunnel/ })).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: /With the machine's own reverse proxy/ }));

    // The values to paste, one by one, and the rule as nginx writes it.
    expect(within(section).getByText("Destination port")).toBeInTheDocument();
    expect(within(section).getByText("8081")).toBeInTheDocument();
    const rule = within(section).getByRole("group", { name: "Rule for the machine's reverse proxy" });
    expect(rule).toHaveTextContent("proxy_pass http://127.0.0.1:8081;");
    expect(rule).toHaveTextContent("proxy_set_header Upgrade $http_upgrade;");
    expect(within(section).getByRole("button", { name: "Copy the rule" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("offers the tunnel only where the table says the machine can run one", { timeout: 60_000 }, async () => {
    const session = nasSession();
    const features = session.central?.features as unknown as Record<string, { available: boolean; reason: unknown }>;
    features["tunnels"] = { available: false, reason: null };
    await overview({ domain: NAME, kind: "port", url: "http://nas-casa:8081", port: 8081, proxy_hint: PROXY_HINT }, session);
    const section = await screen.findByRole("region", { name: "Publish it outside" });

    expect(within(section).queryByRole("button", { name: /Through an outbound tunnel/ })).not.toBeInTheDocument();
    expect(within(section).getByRole("button", { name: /With the machine's own reverse proxy/ })).toBeInTheDocument();
  });

  it("says an application that serves no web has nothing to open, and draws no way to open it", { timeout: 60_000 }, async () => {
    await overview({ domain: NAME, kind: "none", url: null, port: null, proxy_hint: null });
    const section = await screen.findByRole("region", { name: "Publish it outside" });

    expect(within(section).getByText("This application serves no web, so there is nothing to open.")).toBeInTheDocument();
    expect(within(section).queryByRole("button")).not.toBeInTheDocument();
  });

  it("draws none of it on a server, which has its domain and its certificate", { timeout: 60_000 }, async () => {
    const { backend } = await overview({ domain: NAME, kind: "site", url: `https://${NAME}`, port: null, proxy_hint: null }, SESSION);

    expect(screen.queryByRole("region", { name: "Publish it outside" })).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${NAME}/address`)).toHaveLength(0);
  });
});
