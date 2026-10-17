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

const APP = "shop.example.com";

const CERT = {
  domain: APP,
  domains: [APP],
  valid_until: "2026-12-01 00:00:00+00:00 (VALID: 67 days)",
  expires_on: "2026-12-01",
  days_remaining: 67,
  auto_renew: true,
  path: `/etc/letsencrypt/live/${APP}/fullchain.pem`,
  key_path: `/etc/letsencrypt/live/${APP}/privkey.pem`,
  issuer: "C = US, O = Let's Encrypt, CN = R11",
  source: "letsencrypt",
  challenge: "http-01",
};

const APP_CERTIFICATE = {
  app: APP,
  source: "acme",
  name: null,
  challenge: "http-01",
  dns_provider: null,
  authority: "Let's Encrypt",
  tls: true,
  names: [APP],
  certificate: null,
  job_id: null,
};

const DOMAINS = [
  { domain: APP, kind: "primary", created_at: "2026-09-20T10:00:00+00:00", port: null },
  { domain: `admin.${APP}`, kind: "alias", created_at: "2026-09-21T10:00:00+00:00", port: 9000 },
];

const NO_DNS = { app: APP, addresses: ["203.0.113.10"], plans: [], unmanaged: [APP, `admin.${APP}`], records: [] };

function tab(routes: Record<string, RouteHandler> = {}, canonical = "") {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [CERT], total: 1 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${APP}/domains`]: () => json(200, { app: APP, domains: DOMAINS, canonical }),
    [`GET /api/apps/${APP}/dns`]: () => json(200, NO_DNS),
    [`GET /api/apps/${APP}/certificate`]: () => json(200, APP_CERTIFICATE),
    "GET /api/certs/acme": () => json(200, acme()),
    "GET /api/certs/uploaded": () => json(200, { certificates: [] }),
    "GET /api/dns/providers": () => json(200, { accounts: [], kinds: [] }),
    ...routes,
  });
  const harness = renderConsole(`/apps/${APP}/domains`);
  return { ...harness, backend };
}

function acme(changes: Record<string, unknown> = {}) {
  return {
    email: "ops@example.com",
    ca: "letsencrypt",
    label: "Let's Encrypt",
    server: "",
    needs_eab: false,
    eab_kid: "",
    eab_configured: false,
    trusted: true,
    authorities: [
      { name: "letsencrypt", label: "Let's Encrypt", needs_eab: false, trusted: true },
      { name: "letsencrypt-staging", label: "Let's Encrypt (staging)", needs_eab: false, trusted: false },
      { name: "zerossl", label: "ZeroSSL", needs_eab: true, trusted: true },
    ],
    ...changes,
  };
}

function change(domains: object[] = DOMAINS, extra: Record<string, unknown> = {}) {
  return { app: APP, domains, tls: true, adopted: [], certificate_job_id: null, dns: [], canonical: "", ...extra };
}

describe("an application's Domains tab, beyond its names", () => {
  it("shows the port a name goes to beside its role", { timeout: 20_000 }, async () => {
    tab();

    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    const admin = await within(table).findByRole("link", { name: /^admin\.shop\.example\.com/ });
    const row = admin.closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Alias")).toBeInTheDocument();
    expect(within(row).getByText("goes to port 9000")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("changes the port of an alias, or puts the application's own back", async () => {
    const { user, backend } = tab({
      [`PUT /api/apps/${APP}/domains/admin.${APP}/port`]: () => json(200, change()),
    });

    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    const admin = await within(table).findByRole("link", { name: /^admin\.shop\.example\.com/ });
    const row = admin.closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: `Actions for admin.${APP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Change port" }));
    const dialog = await screen.findByRole("dialog", { name: `Port of admin.${APP}` });
    const port = within(dialog).getByLabelText("Port");
    expect(port).toHaveValue("9000");
    await user.clear(port);
    await user.type(port, "9100");
    await user.click(within(dialog).getByRole("button", { name: "Save port" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/domains/admin.${APP}/port`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${APP}/domains/admin.${APP}/port`)[0]?.body).toEqual({ port: 9100 });
  });

  it("offers no port for the primary domain", async () => {
    const { user } = tab();

    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    const primary = await within(table).findByRole("link", { name: /^shop\.example\.com/ });
    const row = primary.closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: `Actions for ${APP}` }));

    expect(await screen.findByRole("menuitem", { name: "Check DNS" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Change port" })).not.toBeInTheDocument();
  });

  it("chooses the spelling that is served and lets the other redirect to it", async () => {
    const { user, backend } = tab({
      [`PUT /api/apps/${APP}/domains/canonical`]: () => json(200, change(DOMAINS, { canonical: "www" })),
    });

    const choice = await screen.findByRole("radiogroup", { name: "Which spelling is served" });
    expect(within(choice).getByRole("radio", { name: /Both, as recorded/ })).toBeChecked();
    await user.click(within(choice).getByRole("radio", { name: /^www\.shop\.example\.com/ }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/domains/canonical`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${APP}/domains/canonical`)[0]?.body).toEqual({ canonical: "www" });
  });

  it("shows the spelling already chosen", async () => {
    tab({}, "apex");

    const choice = await screen.findByRole("radiogroup", { name: "Which spelling is served" });
    expect(within(choice).getByRole("radio", { name: /^shop\.example\.com/ })).toBeChecked();
  });

  it("adds a wildcard without asking DNS about a name that has no address, and explains the certificate", async () => {
    const { user, backend } = tab({
      [`POST /api/apps/${APP}/domains`]: () => json(201, change([...DOMAINS, { domain: `*.${APP}`, kind: "alias", created_at: "2026-10-12T10:00:00+00:00", port: null }])),
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.type(within(dialog).getByLabelText("Domain"), `*.${APP}`);
    expect(await within(dialog).findByText("A wildcard needs the DNS challenge")).toBeInTheDocument();
    // The name has no address of its own, so there is nothing to ask DNS: the first press adds it.
    await user.click(within(dialog).getByRole("button", { name: "Add domain" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${APP}/domains`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/apps/${APP}/domains`)[0]?.body).toEqual({ domain: `*.${APP}`, kind: "alias", force: false, dns: true });
    expect(backend.calls.filter((call) => call.path.includes("/domains/") && call.path.endsWith("/dns"))).toHaveLength(0);
  });

  it("sends the port and leaves the DNS record alone when it is asked to", async () => {
    const { user, backend } = tab({
      [`GET /api/apps/${APP}/domains/api.${APP}/dns`]: () =>
        json(200, { domain: `api.${APP}`, expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true, verdict: "here", cdn: null, resolver: "1.1.1.1" }),
      [`POST /api/apps/${APP}/domains`]: () => json(201, change()),
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.type(within(dialog).getByLabelText("Domain"), `api.${APP}`);
    await user.type(within(dialog).getByLabelText(/^Port/), "9200");
    await user.click(within(dialog).getByRole("checkbox", { name: /Make its DNS record/ }));
    await user.click(within(dialog).getByRole("button", { name: "Check DNS" }));
    expect(await within(dialog).findByText(`api.${APP} points here`)).toBeInTheDocument();
    expect(within(dialog).getByText("Asked of 1.1.1.1, as the certificate authority will.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Add domain" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${APP}/domains`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/apps/${APP}/domains`)[0]?.body).toEqual({ domain: `api.${APP}`, kind: "alias", force: false, dns: false, port: 9200 });
  });

  it("says a name behind Cloudflare's proxy is reached, and that a server behind NAT cannot compare", async () => {
    const { user } = tab({
      [`GET /api/apps/${APP}/domains/cdn.${APP}/dns`]: () =>
        json(200, { domain: `cdn.${APP}`, expected_addresses: ["10.0.0.5"], resolved_addresses: ["104.16.0.1"], points_here: true, verdict: "cdn", cdn: "Cloudflare", resolver: "1.1.1.1" }),
      [`GET /api/apps/${APP}/domains/nat.${APP}/dns`]: () =>
        json(200, { domain: `nat.${APP}`, expected_addresses: ["10.0.0.5"], resolved_addresses: ["203.0.113.10"], points_here: false, verdict: "unknown", cdn: null, resolver: "1.1.1.1" }),
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.type(within(dialog).getByLabelText("Domain"), `cdn.${APP}`);
    await user.click(within(dialog).getByRole("button", { name: "Check DNS" }));
    expect(await within(dialog).findByText(`cdn.${APP} is behind Cloudflare's proxy`)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add domain" })).toBeInTheDocument();

    const name = within(dialog).getByLabelText("Domain");
    await user.clear(name);
    await user.type(name, `nat.${APP}`);
    await user.click(within(dialog).getByRole("button", { name: "Check DNS" }));
    expect(await within(dialog).findByText(`nat.${APP} resolves, but this server cannot tell if that is its own address`)).toBeInTheDocument();
    expect(within(dialog).getByText("dns.public_addresses")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Add domain" })).toBeInTheDocument();
  });

  it("asks before it adds a name another site already answers on, with that site's words in front", async () => {
    let attempts = 0;
    const { user, backend } = tab({
      [`GET /api/apps/${APP}/domains/legacy.${APP}/dns`]: () =>
        json(200, { domain: `legacy.${APP}`, expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true, verdict: "here", cdn: null, resolver: "" }),
      [`POST /api/apps/${APP}/domains`]: (call) => {
        attempts += 1;
        if ((call.body as Record<string, unknown>)["force"] === true) return json(201, change());
        return problem(409, "domain_overlap", `Another site of the web server already answers on legacy.${APP}`, {
          hint: "The site 'old' answers on it. Add it anyway with force.",
        });
      },
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.type(within(dialog).getByLabelText("Domain"), `legacy.${APP}`);
    await user.click(within(dialog).getByRole("button", { name: "Check DNS" }));
    await within(dialog).findByText(`legacy.${APP} points here`);
    await user.click(within(dialog).getByRole("button", { name: "Add domain" }));

    expect(await within(dialog).findByText(`Another site already answers on legacy.${APP}`)).toBeInTheDocument();
    expect(within(dialog).getByText("The site 'old' answers on it. Add it anyway with force.")).toBeInTheDocument();
    expect(attempts).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Add it anyway" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${APP}/domains`)).toHaveLength(2);
    });
    expect(backend.callsTo(`POST /api/apps/${APP}/domains`)[1]?.body).toMatchObject({ force: true });
  });

  it("follows the certificate job a removal starts", async () => {
    const job = { id: "j1", type: "cert_create", name: `SSL for ${APP}`, description: "again", status: "running", progress: 20, total_steps: 100, current_step: "Removing the domain from the certificate", created_at: "2026-10-12T10:00:00", logs: [], metadata: { domain: APP } };
    const { user, backend } = tab({
      [`DELETE /api/apps/${APP}/domains/admin.${APP}`]: () => json(200, change([DOMAINS[0] as object], { certificate_job_id: "j1" })),
      "GET /api/jobs/j1": () => json(200, job),
    });

    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    const admin = await within(table).findByRole("link", { name: /^admin\.shop\.example\.com/ });
    const row = admin.closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: `Actions for admin.${APP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.type(within(dialog).getByRole("textbox"), `admin.${APP}`);
    await user.click(within(dialog).getByRole("button", { name: "Remove domain" }));

    await waitFor(() => {
      expect(backend.callsTo("GET /api/jobs/j1").length).toBeGreaterThan(0);
    });
  });
});

describe("an application's DNS records", () => {
  const PLAN = { hostname: APP, type: "A", content: "203.0.113.10", account: "cf", zone: "example.com", action: "create", current: null, record_id: null, automatic: true };

  it("shows what each name would point at and makes the record in one press", async () => {
    let plans: object[] = [PLAN];
    const { user, backend } = tab({
      [`GET /api/apps/${APP}/dns`]: () => json(200, { app: APP, addresses: ["203.0.113.10"], plans, unmanaged: [], records: [] }),
      [`POST /api/apps/${APP}/dns`]: () => {
        plans = [{ ...PLAN, action: "exists" }];
        return json(200, { hostname: APP, type: "A", content: "203.0.113.10", outcome: "created", account: "cf" });
      },
    });

    const list = await screen.findByRole("list", { name: `Records for the names of ${APP}` });
    expect(within(list).getByText(/Will be created in cf/)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Make the record" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${APP}/dns`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/apps/${APP}/dns`)[0]?.body).toEqual({ hostname: APP, content: "203.0.113.10", type: "A", account: "cf" });
    expect(await within(list).findByText(/Already there/)).toBeInTheDocument();
  });

  it("never replaces a record somebody else made until the operator names it", async () => {
    const conflict = { ...PLAN, action: "conflict", current: "9.9.9.9", record_id: "rec-3" };
    const { user, backend } = tab({
      [`GET /api/apps/${APP}/dns`]: () => json(200, { app: APP, addresses: ["203.0.113.10"], plans: [conflict], unmanaged: [], records: [] }),
      [`POST /api/apps/${APP}/dns`]: () => json(200, { hostname: APP, type: "A", content: "203.0.113.10", outcome: "replaced", account: "cf" }),
    });

    const list = await screen.findByRole("list", { name: `Records for the names of ${APP}` });
    expect(within(list).getByText(/Another record holds 9\.9\.9\.9/)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Replace…" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Replace the record of ${APP}?` });
    expect(backend.callsTo(`POST /api/apps/${APP}/dns`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Replace the record" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${APP}/dns`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/apps/${APP}/dns`)[0]?.body).toMatchObject({ replace_record: "rec-3", replace_current: "9.9.9.9" });
  });

  it("says no account holds the zone, and where to add one", async () => {
    tab();

    expect(await screen.findByText("No DNS account holds the zone of these names.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a DNS account" })).toHaveAttribute("href", "/domains/dns");
  });

  it("lists the records Noust made, and the ones that were already there", async () => {
    tab({
      [`GET /api/apps/${APP}/dns`]: () =>
        json(200, {
          app: APP,
          addresses: [],
          plans: [],
          unmanaged: [],
          records: [
            { id: 1, provider: "cf", zone: "example.com", hostname: APP, type: "A", content: "203.0.113.10", owned: true, proxied: false, applications: [APP], unreferenced_since: null },
            { id: 2, provider: "cf", zone: "example.com", hostname: `www.${APP}`, type: "CNAME", content: APP, owned: false, proxied: false, applications: [APP], unreferenced_since: null },
          ],
        }),
    });

    const records = await screen.findByRole("list", { name: `Records Noust keeps for ${APP}` });
    expect(within(records).getByText("Made by Noust")).toBeInTheDocument();
    expect(within(records).getByText("Was there")).toBeInTheDocument();
  });
});

describe("where an application's certificate comes from", () => {
  it("says how it is ordered and by whom", async () => {
    tab();

    expect(await screen.findByText("Ordered from Let's Encrypt, proven over HTTP")).toBeInTheDocument();
  });

  it("warns that a staging authority issues what browsers do not trust", async () => {
    tab({ "GET /api/certs/acme": () => json(200, acme({ ca: "letsencrypt-staging", label: "Let's Encrypt (staging)", trusted: false })) });

    expect(await screen.findByText(/The authority is a staging one: browsers do not trust what it issues\./)).toBeInTheDocument();
  });

  it("changes the proof to DNS with an account, orders again as a job, and the page follows it", { timeout: 20_000 }, async () => {
    const accounts = [{ name: "cf", kind: "cloudflare", label: "Cloudflare", native: true, automatic: false, verified_at: null, last_error: null, propagation_seconds: null, created_at: "" }];
    const job = { id: "j2", type: "cert_create", name: `SSL for ${APP}`, description: "ordering", status: "running", progress: 10, total_steps: 100, current_step: "Ordering", created_at: "2026-10-12T10:00:00", logs: [], metadata: { domain: APP } };
    const { user, backend } = tab({
      "GET /api/dns/providers": () => json(200, { accounts, kinds: [] }),
      [`PUT /api/apps/${APP}/certificate`]: () => json(200, { ...APP_CERTIFICATE, challenge: "dns-01", dns_provider: "cf", job_id: "j2" }),
      "GET /api/jobs/j2": () => json(200, job),
    });

    await user.click(await screen.findByRole("button", { name: "Change" }));
    const dialog = await screen.findByRole("dialog", { name: `Where the certificate of ${APP} comes from` });
    await user.click(within(dialog).getByRole("radio", { name: /Ordered over DNS/ }));
    await user.click(within(dialog).getByRole("combobox", { name: "DNS account" }));
    await user.click(await screen.findByRole("option", { name: "cf" }));
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/certificate`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${APP}/certificate`)[0]?.body).toEqual({ source: "acme", challenge: "dns-01", dns_provider: "cf", order: true });
    await waitFor(() => {
      expect(backend.callsTo("GET /api/jobs/j2").length).toBeGreaterThan(0);
    });
  });

  it("asks for the account before it asks the server", async () => {
    const { user, backend } = tab({ "GET /api/dns/providers": () => json(200, { accounts: [{ name: "cf", kind: "cloudflare", label: "Cloudflare", native: true, automatic: false, verified_at: null, last_error: null, propagation_seconds: null, created_at: "" }], kinds: [] }) });

    await user.click(await screen.findByRole("button", { name: "Change" }));
    const dialog = await screen.findByRole("dialog", { name: `Where the certificate of ${APP} comes from` });
    await user.click(within(dialog).getByRole("radio", { name: /Ordered over DNS/ }));
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    expect(await within(dialog).findByText("Choose the DNS account that answers the challenge.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${APP}/certificate`)).toHaveLength(0);
  });

  it("offers an uploaded certificate only when there is one, and serves it", async () => {
    const uploaded = { name: "origin", names: [APP, `*.${APP}`], subject: null, issuer: null, key_type: null, fingerprint: "AA", not_before: null, not_after: null, days_remaining: 300, uploaded_by: null, uploaded_at: "", used_by: [], chain_warning: null };
    const { user, backend } = tab({
      "GET /api/certs/uploaded": () => json(200, { certificates: [uploaded] }),
      [`PUT /api/apps/${APP}/certificate`]: () => json(200, { ...APP_CERTIFICATE, source: "custom", name: "origin", job_id: null }),
    });

    await user.click(await screen.findByRole("button", { name: "Change" }));
    const dialog = await screen.findByRole("dialog", { name: `Where the certificate of ${APP} comes from` });
    await user.click(await within(dialog).findByRole("radio", { name: /An uploaded certificate/ }));
    await user.click(within(dialog).getByRole("combobox", { name: "Uploaded certificate" }));
    await user.click(await screen.findByRole("option", { name: /^origin/ }));
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${APP}/certificate`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT /api/apps/${APP}/certificate`)[0]?.body).toEqual({ source: "custom", name: "origin", challenge: "http-01", order: true });
  });

  it("fills the name field with a temporary address made of the server's public one", async () => {
    const { user } = tab({
      "GET /api/domains/temporary": () => json(200, { address: "203.0.113.10", domain: "203-0-113-10.sslip.io", service: "sslip.io", declared: false }),
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.click(within(dialog).getByRole("button", { name: "Use a temporary address" }));

    expect(await within(dialog).findByDisplayValue("203-0-113-10.sslip.io")).toBeInTheDocument();
  });

  it("says why there is no temporary address when the server has no public one", async () => {
    const { user } = tab({
      "GET /api/domains/temporary": () => problem(400, "domain_error", "This server has no public address to make a name of", { hint: "noust config set dns.public_addresses 203.0.113.10" }),
    });

    await user.click(await screen.findByRole("button", { name: "Add domain" }));
    const dialog = await screen.findByRole("dialog", { name: `Add a domain to ${APP}` });
    await user.click(within(dialog).getByRole("button", { name: "Use a temporary address" }));

    expect(await within(dialog).findByText("This server has no public address to make a name of", { selector: "p, div, span" })).toBeInTheDocument();
  });

  it("marks a temporary name in the list of the application's domains", async () => {
    tab({
      [`GET /api/apps/${APP}/domains`]: () =>
        json(200, { app: APP, domains: [...DOMAINS, { domain: "203-0-113-10.sslip.io", kind: "alias", created_at: "2026-09-22T10:00:00+00:00", port: null, temporary: true }], canonical: "" }),
    });

    const table = await screen.findByRole("region", { name: `Domains of ${APP}` });
    const link = await within(table).findByRole("link", { name: /^203-0-113-10\.sslip\.io/ });
    const row = link.closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Temporary")).toBeInTheDocument();
  });
});
