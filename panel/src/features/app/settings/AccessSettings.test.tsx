import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import { DOMAIN, ELEVATED, saveBar, renderSettings } from "./testKit";

const FIELDS = {
  basic_auth: false,
  realm: "Restricted",
  access: "all",
  allow_ips: [],
  deny_ips: [],
  real_ip: "off",
  real_ip_header: "",
  trusted_proxies: [],
  rate_limit: "",
  rate_burst: 20,
  connection_limit: 0,
  headers: {},
  hidden_headers: [],
  hsts: "default",
  noindex: false,
  canonical: "",
  http3: false,
  client_certs: false,
  force_https: true,
  waf: "",
  waf_exclusions: [] as number[],
  forward_auth: null,
  routes: [],
};

const PRESETS = {
  presets: [
    {
      name: "oauth2-proxy",
      url: "http://127.0.0.1:4180/oauth2/auth",
      sign_in: "/oauth2/start?rd=",
      headers: ["X-Auth-Request-User", "X-Auth-Request-Email"],
      note: "Run oauth2-proxy with --set-xauthrequest.",
    },
  ],
};

const TAILNET = {
  name: "tailscale",
  kind: "tailscale",
  interface: "tailscale0",
  cidrs: ["100.64.0.0/10", "fd7a:115c:a1e0::/48"],
  addresses: ["100.101.102.103"],
  up: true,
  dns_name: "web.tail1234.ts.net",
  tailnet: "example.org",
  cert_domains: ["web.tail1234.ts.net"],
  problem: "",
};

function privateStatus(changes: Record<string, unknown> = {}) {
  return {
    app: DOMAIN,
    private: false,
    networks: [],
    allow_ips: [],
    public_ranges: [],
    applicable: true,
    reason: "",
    available: [TAILNET],
    tailnet_name: "web.tail1234.ts.net",
    tailnet_certificate: "",
    ...changes,
  };
}

const WAF = {
  available: true,
  webserver: "nginx",
  module_loaded: true,
  module_installed: true,
  rules: true,
  problem: "",
  hint: "",
  installable: true,
  packages: ["libnginx-mod-http-modsecurity", "modsecurity-crs"],
  anchor: false,
};

const NO_EVENTS = { events: [], noisy: [] };

const SERIAL = "1A000000000000000000000000000001";
const OLD_SERIAL = "2B000000000000000000000000000002";

function certificate(name: string, changes: Record<string, unknown> = {}) {
  return {
    name,
    serial: SERIAL,
    fingerprint: "AA:BB",
    not_before: "2026-10-01T00:00:00+00:00",
    not_after: "2999-10-01T00:00:00+00:00",
    issued_by: "ana",
    issued_at: "2026-10-01T00:00:00+00:00",
    revoked: false,
    revoked_at: null,
    revoked_by: null,
    ...changes,
  };
}

function clientCerts(certificates: object[] = [], enabled = false) {
  return { enabled, authority: certificates.length > 0, certificates };
}

const HTTP3 = { available: true, webserver: "nginx", version: "1.26.2", problem: "", hint: "", anchor: false, udp_allowed: true, firewall_hint: "" };

function policy(changes: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return {
    app: DOMAIN,
    policy: { ...FIELDS, ...changes },
    users: ["ana"],
    webserver: "nginx",
    applicable: true,
    reason: "",
    headers: [{ name: "X-Frame-Options", value: "SAMEORIGIN" }],
    real_ip_networks: ["173.245.48.0/20", "103.21.244.0/22"],
    real_ip_source: "built-in",
    ...extra,
  };
}

async function access(routes: Record<string, RouteHandler> = {}, body: object = policy()) {
  screenWidth(1440);
  const harness = await renderSettings("/access", "Access", undefined, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, body),
    [`GET /api/apps/${DOMAIN}/http3`]: () => json(200, HTTP3),
    [`GET /api/apps/${DOMAIN}/private`]: () => json(200, privateStatus()),
    [`GET /api/apps/${DOMAIN}/waf`]: () => json(200, WAF),
    [`GET /api/apps/${DOMAIN}/waf/events`]: () => json(200, NO_EVENTS),
    [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts()),
    "GET /api/policy/forward-auth-presets": () => json(200, PRESETS),
    ...routes,
  });
  // The policy is read after the page has its heading.
  await screen.findByRole("checkbox", { name: /Ask for a password/ });
  return harness;
}

