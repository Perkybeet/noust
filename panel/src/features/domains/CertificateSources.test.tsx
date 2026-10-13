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

const ORDERED = {
  domain: "shop.example.com",
  domains: ["shop.example.com"],
  valid_until: "2026-12-01 00:00:00+00:00 (VALID: 67 days)",
  expires_on: "2026-12-01",
  days_remaining: 67,
  auto_renew: true,
  path: "/etc/letsencrypt/live/shop.example.com/fullchain.pem",
  key_path: "/etc/letsencrypt/live/shop.example.com/privkey.pem",
  issuer: "C = US, O = Let's Encrypt, CN = R11",
  source: "letsencrypt",
  challenge: "dns-01",
};

const UPLOADED = {
  domain: "origin",
  domains: ["example.com", "*.example.com"],
  valid_until: "2030-01-01 00:00:00+00:00",
  expires_on: "2030-01-01",
  days_remaining: 1200,
  auto_renew: false,
  path: "/etc/noust/certs/origin/fullchain.pem",
  key_path: "/etc/noust/certs/origin/privkey.pem",
  issuer: "O = CloudFlare, Inc., CN = CloudFlare Origin SSL Certificate Authority",
  source: "custom",
  challenge: "",
};

const ACME = {
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
    { name: "custom", label: "Another authority", needs_eab: false, trusted: true },
  ],
};

function certificates(routes: Record<string, RouteHandler> = {}, list: object[] = [ORDERED, UPLOADED]) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: list, total: list.length }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/certs/acme": () => json(200, ACME),
    ...routes,
  });
  const harness = renderConsole("/domains");
  return { ...harness, backend };
}

