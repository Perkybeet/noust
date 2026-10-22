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

const FINGERPRINT = "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99";
const ROOT = "-----BEGIN CERTIFICATE-----\nTUlJQkRPT1Q=\n-----END CERTIFICATE-----\n";

const AUTHORITY = {
  subject: "CN = Noust internal CA (nas)",
  fingerprint: FINGERPRINT,
  not_before: "2026-10-01T00:00:00+00:00",
  not_after: "2036-09-29T00:00:00+00:00",
  days_left: 3640,
  permitted_dns: ["lan", "home.arpa"],
  permitted_networks: ["10.0.0.0/8"],
  issued: 1,
  warnings: [],
};

const ISSUED = {
  name: "internal-wiki",
  names: ["wiki.home.arpa"],
  not_after: "2027-01-10T00:00:00+00:00",
  days_left: 84,
  fingerprint: "11:22",
  apps: ["wiki.home.arpa"],
  expiring: false,
};

const NONE = { authority: null, issued: [], root_pem: null, trusted_here: false, system_store: "Debian, Ubuntu and Alpine", default_dns: ["lan"], default_networks: ["10.0.0.0/8"] };
const MADE = { authority: AUTHORITY, issued: [ISSUED], root_pem: ROOT, trusted_here: false, system_store: "Debian, Ubuntu and Alpine", default_dns: ["lan"], default_networks: ["10.0.0.0/8"] };

const GUIDES = {
  fingerprint: FINGERPRINT,
  guides: [
    { id: "debian", title: "Debian, Ubuntu and their derivatives", file: "pem", steps: ["$ sudo cp ca.crt /usr/local/share/ca-certificates/noust-nas.crt", "$ sudo update-ca-certificates", `Compare the fingerprint it prints with ${FINGERPRINT}`], note: "curl, wget, git and most programs read this store." },
    { id: "windows", title: "Windows", file: "der", steps: ["PS> Import-Certificate -FilePath .\\ca.cer -CertStoreLocation Cert:\\LocalMachine\\Root"], note: "" },
  ],
};

function page(routes: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/sites": () => json(200, { sites: [], total: 0, webserver: "nginx" }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    "GET /api/certs/ca": () => json(200, MADE),
    "GET /api/certs/ca/trust": () => json(200, GUIDES),
    ...routes,
  });
  const harness = renderConsole("/domains");
  return { ...harness, backend };
}

async function open(user: ReturnType<typeof page>["user"]) {
  await user.click(await screen.findByRole("button", { name: "More actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Private authority" }));
  return screen.findByRole("dialog", { name: "Private certificate authority" });
}