describe("an application's access settings", () => {
  it("shows the password, the accounts, the lists, the limits and what the site sends", { timeout: 20_000 }, async () => {
    await access();

    expect(screen.getByRole("checkbox", { name: /Ask for a password/ })).not.toBeChecked();
    const accounts = screen.getByRole("list", { name: `Accounts of ${DOMAIN}` });
    expect(within(accounts).getByText("ana")).toBeInTheDocument();
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("");
    expect(screen.getByLabelText("Burst")).toHaveValue("20");
    expect(screen.getByText("X-Frame-Options: SAMEORIGIN")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("sends only what changed, and then has nothing left to save", { timeout: 20_000 }, async () => {
    let stored = policy();
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as Record<string, unknown>;
        stored = policy({ allow_ips: sent["allow_ips"] ?? [], noindex: sent["noindex"] ?? false });
        return json(200, stored);
      },
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "203.0.113.0/24{enter}198.51.100.7");
    await user.click(screen.getByRole("checkbox", { name: /Ask search engines not to index/ }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      accept_unreachable_auth: false,
      allow_ips: ["203.0.113.0/24", "198.51.100.7"],
      noindex: true,
    });
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("203.0.113.0/24\n198.51.100.7");
  });

  it("asks before it saves lists that would refuse the operator's own address, and says which", async () => {
    let calls = 0;
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        calls += 1;
        if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, policy({ allow_ips: ["10.0.0.1"] }));
        return problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
          fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
        });
      },
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "10.0.0.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("alertdialog", { name: "These lists would refuse your own address" });
    expect(within(dialog).getByText(/198\.51\.100\.4 is the address you are using now/)).toBeInTheDocument();
    // It is a question, not a red error beside the field.
    expect(screen.queryByText("These lists would refuse your own address, 198.51.100.4")).not.toBeInTheDocument();
    expect(calls).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Save and risk the lockout" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body).toEqual({ accept_lockout: true, accept_unreachable_auth: false, allow_ips: ["10.0.0.1"] });
    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
  });

  it("declining the lockout keeps the lists as typed and sends nothing more", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
          fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
        }),
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "10.0.0.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });
    expect(screen.getByLabelText("Allowed addresses")).toHaveValue("10.0.0.1");
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
  });

  it("shows a refusal beside the field it is about, in the server's words", async () => {
    const { user } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "bad network", { fields: { allow_ips: "999.1.1.1 is not an address or a network" } }),
    });

    await user.type(screen.getByLabelText("Allowed addresses"), "999.1.1.1");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("999.1.1.1 is not an address or a network")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("tells a mistake it can see before asking the server, and sends nothing", async () => {
    const { user, backend } = await access();

    await user.type(screen.getByLabelText("Requests per client"), "fast");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Use a whole number of requests, 1 or more, or leave it empty for no limit.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
  });

  it("adds an account with a password the server hashes, and shows it in the list", async () => {
    let users = ["ana"];
    const { user, backend } = await access({
      [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, policy({}, { users })),
      [`PUT /api/apps/${DOMAIN}/policy/users/bob`]: () => {
        users = ["ana", "bob"];
        return json(200, policy({}, { users }));
      },
    });

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an account" });
    await user.type(within(dialog).getByLabelText("Name"), "bob");
    await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
    expect(within(dialog).getByLabelText("Password")).toHaveAttribute("type", "password");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy/users/bob`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy/users/bob`)[0]?.body).toEqual({ password: "correct horse battery" });
    const accounts = screen.getByRole("list", { name: `Accounts of ${DOMAIN}` });
    expect(await within(accounts).findByText("bob")).toBeInTheDocument();
    // The password is not anywhere on the page once the dialog has closed.
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add an account" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("correct horse battery");
  });

  it("refuses a login name the server would refuse, without asking it", async () => {
    const { user, backend } = await access();

    await user.click(screen.getByRole("button", { name: "Add account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add an account" });
    await user.type(within(dialog).getByLabelText("Name"), "a b");
    await user.type(within(dialog).getByLabelText("Password"), "correct horse battery");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    expect(await within(dialog).findByText("Letters, digits and . _ @ - only.", { selector: "span" })).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT" && call.path.includes("/policy/users/"))).toHaveLength(0);
  });

  it("removes an account after asking, and warns when it is the only one a password needs", async () => {
    let users = ["ana"];
    const { user, backend } = await access(
      {
        [`GET /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ basic_auth: true }, { users })),
        [`DELETE /api/apps/${DOMAIN}/policy/users/ana`]: () => {
          users = [];
          return json(200, policy({ basic_auth: true }, { users }));
        },
      },
      policy({ basic_auth: true }),
    );

    await user.click(screen.getByRole("button", { name: "Remove: ana" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove ana" });
    expect(within(dialog).getByText(/It is the only account and the site asks for a password/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/policy/users/ana`)).toHaveLength(1);
    });
    expect(await screen.findByText("No accounts yet.")).toBeInTheDocument();
  });

  it("shows the limits only for nginx, which is the only server that has them", async () => {
    await access({}, policy({}, { webserver: "apache" }));

    expect(screen.queryByLabelText("Requests per client")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Allowed addresses")).toBeInTheDocument();
  });

  it("says why a policy does not reach the site, in the server's words", async () => {
    await access({}, policy({}, { applicable: false, reason: "A static site served by Apache has no password support here." }));

    expect(screen.getByText("This policy does not reach the site")).toBeInTheDocument();
    expect(screen.getByText("A static site served by Apache has no password support here.")).toBeInTheDocument();
  });

  it("names the ranges Cloudflare's address option trusts, and asks for the header of a proxy of its own", async () => {
    const { user } = await access();

    expect(screen.getByText(/Trust the header Cloudflare sends, from the networks it publishes: 2 \(built in\)\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("Header that carries the address")).not.toBeInTheDocument();
    await user.click(screen.getByRole("radio", { name: /Another proxy tells it/ }));
    expect(screen.getByLabelText("Header that carries the address")).toBeInTheDocument();
    expect(screen.getByLabelText("Trusted proxies")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await access();

    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { level: 2, name: "Acceso" })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Pedir contraseña/ })).toBeInTheDocument();
  });

  it("lists the routes of the site, each with what it does", async () => {
    await access(
      {},
      policy({
        routes: [
          { path: "/api", kind: "proxy", auth: "inherit", port: 4000, strip: true, to: "", code: 301, keep_path: true, headers: {} },
          { path: "/admin", kind: "site", auth: "password", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: { "X-A": "1" } },
          { path: "/docs", kind: "redirect", auth: "inherit", port: 0, strip: false, to: "https://docs.example.org/", code: 308, keep_path: true, headers: {} },
        ],
      }),
    );

    const routes = screen.getByRole("list", { name: `Routes of ${DOMAIN}` });
    expect(within(routes).getByText("served by port 4000, prefix removed")).toBeInTheDocument();
    expect(within(routes).getByText("served as the site · password asked · headers: X-A")).toBeInTheDocument();
    expect(within(routes).getByText("308 to https://docs.example.org/")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("adds a route in a dialog and saves it with the rest of the form", { timeout: 20_000 }, async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as { routes: object[] };
        return json(200, policy({ routes: sent.routes }));
      },
    });
    expect(screen.getByText("No routes. The whole site answers the same way.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "/api/");
    await user.click(within(dialog).getByRole("radio", { name: /Another service/ }));
    await user.type(within(dialog).getByLabelText("Port"), "4000");
    await user.click(within(dialog).getByRole("checkbox", { name: /Take the prefix off the path/ }));
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));

    const routes = await screen.findByRole("list", { name: `Routes of ${DOMAIN}` });
    expect(within(routes).getByText("served by port 4000, prefix removed")).toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      accept_unreachable_auth: false,
      routes: [{ path: "/api", kind: "proxy", auth: "inherit", port: 4000, strip: true, to: "", code: 301, keep_path: true, headers: {} }],
    });
    await waitFor(() => {
      expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    });
  });

  it("tells a route it cannot send before sending anything", async () => {
    const { user, backend } = await access();

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "admin");
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));

    expect(await within(dialog).findByText(/Use a path such as \/admin/)).toBeInTheDocument();
    expect(within(dialog).getByText("A route of the site must ask for a password or lift it, or send a header.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("edits a route in place and removes another", async () => {
    const { user } = await access(
      {},
      policy({
        routes: [
          { path: "/admin", kind: "site", auth: "password", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: {} },
          { path: "/hooks", kind: "site", auth: "open", port: 0, strip: false, to: "", code: 301, keep_path: true, headers: {} },
        ],
      }),
    );

    await user.click(screen.getByRole("button", { name: "Remove /hooks" }));
    expect(within(screen.getByRole("list", { name: `Routes of ${DOMAIN}` })).queryByText("/hooks")).not.toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit /admin" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit the route /admin" });
    expect(within(dialog).getByLabelText("Prefix")).toBeDisabled();
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));
    // Nothing differs from what is stored except the removed route.
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("shows the server's refusal of the routes in its own words", async () => {
    const { user } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () =>
        problem(400, "validation_error", "bad route", { fields: { routes: "/api: port 22 is SSH's" } }),
    });

    await user.click(screen.getByRole("button", { name: "Add a route" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a route" });
    await user.type(within(dialog).getByLabelText("Prefix"), "/api");
    await user.click(within(dialog).getByRole("radio", { name: /Another service/ }));
    await user.type(within(dialog).getByLabelText("Port"), "22");
    await user.click(within(dialog).getByRole("button", { name: "Apply to the form" }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("/api: port 22 is SSH's")).toBeInTheDocument();
  });

  it("turns HTTP/3 on from a checkbox and sends only that", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ http3: true })),
    });

    await user.click(await screen.findByRole("checkbox", { name: /Serve this site over HTTP\/3/ }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, http3: true });
  });

  it("lets a site answer over HTTP as well from a checkbox that is on by default, and sends only that", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ force_https: false })),
    });

    const forced = await screen.findByRole("checkbox", { name: /Send visitors on port 80 to HTTPS/ });
    expect(forced).toBeChecked();
    await user.click(forced);
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, force_https: false });
  });

  it("says what is missing when the server cannot serve HTTP/3, and offers no way to turn it on", async () => {
    await access({
      [`GET /api/apps/${DOMAIN}/http3`]: () =>
        json(200, { ...HTTP3, available: false, version: "1.24.0", problem: "nginx 1.24.0 is older than 1.25.0, the first with HTTP/3", hint: "Install the nginx.org build." }),
    });

    expect(await screen.findByText("nginx 1.24.0 is older than 1.25.0, the first with HTTP/3")).toBeInTheDocument();
    expect(screen.getByText("Install the nginx.org build.")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Serve this site over HTTP\/3/ })).toHaveAttribute("aria-disabled", "true");
  });

  it("warns when the firewall keeps UDP 443 out, with the command that opens it", async () => {
    await access({
      [`GET /api/apps/${DOMAIN}/http3`]: () => json(200, { ...HTTP3, udp_allowed: false, firewall_hint: "ufw allow 443/udp" }),
    });

    expect(await screen.findByText("The firewall does not let UDP 443 in")).toBeInTheDocument();
    expect(screen.getByText("ufw allow 443/udp")).toBeInTheDocument();
  });

  it("does not offer HTTP/3 for a site Apache serves", async () => {
    await access({}, policy({}, { webserver: "apache" }));

    expect(screen.queryByRole("checkbox", { name: /Serve this site over HTTP\/3/ })).not.toBeInTheDocument();
  });

  it("fills the sign-in service from a preset and sends it as one field", async () => {
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        const sent = call.body as { forward_auth: object };
        return json(200, policy({ forward_auth: sent.forward_auth }));
      },
    });

    await user.click(await screen.findByRole("combobox", { name: "Start from" }));
    await user.click(await screen.findByRole("option", { name: "oauth2-proxy" }));
    expect(screen.getByLabelText("Address that is asked")).toHaveValue("http://127.0.0.1:4180/oauth2/auth");
    expect(screen.getByText("Run oauth2-proxy with --set-xauthrequest.")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({
      accept_lockout: false,
      accept_unreachable_auth: false,
      forward_auth: { url: "http://127.0.0.1:4180/oauth2/auth", sign_in: "/oauth2/start?rd=", headers: ["X-Auth-Request-User", "X-Auth-Request-Email"] },
    });
  });

  it("tells an address that is not one before asking the server", async () => {
    const { user, backend } = await access();

    await user.type(await screen.findByLabelText("Address that is asked"), "127.0.0.1:4180/auth");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Use an address that starts with http:// or https://.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
  });

  it("asks before saving a sign-in service that does not answer, with what was found", async () => {
    let calls = 0;
    const { user, backend } = await access({
      [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
        calls += 1;
        if ((call.body as Record<string, unknown>)["accept_unreachable_auth"] === true) return json(200, policy({ forward_auth: { url: "http://127.0.0.1:4180/auth", sign_in: "", headers: [] } }));
        return problem(400, "validation_error", "The authentication service at http://127.0.0.1:4180/auth does not answer: nothing accepts connections there", {
          fields: { forward_auth: "The authentication service at http://127.0.0.1:4180/auth does not answer: nothing accepts connections there" },
        });
      },
    });

    await user.type(await screen.findByLabelText("Address that is asked"), "http://127.0.0.1:4180/auth");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    const dialog = await screen.findByRole("alertdialog", { name: "The sign-in service does not answer" });
    expect(within(dialog).getByText(/nothing accepts connections there/)).toBeInTheDocument();
    // A question, not a red error beside the field.
    expect(calls).toBe(1);
    await user.click(within(dialog).getByRole("button", { name: "Save anyway" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
    });
    expect((backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body as Record<string, unknown>)["accept_unreachable_auth"]).toBe(true);
  });

  it("turns the sign-in service off by emptying its address", async () => {
    const stored = policy({ forward_auth: { url: "http://127.0.0.1:4180/auth", sign_in: "/oauth2/start?rd=", headers: ["X-A"] } });
    const { user, backend } = await access({ [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy()) }, stored);

    await user.clear(await screen.findByLabelText("Address that is asked"));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
    });
    expect((backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body as Record<string, unknown>)["forward_auth"]).toEqual({ url: "", sign_in: "/oauth2/start?rd=", headers: ["X-A"] });
  });
  describe("the private network card", () => {
    it("offers the networks the server is on and publishes to the one chosen", { timeout: 20_000 }, async () => {
      let stored = privateStatus();
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, stored),
        [`PUT /api/apps/${DOMAIN}/private`]: () => {
          stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs });
          return json(200, stored);
        },
      });

      const card = (await screen.findByRole("heading", { name: "Private network" })).closest("section");
      if (!card) throw new Error("no card");
      expect(await within(card).findByText(/Not private: it answers everybody/)).toBeInTheDocument();
      await user.click(within(card).getByRole("button", { name: "Answer only this network" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)[0]?.body).toEqual({ network: "tailscale", cidrs: [], accept_lockout: false });
      expect(await within(card).findByText("Private: only tailscale are let in.")).toBeInTheDocument();
      expect(within(card).getByRole("button", { name: "Answer everybody again" })).toBeInTheDocument();
      await expectNoAxeViolations(document.body);
    });

    it("asks before it publishes to a network the operator is not on", { timeout: 20_000 }, async () => {
      const { user, backend } = await access({
        [`PUT /api/apps/${DOMAIN}/private`]: (call) => {
          if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs }));
          return problem(400, "validation_error", "These lists would refuse your own address, 198.51.100.4", {
            fields: { allow_ips: "These lists would refuse your own address, 198.51.100.4" },
          });
        },
      });

      await user.click(await screen.findByRole("button", { name: "Answer only this network" }));
      const dialog = await screen.findByRole("alertdialog", { name: "These lists would refuse your own address" });
      expect(within(dialog).getByText(/198\.51\.100\.4 is the address you are using now/)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Save and risk the lockout" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)).toHaveLength(2);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/private`)[1]?.body).toEqual({ network: "tailscale", cidrs: [], accept_lockout: true });
    });

    it("says so when the server is on no private network, and names a network that is down", async () => {
      await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, privateStatus({ available: [{ ...TAILNET, up: false, problem: "Tailscale is Stopped" }], tailnet_name: "" })),
      });

      expect(await screen.findByText("This server is on no tailnet or WireGuard network Noust can see")).toBeInTheDocument();
      expect(screen.getByText("tailscale is not usable")).toBeInTheDocument();
      expect(screen.getByText("Tailscale is Stopped")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Answer only this network" })).not.toBeInTheDocument();
    });

    it("serves a private application with the tailnet's certificate when the server's administrator asks", { timeout: 20_000 }, async () => {
      let stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs });
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/private`]: () => json(200, stored),
        [`POST /api/apps/${DOMAIN}/private/certificate`]: () => {
          stored = privateStatus({ private: true, networks: ["tailscale"], allow_ips: TAILNET.cidrs, tailnet_certificate: "tailnet-web.tail1234.ts.net" });
          return json(200, { name: "tailnet-web.tail1234.ts.net", hostname: "web.tail1234.ts.net", not_after: "2027-01-01T00:00:00+00:00", applications: [DOMAIN] });
        },
      });

      expect(await screen.findByText("web.tail1234.ts.net")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Serve it with the tailnet's certificate" }));

      await waitFor(() => {
        expect(backend.callsTo(`POST /api/apps/${DOMAIN}/private/certificate`)).toHaveLength(1);
      });
      expect(await screen.findByText(/Served with tailnet-web\.tail1234\.ts\.net/)).toBeInTheDocument();
    });

    it("speaks Spanish", async () => {
      await access();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Red privada" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Responder solo a esta red" })).toBeInTheDocument();
    });
  });
  describe("the client certificates card", () => {
    const cardOf = async (): Promise<HTMLElement> => {
      const card = (await screen.findByRole("heading", { name: "Client certificates" })).closest("section");
      if (!card) throw new Error("no card");
      return card;
    };

    it("lists the certificates with their state and revokes one after asking", { timeout: 20_000 }, async () => {
      let revoked = false;
      const { user, backend } = await access(
        {
          [`GET /api/apps/${DOMAIN}/client-certs`]: () =>
            json(200, clientCerts([certificate("ana-laptop"), certificate("bob-phone", { serial: OLD_SERIAL, revoked: revoked || true, revoked_at: "2026-10-02T00:00:00+00:00" })], true)),
          [`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`]: () => {
            revoked = true;
            return new Response(null, { status: 204 });
          },
        },
        policy({ client_certs: true }),
      );

      const card = await cardOf();
      const list = await within(card).findByRole("list", { name: `Client certificates of ${DOMAIN}` });
      expect(within(list).getByText("ana-laptop")).toBeInTheDocument();
      expect(within(list).getByText("Valid")).toBeInTheDocument();
      expect(within(list).getByText("Revoked")).toBeInTheDocument();
      expect(within(list).queryByRole("button", { name: "Revoke: bob-phone" })).not.toBeInTheDocument();

      await user.click(within(list).getByRole("button", { name: "Revoke: ana-laptop" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Revoke the certificate ana-laptop?" });
      // It is the only valid one of a site that asks for them, and the dialog says so.
      expect(within(dialog).getByText(/last valid certificate/)).toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

      await waitFor(() => {
        expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)).toHaveLength(1);
      });
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)[0]?.search.get("accept_lockout")).toBe("true");
    });

    it("does not warn about the last certificate when another is valid, and sends no lockout consent", { timeout: 20_000 }, async () => {
      const { user, backend } = await access(
        {
          [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts([certificate("ana-laptop"), certificate("bob-phone", { serial: OLD_SERIAL })], true)),
          [`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`]: () => new Response(null, { status: 204 }),
        },
        policy({ client_certs: true }),
      );

      const card = await cardOf();
      await user.click(await within(card).findByRole("button", { name: "Revoke: ana-laptop" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(within(dialog).queryByText(/last valid certificate/)).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Revoke" }));

      await waitFor(() => {
        expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)).toHaveLength(1);
      });
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/client-certs/${SERIAL}`)[0]?.search.get("accept_lockout")).toBe("false");
    });

    it("makes a certificate and shows the password and the downloads once", { timeout: 30_000 }, async () => {
      let made = false;
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts(made ? [certificate("ana-laptop")] : [])),
        [`POST /api/apps/${DOMAIN}/client-certs`]: () => {
          made = true;
          return json(201, {
            certificate: certificate("ana-laptop"),
            bundle: "MIIBAA==",
            password: "correct-horse-battery",
            certificate_pem: "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n",
            key_pem: "-----BEGIN PRIVATE KEY-----\nBBBB\n-----END PRIVATE KEY-----\n",
            ca_pem: "-----BEGIN CERTIFICATE-----\nCCCC\n-----END CERTIFICATE-----\n",
          });
        },
      });

      const card = await cardOf();
      expect(await within(card).findByText("No certificates yet.")).toBeInTheDocument();
      await user.click(within(card).getByRole("button", { name: "Make a certificate" }));
      const dialog = await screen.findByRole("dialog", { name: "Make a certificate" });
      await user.type(within(dialog).getByLabelText("Name"), "ana-laptop");
      await user.click(within(dialog).getByRole("button", { name: "Make a certificate" }));

      await waitFor(() => {
        expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)).toHaveLength(1);
      });
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)[0]?.body).toEqual({ name: "ana-laptop", days: 365 });
      const ready = await screen.findByRole("dialog", { name: "Certificate ana-laptop is ready" });
      expect(within(ready).getByText("correct-horse-battery")).toBeInTheDocument();
      expect(within(ready).getByText("This is the only time the key and the password are shown")).toBeInTheDocument();
      expect(within(ready).getByRole("button", { name: "Download the bundle (.p12)" })).toBeInTheDocument();
      expect(within(ready).getByRole("button", { name: "Download the key (.key)" })).toBeInTheDocument();
      await expectNoAxeViolations(document.body);

      await user.click(within(ready).getByRole("button", { name: "Done" }));
      await waitFor(() => {
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      });
      // The password is gone with the window: nothing on the page holds it any more.
      expect(screen.queryByText("correct-horse-battery")).not.toBeInTheDocument();
      expect(await within(card).findByText("ana-laptop")).toBeInTheDocument();
    });

    it("refuses a name the server would refuse without asking it", { timeout: 20_000 }, async () => {
      const { user, backend } = await access();

      const card = await cardOf();
      await user.click(within(card).getByRole("button", { name: "Make a certificate" }));
      const dialog = await screen.findByRole("dialog", { name: "Make a certificate" });
      await user.type(within(dialog).getByLabelText("Name"), "has space");
      await user.click(within(dialog).getByRole("button", { name: "Make a certificate" }));

      expect(await within(dialog).findAllByText(/Letters, digits and \. _ @ - only, up to 64\./)).not.toHaveLength(0);
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/client-certs`)).toHaveLength(0);
    });

    it("asks before it asks every visitor for a certificate nobody has", { timeout: 20_000 }, async () => {
      const message = `No client certificate of ${DOMAIN} is valid, so no one could open it`;
      const { user, backend } = await access({
        [`PUT /api/apps/${DOMAIN}/policy`]: (call) => {
          if ((call.body as Record<string, unknown>)["accept_lockout"] === true) return json(200, policy({ client_certs: true }));
          return problem(400, "validation_error", message, { fields: { client_certs: message } });
        },
      });

      await user.click(screen.getByRole("checkbox", { name: /Ask every visitor for a certificate/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      const dialog = await screen.findByRole("alertdialog", { name: "No client certificate is valid yet" });
      // A question, not a red error beside the checkbox.
      expect(screen.queryByText(message)).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Save and shut everybody out" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(2);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, client_certs: true });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[1]?.body).toEqual({ accept_lockout: true, accept_unreachable_auth: false, client_certs: true });
    });

    it("saves the requirement with the rest of the form when a certificate is valid", { timeout: 20_000 }, async () => {
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/client-certs`]: () => json(200, clientCerts([certificate("ana-laptop")])),
        [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ client_certs: true })),
      });

      await user.click(screen.getByRole("checkbox", { name: /Ask every visitor for a certificate/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, client_certs: true });
    });

    it("speaks Spanish", async () => {
      await access();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Certificados de cliente" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Hacer un certificado" })).toBeInTheDocument();
    });
  });

  describe("the web application firewall card", () => {
    it("starts in detect mode when asked and sends only that", { timeout: 20_000 }, async () => {
      const { user, backend } = await access({
        [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ waf: "detect" })),
      });

      const card = (await screen.findByRole("heading", { name: "Web application firewall" })).closest("section");
      if (!card) throw new Error("no card");
      expect(within(card).getByRole("radio", { name: /Off/ })).toBeChecked();
      await user.click(within(card).getByRole("radio", { name: /Detect/ }));
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, waf: "detect" });
    });

    it("lists what the rules saw, flags what block mode would refuse, and leaves a noisy rule out on a click", { timeout: 20_000 }, async () => {
      const { user, backend } = await access(
        {
          [`GET /api/apps/${DOMAIN}/waf/events`]: () =>
            json(200, {
              events: [
                { time: "Sat Oct 17 01:21:33 2026", client: "203.0.113.9", host: DOMAIN, method: "GET", uri: "/search?q=select", status: 200, blocked: false, would_block: true, rules: [{ id: "942100", message: "SQL Injection", severity: "2" }, { id: "949110", message: "Score", severity: "2" }] },
                { time: "Sat Oct 17 01:21:30 2026", client: "198.51.100.4", host: DOMAIN, method: "GET", uri: "/?f=../../etc/passwd", status: 403, blocked: true, would_block: false, rules: [{ id: "930110", message: "Path Traversal", severity: "2" }, { id: "949110", message: "Score", severity: "2" }] },
              ],
              noisy: [{ id: "942100", message: "SQL Injection Attack Detected via libinjection", count: 4 }],
            }),
          [`PUT /api/apps/${DOMAIN}/policy`]: () => json(200, policy({ waf: "detect", waf_exclusions: [942100] })),
        },
        policy({ waf: "detect" }),
      );

      const list = await screen.findByRole("list", { name: "Requests the rules judged lately" });
      expect(within(list).getByText("GET /search?q=select")).toBeInTheDocument();
      expect(within(list).getByText("Block mode would refuse it")).toBeInTheDocument();
      expect(within(list).getByText("Refused")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Leave out 942100" }));
      expect(screen.getByLabelText("Rules left out for this site")).toHaveValue("942100");
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      await waitFor(() => {
        expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(1);
      });
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)[0]?.body).toEqual({ accept_lockout: false, accept_unreachable_auth: false, waf_exclusions: [942100] });
      await expectNoAxeViolations(document.body);
    });

    it("says what the server is missing and installs it for the server's administrator", { timeout: 20_000 }, async () => {
      let available = false;
      const { user, backend } = await access({
        [`GET /api/apps/${DOMAIN}/waf`]: () =>
          json(200, available ? WAF : { ...WAF, available: false, module_loaded: false, module_installed: false, rules: false, problem: "nginx has no ModSecurity module", hint: "Install it: noust app policy waf install" }),
        "POST /api/waf/install": () => {
          available = true;
          return json(200, { packages: WAF.packages });
        },
      });

      expect(await screen.findByText("nginx has no ModSecurity module")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Install it" }));

      await waitFor(() => {
        expect(backend.callsTo("POST /api/waf/install")).toHaveLength(1);
      });
      await waitFor(() => {
        expect(screen.queryByText("nginx has no ModSecurity module")).not.toBeInTheDocument();
      });
    });

    it("tells a rule number that is not one before asking the server", async () => {
      const { user, backend } = await access({}, policy({ waf: "block" }));

      await user.type(await screen.findByLabelText("Rules left out for this site"), "942100{enter}abc");
      await user.click(within(saveBar()).getByRole("button", { name: "Save" }));

      expect(await screen.findByText("Rule numbers are whole numbers, such as 942100.")).toBeInTheDocument();
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/policy`)).toHaveLength(0);
    });

    it("speaks Spanish", async () => {
      await access();
      await act(() => setLocale("es"));

      expect(await screen.findByRole("heading", { name: "Cortafuegos de aplicaciones web" })).toBeInTheDocument();
      expect(screen.getByRole("radio", { name: /Detectar/ })).toBeInTheDocument();
    });
  });
});