describe("the Certificates tab, beyond ordering", () => {
  it("says where each certificate comes from, and offers an upload nothing to renew", { timeout: 20_000 }, async () => {
    const { user } = certificates();

    const table = await screen.findByRole("region", { name: "Certificates" });
    const origin = (await within(table).findByText("origin")).closest("tr");
    if (!origin) throw new Error("no row");
    await user.click(within(origin).getByRole("button", { name: "Actions for origin" }));
    expect(await screen.findByRole("menuitem", { name: "Delete upload" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Renew now" })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Revoke" })).not.toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(within(origin).getByText("origin"));
    const drawer = await screen.findByRole("dialog", { name: "origin" });
    expect(within(drawer).getByText("Uploaded by you; it is not renewed here")).toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "Renew" })).not.toBeInTheDocument();
    await expectNoAxeViolations(drawer);
  });

  it("says an ordered certificate was proven with a DNS record", async () => {
    const { user } = certificates();

    const table = await screen.findByRole("region", { name: "Certificates" });
    await user.click(await within(table).findByText("shop.example.com"));
    const drawer = await screen.findByRole("dialog", { name: "shop.example.com" });
    expect(within(drawer).getByText("Ordered, proven with a DNS record")).toBeInTheDocument();
  });

  it("uploads a certificate and its key, shows the names it covers, and never shows the key again", { timeout: 20_000 }, async () => {
    const { user, backend } = certificates({
      "POST /api/certs/uploaded": () => json(201, { name: "origin", names: ["example.com", "*.example.com"], fingerprint: "AA", uploaded_at: "", used_by: [] }),
    });

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Upload certificate" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload a certificate" });
    await user.type(within(dialog).getByLabelText("Name"), "origin");
    await user.click(within(dialog).getByLabelText("Certificate"));
    await user.paste("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----");
    await user.click(within(dialog).getByLabelText("Private key"));
    await user.paste("-----BEGIN PRIVATE KEY-----\nSECRETKEY\n-----END PRIVATE KEY-----");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Upload certificate" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/uploaded")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/certs/uploaded")[0]?.body).toEqual({
      name: "origin",
      certificate: "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----",
      private_key: "-----BEGIN PRIVATE KEY-----\nSECRETKEY\n-----END PRIVATE KEY-----",
      chain: "",
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Upload a certificate" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("SECRETKEY");
  });

  it("shows why the server refused the key, beside the key", async () => {
    const { user } = certificates({
      "POST /api/certs/uploaded": () => problem(400, "validation_error", "The key is not the certificate's", { fields: { private_key: "This private key is not the certificate's" } }),
    });

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Upload certificate" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload a certificate" });
    await user.type(within(dialog).getByLabelText("Name"), "origin");
    await user.click(within(dialog).getByLabelText("Certificate"));
    await user.paste("CERT");
    await user.click(within(dialog).getByLabelText("Private key"));
    await user.paste("KEY");
    await user.click(within(dialog).getByRole("button", { name: "Upload certificate" }));

    expect(await within(dialog).findByText("This private key is not the certificate's")).toBeInTheDocument();
  });

  it("refuses a name the server would refuse, without asking it", async () => {
    const { user, backend } = certificates();

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Upload certificate" }));
    const dialog = await screen.findByRole("dialog", { name: "Upload a certificate" });
    await user.type(within(dialog).getByLabelText("Name"), "My Cert");
    await user.click(within(dialog).getByRole("button", { name: "Upload certificate" }));

    expect(await within(dialog).findByText("Use lower case letters, digits, dots, dashes and underscores, starting with a letter or digit.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/certs/uploaded")).toHaveLength(0);
  });

  it("deletes an upload after asking, and shows the server's words when an application serves it", async () => {
    const { user, backend } = certificates({
      "DELETE /api/certs/uploaded/origin": () => problem(400, "validation_error", "origin is served by shop.example.com", { hint: "Give that application another certificate first." }),
    });

    const table = await screen.findByRole("region", { name: "Certificates" });
    const origin = (await within(table).findByText("origin")).closest("tr");
    if (!origin) throw new Error("no row");
    await user.click(within(origin).getByRole("button", { name: "Actions for origin" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete upload" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete origin" });
    await user.click(within(dialog).getByRole("button", { name: "Delete upload" }));

    expect(await within(dialog).findByText("origin is served by shop.example.com")).toBeInTheDocument();
    expect(backend.callsTo("DELETE /api/certs/uploaded/origin")).toHaveLength(1);
  });

  it("changes the authority to staging and says what that costs", async () => {
    const { user, backend } = certificates({
      "PUT /api/certs/acme": () => json(200, { ...ACME, ca: "letsencrypt-staging", label: "Let's Encrypt (staging)", trusted: false }),
    });

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Certificate authority" }));
    const dialog = await screen.findByRole("dialog", { name: "Certificate authority" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Authority" }));
    await user.click(await screen.findByRole("option", { name: "Let's Encrypt (staging)" }));
    expect(within(dialog).getByText(/issues certificates browsers do not trust/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/certs/acme")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/certs/acme")[0]?.body).toEqual({ ca: "letsencrypt-staging", email: "ops@example.com" });
  });

  it("asks for the external account of an authority that needs one, and never shows the key back", { timeout: 20_000 }, async () => {
    const { user, backend } = certificates({
      "PUT /api/certs/acme": () => json(200, { ...ACME, ca: "zerossl", label: "ZeroSSL", needs_eab: true, eab_kid: "kid-1", eab_configured: true }),
      "POST /api/certs/acme/register": () => json(202, { job_id: "reg-1", status: "pending", message: "queued", job: { id: "reg-1" } }),
      "GET /api/jobs/reg-1": () =>
        json(200, { id: "reg-1", type: "cert_create", name: "Register the account", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-10-12T10:00:00", logs: [], metadata: {} }),
    });

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Certificate authority" }));
    const dialog = await screen.findByRole("dialog", { name: "Certificate authority" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Authority" }));
    await user.click(await screen.findByRole("option", { name: "ZeroSSL" }));
    expect(within(dialog).getByText("This authority asks for an external account")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Key id"), "kid-1");
    const hmac = within(dialog).getByLabelText("HMAC key");
    expect(hmac).toHaveAttribute("type", "password");
    await user.type(hmac, "hmac-secret-value");
    await user.click(within(dialog).getByRole("checkbox", { name: "Register the account now" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/certs/acme")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/certs/acme")[0]?.body).toEqual({ ca: "zerossl", email: "ops@example.com", eab_kid: "kid-1", eab_hmac_key: "hmac-secret-value" });
    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/acme/register")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Certificate authority" })).not.toBeInTheDocument();
    });
    expect(document.body.textContent).not.toContain("hmac-secret-value");
  });

  it("keeps the stored key when none is typed, and says one is stored", async () => {
    const { user, backend } = certificates({
      "GET /api/certs/acme": () => json(200, { ...ACME, ca: "zerossl", label: "ZeroSSL", needs_eab: true, eab_kid: "kid-1", eab_configured: true }),
      "PUT /api/certs/acme": () => json(200, ACME),
    });

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Certificate authority" }));
    const dialog = await screen.findByRole("dialog", { name: "Certificate authority" });
    expect(await within(dialog).findByText(/A key is stored\. Leave this empty to keep it/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/certs/acme")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/certs/acme")[0]?.body).not.toHaveProperty("eab_hmac_key");
  });

  it("asks for the directory of another authority", async () => {
    const { user } = certificates();

    await user.click(await screen.findByRole("button", { name: "More actions" }));
    await user.click(await screen.findByRole("menuitem", { name: "Certificate authority" }));
    const dialog = await screen.findByRole("dialog", { name: "Certificate authority" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Authority" }));
    await user.click(await screen.findByRole("option", { name: "Another authority" }));

    expect(within(dialog).getByLabelText("Directory URL")).toBeInTheDocument();
  });
});
