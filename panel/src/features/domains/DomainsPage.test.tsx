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

function site(name: string, extra: Record<string, unknown> = {}) {
  return {
    name,
    webserver: "nginx",
    enabled: true,
    config_path: `/etc/nginx/sites-available/${name}`,
    has_ssl: true,
    server_names: [name],
    noust_managed: true,
    app: name,
    site_name: name,
    pending_reload: false,
    edited_outside: false,
    ...extra,
  };
}

function cert(domain: string, days: number, domains: string[] = [domain]) {
  return { domain, domains, days_remaining: days, expires_on: "2027-01-01", valid_until: "2027-01-01", auto_renew: true, source: "letsencrypt", challenge: "http-01", issuer: "O = Let's Encrypt, CN = R11" };
}

const SITES = [
  site("shop.example.com", { server_names: ["shop.example.com", "www.shop.example.com"] }),
  site("blog.example.com"),
  site("old.example.org", { has_ssl: false, app: null, noust_managed: false }),
];
const CERTS = [cert("shop.example.com", 46, ["shop.example.com", "www.shop.example.com"]), cert("blog.example.com", 12)];

function domains(path: string, routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: CERTS, total: CERTS.length }),
    "GET /api/sites": () => json(200, { sites: SITES, total: SITES.length, webserver: "nginx" }),
    "GET /api/apps": () => json(200, { apps: [{ domain: "shop.example.com", name: "shop", status: "running", enabled: true, active: true }, { domain: "blog.example.com", name: "blog", status: "running", enabled: true, active: true }], total: 2 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    // What each application says of its own names: www.shop.example.com is sent to the application, not served as it.
    "GET /api/apps/shop.example.com/domains": () =>
      json(200, {
        app: "shop.example.com",
        canonical: "shop.example.com",
        domains: [
          { domain: "shop.example.com", kind: "primary", enabled: true, certificate: "app", certificate_pending: false, temporary: false },
          { domain: "www.shop.example.com", kind: "redirect", enabled: true, certificate: "app", certificate_pending: false, temporary: false },
        ],
      }),
    "GET /api/domains/dns": (call) =>
      call.search.get("name") === "old.example.org"
        ? json(200, { domain: "old.example.org", expected_addresses: ["203.0.113.10"], resolved_addresses: ["198.51.100.7"], points_here: false, verdict: "elsewhere", cdn: null, resolver: "" })
        : json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true, verdict: "here", cdn: null, resolver: "" }),
    "GET /api/dns/providers": () => json(200, { accounts: [], kinds: [] }),
    "GET /api/tunnels": () => json(200, { tunnels: [], support: { available: true, cloudflared: "/usr/bin/cloudflared", sealed: false, accounts: ["cf"], problem: null, hint: null } }),
    "GET /api/streams": () => json(200, { exposures: [], support: { available: true, module: "dynamic", loader: "installable", problem: null, hint: null } }),
    ...routes,
  });
  return { ...renderConsole(path), backend };
}