describe("the private certificate authority", () => {
  it("offers to make one when the server has none, and sends what the operator permits", { timeout: 20_000 }, async () => {
    let made = false;
    const { user, backend } = page({
      "GET /api/certs/ca": () => json(200, made ? MADE : NONE),
      "POST /api/certs/ca": () => {
        made = true;
        return json(201, AUTHORITY);
      },
    });

    const dialog = await open(user);
    expect(await within(dialog).findByText("This server has no private authority yet")).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/More domains it may sign for/), "corp.example.com, intranet.lan");
    await user.type(within(dialog).getByLabelText(/More private address ranges/), "10.20.0.0/16");
    await user.click(within(dialog).getByRole("checkbox", { name: "Trust it on this server too" }));
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create authority" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/ca")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/certs/ca")[0]?.body).toEqual({
      days: 3650,
      permit_dns: ["corp.example.com", "intranet.lan"],
      permit_networks: ["10.20.0.0/16"],
      only: false,
      trust_here: true,
    });
    expect(await within(dialog).findByText(FINGERPRINT)).toBeInTheDocument();
  });

  it("shows why the server refused a suffix, beside the field", async () => {
    const { user } = page({
      "GET /api/certs/ca": () => json(200, NONE),
      "POST /api/certs/ca": () => problem(400, "validation_error", "'com' is a top-level domain", { fields: { permit: "'com' is a top-level domain: the authority would sign for every name under it" } }),
    });

    const dialog = await open(user);
    await user.type(await within(dialog).findByLabelText(/More domains it may sign for/), "com");
    await user.click(within(dialog).getByRole("button", { name: "Create authority" }));

    expect(await within(dialog).findByText(/top-level domain: the authority would sign for every name under it/)).toBeInTheDocument();
  });

  it("shows the authority, what it signs for and what it signed, and never a key", { timeout: 20_000 }, async () => {
    const { user } = page();

    const dialog = await open(user);
    expect(await within(dialog).findByText(FINGERPRINT)).toBeInTheDocument();
    expect(within(dialog).getByText("lan, home.arpa")).toBeInTheDocument();
    expect(within(dialog).getByText("10.0.0.0/8")).toBeInTheDocument();
    expect(within(dialog).getByText("internal-wiki")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Sign internal-wiki again" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("PRIVATE KEY");
    await expectNoAxeViolations(dialog);
  });

  it("warns, in the server's words, that the authority is about to expire", async () => {
    const { user } = page({
      "GET /api/certs/ca": () => json(200, { ...MADE, authority: { ...AUTHORITY, days_left: 40, warnings: ["The authority expires in 40 days. A new root has to be trusted again on every device."] } }),
    });

    const dialog = await open(user);

    expect(await within(dialog).findByText("The authority expires in 40 days. A new root has to be trusted again on every device.")).toBeInTheDocument();
  });

  it("says how to trust it on a device the operator chooses, with the fingerprint to compare", { timeout: 20_000 }, async () => {
    const { user } = page();

    const dialog = await open(user);
    await user.click(await within(dialog).findByRole("combobox", { name: "Device" }));
    await user.click(await screen.findByRole("option", { name: "Windows" }));

    expect(await within(dialog).findByText("File to carry: ca.cer")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("combobox", { name: "Device" }));
    await user.click(await screen.findByRole("option", { name: /Debian/ }));
    expect(await within(dialog).findByText("sudo update-ca-certificates")).toBeInTheDocument();
    expect(within(dialog).getByText(`Compare the fingerprint it prints with ${FINGERPRINT}`)).toBeInTheDocument();
  });

  it("trusts it on this server, and stops", async () => {
    const { user, backend } = page({
      "POST /api/certs/ca/trust-here": () => json(200, { ...MADE, trusted_here: true }),
      "DELETE /api/certs/ca/trust-here": () => json(200, MADE),
    });

    const dialog = await open(user);
    await user.click(await within(dialog).findByRole("button", { name: "Trust it on this server" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/ca/trust-here")).toHaveLength(1);
    });
    await user.click(await within(dialog).findByRole("button", { name: "Stop trusting it here" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/certs/ca/trust-here")).toHaveLength(1);
    });
  });

  it("does not offer to trust it on a system whose store Noust cannot update", async () => {
    const { user } = page({ "GET /api/certs/ca": () => json(200, { ...MADE, system_store: null }) });

    const dialog = await open(user);

    expect(await within(dialog).findByText(/no store of authorities Noust can update/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Trust it on this server" })).not.toBeInTheDocument();
  });

  it("signs a certificate for the names typed, and signs one again", { timeout: 20_000 }, async () => {
    const { user, backend } = page({
      "POST /api/certs/ca/issued": () => json(201, { ...ISSUED, name: "internal-grafana", names: ["grafana.lan"] }),
      "POST /api/certs/ca/issued/internal-wiki/renew": () => json(200, ISSUED),
    });

    const dialog = await open(user);
    await user.click(await within(dialog).findByRole("button", { name: "Sign internal-wiki again" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/ca/issued/internal-wiki/renew")).toHaveLength(1);
    });
    await user.type(within(dialog).getByLabelText("What it is for"), "grafana");
    await user.type(within(dialog).getByLabelText("Names"), "grafana.lan, 10.0.0.9");
    await user.click(within(dialog).getByRole("button", { name: "Sign" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/certs/ca/issued")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/certs/ca/issued")[0]?.body).toEqual({ name: "grafana", names: ["grafana.lan", "10.0.0.9"], days: 90 });
  });

  it("shows why a name was refused beside the names", async () => {
    const { user } = page({
      "POST /api/certs/ca/issued": () => problem(400, "validation_error", "www.bank.com is outside what this authority signs for", { fields: { names: "www.bank.com is outside what this authority signs for" } }),
    });

    const dialog = await open(user);
    await user.type(await within(dialog).findByLabelText("What it is for"), "bank");
    await user.type(within(dialog).getByLabelText("Names"), "www.bank.com");
    await user.click(within(dialog).getByRole("button", { name: "Sign" }));

    expect(await within(dialog).findByText(/www\.bank\.com is outside what this authority signs for/)).toBeInTheDocument();
  });

  it("deletes the authority only after asking, and says certificates it signed need force", { timeout: 20_000 }, async () => {
    const { user, backend } = page({ "DELETE /api/certs/ca": () => new Response(null, { status: 204 }) });

    const dialog = await open(user);
    await user.click(await within(dialog).findByRole("button", { name: "Delete the authority" }));
    const sure = await screen.findByRole("alertdialog", { name: "Delete the private authority" });
    await user.click(within(sure).getByRole("checkbox", { name: "Delete it although certificates it signed are kept" }));
    await user.click(within(sure).getByRole("button", { name: "Delete the authority" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/certs/ca")).toHaveLength(1);
    });
    expect(backend.callsTo("DELETE /api/certs/ca")[0]?.search.get("force")).toBe("true");
  });
});
