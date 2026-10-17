import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
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

/** The first of several matches: the header's primary action, which an empty state repeats. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing matched");
  return item;
}

const KINDS = [
  {
    kind: "cloudflare",
    label: "Cloudflare",
    native: true,
    fields: [
      { name: "token", label: "API token", secret: true, required: true, default: "" },
      { name: "account_id", label: "Account id", secret: false, required: false, default: "" },
    ],
    help: "A token with Zone:Read and DNS:Edit.",
    package: "",
  },
  {
    kind: "digitalocean",
    label: "DigitalOcean",
    native: false,
    fields: [{ name: "token", label: "API token", secret: true, required: true, default: "" }],
    help: "A personal access token.",
    package: "python3-certbot-dns-digitalocean",
  },
  {
    kind: "route53",
    label: "Amazon Route 53",
    native: true,
    tunnels: false,
    comments: false,
    fields: [
      { name: "access_key_id", label: "Access key id", secret: false, required: true, default: "" },
      { name: "secret_access_key", label: "Secret access key", secret: true, required: true, default: "" },
    ],
    help: "The access key of an IAM user allowed to edit the zones.",
    package: "",
  },
];

const CF = {
  name: "cf",
  kind: "cloudflare",
  label: "Cloudflare",
  native: true,
  automatic: true,
  verified_at: "2026-10-12T10:00:00+00:00",
  last_error: null,
  propagation_seconds: null,
  created_at: "2026-10-01T10:00:00+00:00",
};

function dns(accounts: object[], routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/dns/providers": () => json(200, { accounts, kinds: KINDS }),
    "GET /api/tunnels": () => json(200, { tunnels: [], support: { available: true, cloudflared: "/usr/bin/cloudflared", sealed: false, accounts: ["cf"], problem: null, hint: null } }),
    "GET /api/streams": () => json(200, { exposures: [], support: { available: true, module: "dynamic", loader: "installable", problem: null, hint: null } }),
    ...routes,
  });
  const harness = renderConsole("/domains/dns");
  return { ...harness, backend };
}

describe("the DNS tab", () => {
  it("lists the accounts with how each behaves and whether its credential works", { timeout: 20_000 }, async () => {
    dns([CF]);

    const table = await screen.findByRole("region", { name: "DNS accounts" });
    const row = (await within(table).findByText("cf")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Cloudflare")).toBeInTheDocument();
    expect(within(row).getByText("Made without asking")).toBeInTheDocument();
    expect(within(row).getByText("Works")).toBeInTheDocument();
    // The strip the tab belongs to names its sister tabs and counts what each holds.
    const tabs = screen.getByRole("navigation", { name: "Domains and certificates sections" });
    expect(within(tabs).getByRole("link", { name: /^DNS/ })).toHaveAttribute("aria-current", "page");
    expect(within(tabs).getByRole("link", { name: /^Tunnels/ })).toBeInTheDocument();
    expect(within(tabs).getByRole("link", { name: /^Ports/ })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("says what an account is for when there is none, and offers the first", async () => {
    dns([]);

    expect(await screen.findByText("No DNS accounts yet")).toBeInTheDocument();
    expect(screen.getByText("noust dns provider add cloudflare --kind cloudflare --token-stdin")).toBeInTheDocument();
  });

  it("proves a token before it keeps it, sends it once and never shows it again", async () => {
    let accounts: object[] = [];
    const { user, backend } = dns(accounts, {
      "POST /api/dns/providers": () => {
        accounts = [CF];
        return json(201, CF);
      },
      "GET /api/dns/providers": () => json(200, { accounts, kinds: KINDS }),
    });

    await user.click(first(await screen.findAllByRole("button", { name: "Add account" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a DNS account" });
    await user.type(within(dialog).getByLabelText("Name"), "cf");
    const token = within(dialog).getByLabelText("API token");
    expect(token).toHaveAttribute("type", "password");
    await user.type(token, "cf-token-secret-value");
    await user.click(within(dialog).getByRole("checkbox", { name: /Make the records of a domain without asking/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/dns/providers")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/dns/providers")[0]?.body).toEqual({
      name: "cf",
      kind: "cloudflare",
      credentials: { token: "cf-token-secret-value" },
      automatic: true,
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a DNS account" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("cf-token-secret-value");
  });

  it("shows the provider's refusal beside the credential, in its own words", async () => {
    const { user } = dns([], {
      "POST /api/dns/providers": () => problem(502, "dnsprovidererror", "Cloudflare refused the token", { fields: { token: "Authentication error (10000)" } }),
    });

    await user.click(first(await screen.findAllByRole("button", { name: "Add account" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a DNS account" });
    await user.type(within(dialog).getByLabelText("Name"), "cf");
    await user.type(within(dialog).getByLabelText("API token"), "bad");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    expect(await within(dialog).findByText("Authentication error (10000)")).toBeInTheDocument();
  });

  it("refuses a name the server would refuse, without asking it", async () => {
    const { user, backend } = dns([]);

    await user.click(first(await screen.findAllByRole("button", { name: "Add account" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a DNS account" });
    await user.type(within(dialog).getByLabelText("Name"), "My Account");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    expect(await within(dialog).findByText("Use lower case letters, digits and dashes, starting with a letter or digit.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/dns/providers")).toHaveLength(0);
  });

  it("says a provider that only answers the challenge needs its certbot plugin", async () => {
    const { user } = dns([]);

    await user.click(first(await screen.findAllByRole("button", { name: "Add account" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a DNS account" });
    await user.click(within(dialog).getByRole("combobox", { name: "Provider" }));
    await user.click(await screen.findByRole("option", { name: "DigitalOcean" }));

    expect(await within(dialog).findByText("python3-certbot-dns-digitalocean")).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox", { name: /without asking/ })).not.toBeInTheDocument();
  });

  it("asks a provider with two credentials for both, keeps the secret half hidden and says it keeps no notes", async () => {
    const { user, backend } = dns([], {
      "POST /api/dns/providers": () => json(201, { ...CF, name: "aws", kind: "route53", label: "Amazon Route 53" }),
    });

    await user.click(first(await screen.findAllByRole("button", { name: "Add account" })));
    const dialog = await screen.findByRole("dialog", { name: "Add a DNS account" });
    await user.click(within(dialog).getByRole("combobox", { name: "Provider" }));
    await user.click(await screen.findByRole("option", { name: "Amazon Route 53" }));

    expect(within(dialog).getByLabelText("Access key id")).toHaveAttribute("type", "text");
    expect(within(dialog).getByLabelText("Secret access key")).toHaveAttribute("type", "password");
    expect(within(dialog).getByText(/keeps no note with a record/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /without asking/ })).toBeInTheDocument();
    expect(within(dialog).queryByText(/certbot/)).not.toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Name"), "aws");
    await user.type(within(dialog).getByLabelText("Access key id"), "AKIAIOSFODNN7EXAMPLE");
    await user.type(within(dialog).getByLabelText("Secret access key"), "secret-half-of-the-key");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/dns/providers")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/dns/providers")[0]?.body).toEqual({
      name: "aws",
      kind: "route53",
      credentials: { access_key_id: "AKIAIOSFODNN7EXAMPLE", secret_access_key: "secret-half-of-the-key" },
      automatic: false,
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a DNS account" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("secret-half-of-the-key");
  });

  it("removes an account after asking, and says the records stay", async () => {
    const { user, backend } = dns([CF], { "DELETE /api/dns/providers/cf": () => new Response(null, { status: 204 }) });

    const table = await screen.findByRole("region", { name: "DNS accounts" });
    const row = (await within(table).findByText("cf")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for cf" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove account" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove cf" });
    expect(within(dialog).getByText(/The records Noust made stay where they are/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove account" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/dns/providers/cf")).toHaveLength(1);
    });
  });

  it("shows an account that cannot be removed because a certificate or a tunnel needs it, in the server's words", async () => {
    const { user } = dns([CF], {
      "DELETE /api/dns/providers/cf": () => problem(400, "validation_error", "cf manages the tunnel home", { hint: "Remove the tunnel first: noust tunnel remove home" }),
    });

    const table = await screen.findByRole("region", { name: "DNS accounts" });
    const row = (await within(table).findByText("cf")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for cf" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove account" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove cf" });
    await user.click(within(dialog).getByRole("button", { name: "Remove account" }));

    expect(await within(dialog).findByText("cf manages the tunnel home")).toBeInTheDocument();
  });

  it("lists the records Noust manages, who made them and who uses them, and sweeps the unused", async () => {
    const { user, backend } = dns([CF], {
      "GET /api/dns/records": () =>
        json(200, {
          records: [
            { id: 1, provider: "cf", zone: "example.com", hostname: "shop.example.com", type: "A", content: "203.0.113.10", owned: true, proxied: false, applications: ["shop.example.com"], unreferenced_since: null },
            { id: 2, provider: "cf", zone: "example.com", hostname: "old.example.com", type: "A", content: "203.0.113.10", owned: false, proxied: false, applications: [], unreferenced_since: "2026-10-12T09:00:00+00:00" },
          ],
        }),
      "POST /api/dns/sweep": () => json(200, { released: [{ hostname: "old.example.com", type: "A", outcome: "not_owned", account: "cf" }] }),
    });

    await user.click(await screen.findByRole("button", { name: "Managed records" }));
    const drawer = await screen.findByRole("dialog", { name: "Managed records" });
    const list = await within(drawer).findByRole("list", { name: "Managed records" });
    expect(within(list).getByText("A shop.example.com")).toBeInTheDocument();
    expect(within(list).getByText("Made by Noust")).toBeInTheDocument();
    expect(within(list).getByText("Was there")).toBeInTheDocument();
    expect(within(list).getByText("Used by shop.example.com")).toBeInTheDocument();
    expect(within(list).getByText("Nothing uses it")).toBeInTheDocument();

    await user.click(within(drawer).getByRole("button", { name: "Release unused records" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/dns/sweep")).toHaveLength(1);
    });
  });

  it("speaks Spanish", async () => {
    dns([CF]);
    await screen.findByRole("region", { name: "DNS accounts" });

    await act(() => setLocale("es"));

    expect(await screen.findByRole("region", { name: "Cuentas DNS" })).toBeInTheDocument();
    expect(screen.getByText("Los crea sin preguntar")).toBeInTheDocument();
  });
});