describe("the Domains section", () => {
  it("lists every name a site answers, with its application and whether it has HTTPS", { timeout: 20_000 }, async () => {
    domains("/domains");

    expect(await screen.findByRole("heading", { level: 1, name: "Domains" })).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Domains" });
    // A name is in its own cell, and where it leads is the application's or the site's: the first is the name's.
    const row = (name: string): HTMLElement => {
      const found = within(table).getAllByText(name)[0]?.closest("tr");
      if (!found) throw new Error(`no row for ${name}`);
      return found;
    };
    expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual(["Name", "Leads to", "HTTPS", "DNS", "Actions"]);
    // A name the certificate lists is covered by it, www included; an expiring one says so; a plain site says it has no HTTPS.
    expect(within(row("www.shop.example.com")).getByText("Valid for 46 days")).toBeInTheDocument();
    expect(within(row("blog.example.com")).getByText("Expires in 12 days")).toBeInTheDocument();
    expect(within(row("old.example.org")).getByText("No HTTPS")).toBeInTheDocument();
    expect(within(row("shop.example.com")).getAllByText("shop.example.com")).toHaveLength(2);
    // What each is: an application's, a redirect to one (its own list says so), or a site of its own.
    expect(within(row("shop.example.com")).getByText("Application")).toBeInTheDocument();
    expect(await within(row("www.shop.example.com")).findByText("Redirects")).toBeInTheDocument();
    expect(within(row("old.example.org")).getByText("Own site")).toBeInTheDocument();
    // Where it leads is where it is changed: its application's Domains, or its site.
    expect(within(row("blog.example.com")).getByRole("link", { name: "blog.example.com" })).toHaveAttribute("href", "/apps/blog.example.com/domains");
    expect(within(row("old.example.org")).getByRole("link", { name: "old.example.org" })).toHaveAttribute("href", "/domains/sites/old.example.org");
    // And where DNS points it, asked when its row is drawn.
    expect(await within(row("blog.example.com")).findByText("Points here")).toBeInTheDocument();
    expect(await within(row("old.example.org")).findByText("Points elsewhere")).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("filters by name and says how many are shown", async () => {
    domains("/domains?q=blog");
    const table = await screen.findByRole("region", { name: "Domains that match the search" });
    expect((await within(table).findAllByText("blog.example.com")).length).toBeGreaterThan(0);
    expect(within(table).queryByText("old.example.org")).not.toBeInTheDocument();
    expect(await screen.findByText("1 of 4 domains")).toBeInTheDocument();
  });

  it("puts a name on an application in one dialog: which application, the name, where it points, and Add", { timeout: 30_000 }, async () => {
    const { user, backend } = domains("/domains", {
      "GET /api/apps/blog.example.com/domains/blog.example.com/dns": () => json(200, {}),
      "GET /api/apps/blog.example.com/domains/shop.blog.example.com/dns": () =>
        json(200, { domain: "shop.blog.example.com", expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true, verdict: "here", cdn: null, resolver: "" }),
      "POST /api/apps/blog.example.com/domains": () => json(200, { app: "blog.example.com", domains: [], canonical: "bare", tls: true, certificate_job_id: null, adopted: [], dns: [] }),
    });

    await user.click(await screen.findByRole("button", { name: "Add a name" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a name" });
    // The one dialog says what a name is for: an application, a redirect to one, or a site of its own.
    for (const purpose of [/For an application/, /Redirect to another name/, /A site of its own/]) expect(within(dialog).getByRole("radio", { name: purpose })).toBeInTheDocument();
    // Without the application, pressing Add says what is missing where it is missing, and asks the server for nothing.
    await user.type(within(dialog).getByLabelText("Name"), "shop.blog.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add the name" }));
    expect((await within(dialog).findAllByText("Choose the application the name is for.")).length).toBeGreaterThan(0);
    expect(backend.callsTo("POST /api/apps/blog.example.com/domains")).toHaveLength(0);

    await user.click(within(dialog).getByRole("combobox", { name: "Application" }));
    await user.click(await screen.findByRole("option", { name: "blog.example.com" }));
    await user.click(within(dialog).getByRole("button", { name: "Add the name" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps/blog.example.com/domains").length).toBeGreaterThanOrEqual(1));
    expect(backend.callsTo("POST /api/apps/blog.example.com/domains")[0]?.body).toMatchObject({ domain: "shop.blog.example.com", kind: "alias" });
  });

  it("still lists the names when the certificates cannot be read, and says HTTPS is not shown", async () => {
    domains("/domains", { "GET /api/certs": () => problem(500, "internal_error", "certbot did not answer") });
    expect(await screen.findByText("Could not read the certificates, so HTTPS is not shown")).toBeInTheDocument();
    const table = await screen.findByRole("region", { name: "Domains" });
    expect((await within(table).findAllByText("blog.example.com")).length).toBeGreaterThan(0);
  });

  it("offers the first domain when there is none, once", async () => {
    domains("/domains", {
      "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
      "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    });
    expect(await screen.findByText(/A name is what your applications and sites answer on/)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add a name" })).toHaveLength(1);
    // The console adds it: no command to type.
    expect(screen.queryByText(/noust domain/)).not.toBeInTheDocument();
  });

  it("keeps the certificates on their own section, with their own title", async () => {
    domains("/domains/certificates");
    expect(await screen.findByRole("heading", { level: 1, name: "Certificates" })).toBeInTheDocument();
    expect(await screen.findByText("blog.example.com")).toBeInTheDocument();
  });

  it("keeps the card of the names no site serves apart from, and after, the sites", async () => {
    domains("/domains/sites", { "GET /api/unknown-hosts": () => json(200, { enabled: false, tls: false, file: "", holders: [], notes: [], disabled_distribution_site: "" }) });
    const table = await screen.findByRole("region", { name: "Web server sites" });
    const card = await screen.findByRole("heading", { level: 2, name: "Names no site serves" });
    const content = card.closest('[data-slot="content"]');
    expect(content).not.toBeNull();
    expect(content).toContainElement(table);
    // The table is first: what answers an unknown name is read once in a while and does not push the sites down.
    expect(table.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
