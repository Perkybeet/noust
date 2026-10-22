import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { FakeBackend, RouteHandler } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";

// A dialog of five steps over four requests is slow to settle on a busy machine.
configure({ asyncUtilTimeout: 5000 });

const TOKEN = "hcloud_0123456789abcdefghijklmnopqrstuvwxyz";

const PROVIDERS = [
  { kind: "hetzner", name: "Hetzner Cloud", token_url: "https://console.hetzner.cloud/", token_help: "", capabilities: { ipv6: true, backups: true }, user_data_limit: 32768 },
  { kind: "digitalocean", name: "DigitalOcean", token_url: "https://cloud.digitalocean.com/account/api/tokens", token_help: "", capabilities: { ipv6: true }, user_data_limit: 65536 },
  { kind: "vultr", name: "Vultr", token_url: "https://my.vultr.com/settings/#settingsapi", token_help: "", capabilities: {}, user_data_limit: 16384 },
];

const ACCOUNT = {
  name: "hetzner-prod",
  provider: "hetzner",
  token_hint: "...wxyz",
  note: "Production project",
  created_at: "2026-10-01T10:00:00+00:00",
  created_by: "yago",
  verified_at: "2026-10-12T08:00:00+00:00",
  last_used_at: null,
};

const READY = {
  node: "web-5",
  account: "hetzner-prod",
  provider: "hetzner",
  provider_id: "4711",
  address: "203.0.113.9",
  location: "fsn1",
  plan: "cx22",
  image: "ubuntu-24.04",
  state: "ready",
  error: null,
  job_id: null,
  created_at: "2026-10-10T10:00:00+00:00",
  created_by: "yago",
  updated_at: "2026-10-10T10:05:00+00:00",
};

const FAILED = { ...READY, node: "web-6", provider_id: "4712", address: "203.0.113.10", state: "failed", error: "The server never answered on port 22 after 300 seconds." };

const CATALOG = {
  provider: "hetzner",
  capabilities: { ipv6: true, backups: true, ipv4_optional: true },
  locations: [
    { id: "fsn1", name: "Falkenstein", detail: "DE" },
    { id: "ash", name: "Ashburn", detail: "US" },
  ],
  plans: [
    { id: "cx22", name: "CX22", detail: "2 vCPU, 4 GB, 40 GB", locations: ["fsn1"], arch: "x86", monthly: 3.8 },
    { id: "cx32", name: "CX32", detail: "4 vCPU, 8 GB, 80 GB", locations: ["fsn1", "ash"], arch: "x86", monthly: 6.8 },
  ],
  images: [
    { id: "ubuntu-24.04", name: "Ubuntu 24.04", detail: "", archs: ["x86"] },
    { id: "debian-12", name: "Debian 12", detail: "", archs: ["x86"] },
  ],
  ssh_keys: [],
  firewalls: [],
  networks: [],
};

const SCRIPTS = [
  { name: "swap-2g", kind: "script", description: "A 2 GB swap file", builtin: true, bytes: 180 },
  { name: "my-tools", kind: "cloud-config", description: "Packages I always want", builtin: false, bytes: 64, updated_at: "2026-10-05T10:00:00+00:00" },
];

interface Setup {
  accounts?: unknown[];
  machines?: unknown[];
  extra?: Record<string, RouteHandler>;
}

function cloudBackend({ accounts = [ACCOUNT], machines = [READY, FAILED], extra = {} }: Setup = {}): FakeBackend {
  return fakeBackend({
    ...fleetRoutes(centralSession({}, { totp_enabled: true })),
    "GET /api/fleet/cloud/providers": () => json(200, PROVIDERS),
    "GET /api/fleet/cloud/accounts": () => json(200, accounts),
    "GET /api/fleet/cloud/accounts/hetzner-prod/catalog": () => json(200, CATALOG),
    "GET /api/fleet/cloud/servers": () => json(200, machines),
    "GET /api/fleet/cloud/scripts": () => json(200, SCRIPTS),
    "POST /api/fleet/cloud/scripts/preview": () => json(200, { document: "#cloud-config\nssh_pwauth: false\n", bytes: 40 }),
    ...extra,
  });
}

describe("Settings > Cloud", () => {
  it("lists the accounts without their token, the servers made here and the boot scripts, and passes axe", { timeout: 30_000 }, async () => {
    cloudBackend();
    const { container } = renderConsole("/settings/cloud");
    // The tables are found by their rows: the region that holds them is replaced when they load.
    // The account's row, and the account named in the rows of the servers made with it.
    expect((await screen.findAllByText("hetzner-prod")).length).toBeGreaterThan(0);
    // Only the last characters of a token are ever shown.
    expect(await screen.findByText("...wxyz")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(TOKEN);

    expect(await screen.findByText("In the fleet")).toBeInTheDocument();
    expect(screen.getByText("Did not join")).toBeInTheDocument();
    // Why it did not make it, as the central recorded it.
    expect(screen.getByText("The server never answered on port 22 after 300 seconds.")).toBeInTheDocument();

    expect(await screen.findByText("swap-2g")).toBeInTheDocument();
    expect(screen.getByText("Built in")).toBeInTheDocument();
    expect(screen.getByText("my-tools")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("draws no section until the servers and the accounts have both answered, so none is pushed down", { timeout: 30_000 }, async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    cloudBackend({
      accounts: [],
      machines: [],
      extra: {
        "GET /api/fleet/cloud/accounts": async () => {
          await held;
          return json(200, []);
        },
      },
    });
    renderConsole("/settings/cloud");

    // The servers' answer is in, the accounts' is not: the page is one busy region, with no empty state.
    await waitFor(() => {
      expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
    });
    expect(screen.getByText("Loading the servers and accounts")).toBeInTheDocument();
    expect(screen.queryByText("No server created here yet")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Servers created here" })).not.toBeInTheDocument();

    release();
    // All three sections at once, the servers' invitation worded for an account that is missing.
    expect(await screen.findByText("Add a provider account first, then create a server.")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Provider accounts" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Boot scripts" })).toBeInTheDocument();
  });

  it("draws the sections with what failed when the servers and the accounts cannot be read, and asks once", { timeout: 30_000 }, async () => {
    const backend = cloudBackend({
      extra: {
        "GET /api/fleet/cloud/accounts": () => problem(500, "internal_error", "The accounts file is unreadable"),
        "GET /api/fleet/cloud/servers": () => problem(500, "internal_error", "The servers file is unreadable"),
      },
    });
    renderConsole("/settings/cloud");

    expect(await screen.findByText("The accounts file is unreadable")).toBeInTheDocument();
    expect(screen.getByText("The servers file is unreadable")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Provider accounts" })).toBeInTheDocument();
    // Not an endless round of asking, which is what taking the sections away and back looked like.
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(backend.callsTo("GET /api/fleet/cloud/accounts").length).toBeLessThanOrEqual(3);
    expect(backend.callsTo("GET /api/fleet/cloud/servers").length).toBeLessThanOrEqual(3);
  });

  it("asks for an account before a server can be created", async () => {
    cloudBackend({ accounts: [], machines: [] });
    renderConsole("/settings/cloud");
    expect(await screen.findByText("No provider account yet")).toBeInTheDocument();
    expect(await screen.findByText("Add a provider account first, then create a server.")).toBeInTheDocument();
  });

  it("adds an account: the token is sent once, checked, and gone from the page", { timeout: 30_000 }, async () => {
    const backend = cloudBackend({
      accounts: [],
      machines: [],
      extra: { "POST /api/fleet/cloud/accounts": () => json(201, ACCOUNT) },
    });
    const { user } = renderConsole("/settings/cloud");
    await user.click(await screen.findByRole("button", { name: "Add an account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a provider account" });
    await user.type(within(dialog).getByLabelText(/^Name/), "hetzner-prod");
    await user.click(within(dialog).getByRole("combobox", { name: /Provider/ }));
    await user.click(await screen.findByRole("option", { name: "Hetzner Cloud" }));
    const token = within(dialog).getByLabelText("API token");
    // A token is a password: not shown unless the operator asks.
    expect(token).toHaveAttribute("type", "password");
    await user.click(token);
    await user.paste(TOKEN);
    expect(await within(dialog).findByRole("link", { name: /Create a token at Hetzner Cloud/ })).toHaveAttribute("href", "https://console.hetzner.cloud/");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/cloud/accounts")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/cloud/accounts")[0]?.body).toEqual({ name: "hetzner-prod", provider: "hetzner", token: TOKEN, note: null, verify: true });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a provider account" })).not.toBeInTheDocument();
    });
    expect(document.body).not.toHaveTextContent(TOKEN);
  });

  it("checks the account's name, the provider and the token before asking the central", { timeout: 30_000 }, async () => {
    const backend = cloudBackend({ accounts: [], machines: [] });
    const { user } = renderConsole("/settings/cloud");
    await user.click(await screen.findByRole("button", { name: "Add an account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a provider account" });
    await user.type(within(dialog).getByLabelText(/^Name/), "Hetzner Prod");
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await within(dialog).findByText(/Use 1 to 32 lower-case letters, digits and dashes, such as hetzner-prod/)).toBeInTheDocument();
    expect(within(dialog).getByText("Choose the provider this token is for.")).toBeInTheDocument();
    expect(within(dialog).getByText(/does not look like an API token/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/fleet/cloud/accounts")).toHaveLength(0);
  });

  it("shows the provider's own refusal of a token, verbatim, and keeps the dialog open", { timeout: 30_000 }, async () => {
    cloudBackend({
      accounts: [],
      machines: [],
      extra: { "POST /api/fleet/cloud/accounts": () => problem(400, "validation_error", "Hetzner refused the token: unauthorized (401)") },
    });
    const { user } = renderConsole("/settings/cloud");
    await user.click(await screen.findByRole("button", { name: "Add an account" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a provider account" });
    await user.type(within(dialog).getByLabelText(/^Name/), "hetzner-prod");
    await user.click(within(dialog).getByRole("combobox", { name: /Provider/ }));
    await user.click(await screen.findByRole("option", { name: "Hetzner Cloud" }));
    await user.click(within(dialog).getByLabelText("API token"));
    await user.paste(TOKEN);
    await user.click(within(dialog).getByRole("button", { name: "Add account" }));
    expect(await within(dialog).findByText(/Hetzner refused the token: unauthorized \(401\)/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Add a provider account" })).toBeInTheDocument();
  });

  it("creates a server in the five steps and follows the job", { timeout: 60_000 }, async () => {
    const backend = cloudBackend({
      extra: {
        "POST /api/fleet/cloud/servers": () => json(202, { job_id: "j-cloud", message: "Creating web-7", status: "pending" }),
        "GET /api/jobs/j-cloud": () =>
          json(200, {
            id: "j-cloud",
            type: "fleet_provision",
            name: "Create web-7",
            description: "",
            status: "running",
            progress: 30,
            total_steps: 100,
            current_step: "Waiting for the machine",
            created_at: "2026-10-13T10:00:00Z",
            logs: [{ message: "Created the machine at Hetzner Cloud" }],
          }),
      },
    });
    const { user } = renderConsole("/settings/cloud?create=true");
    const dialog = await screen.findByRole("dialog", { name: "Create a server" });
    await user.type(within(dialog).getByLabelText(/^Name/), "web-7");
    await user.click(within(dialog).getByRole("button", { name: "Next" }));

    // Machine: the plans are those sold where the server goes.
    await user.click(await within(dialog).findByRole("combobox", { name: "Location" }));
    await user.click(await screen.findByRole("option", { name: /Falkenstein/ }));
    await user.click(within(dialog).getByRole("combobox", { name: "Size" }));
    await user.click(await screen.findByRole("option", { name: /CX22/ }));
    // Noust's own supported release is chosen by itself.
    expect(within(dialog).getByRole("combobox", { name: "Starts from" })).toHaveValue("Ubuntu 24.04");
    await user.click(within(dialog).getByRole("button", { name: "Next" }));

    // Options: the boot scripts of the library.
    await user.click(await within(dialog).findByRole("checkbox", { name: /swap-2g/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review" }));

    // Review: what it costs is said before the button.
    expect(await within(dialog).findByText("It costs money from the moment it is created")).toBeInTheDocument();
    expect(within(dialog).getByText("web-7")).toBeInTheDocument();
    expect(within(dialog).getByText("swap-2g")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create server" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/cloud/servers")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/cloud/servers")[0]?.body).toMatchObject({
      name: "web-7",
      account: "hetzner-prod",
      location: "fsn1",
      plan: "cx22",
      image: "ubuntu-24.04",
      scripts: ["swap-2g"],
      ipv4: true,
      delete_on_failure: false,
    });
    // No credential of any kind travels with it.
    expect(JSON.stringify(backend.callsTo("POST /api/fleet/cloud/servers")[0]?.body)).not.toMatch(/token|password/i);
    expect(await within(dialog).findByText("Created the machine at Hetzner Cloud")).toBeInTheDocument();
  });

  it("will not leave the first step without a valid name", async () => {
    const backend = cloudBackend();
    const { user } = renderConsole("/settings/cloud?create=true");
    const dialog = await screen.findByRole("dialog", { name: "Create a server" });
    await user.type(within(dialog).getByLabelText(/^Name/), "Web 7");
    await user.click(within(dialog).getByRole("button", { name: "Next" }));
    expect(await within(dialog).findByText(/Use 1 to 32 lower-case letters, digits and dashes, such as web-2/)).toBeInTheDocument();
    expect(backend.callsTo("GET /api/fleet/cloud/accounts/hetzner-prod/catalog")).toHaveLength(0);
  });

  it("deletes a machine only after its name is typed, and says what it does", { timeout: 30_000 }, async () => {
    const backend = cloudBackend({
      extra: { "DELETE /api/fleet/cloud/servers/web-6": () => json(200, { node: "web-6", warnings: [] }) },
    });
    const { user } = renderConsole("/settings/cloud");
    await user.click(await screen.findByRole("button", { name: "Actions for web-6" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete the machine" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete the machine of web-6?" });
    expect(within(dialog).getByText(/destroyed at the provider/)).toBeInTheDocument();
    const action = within(dialog).getByRole("button", { name: "Delete machine" });
    expect(action).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "web-6");
    await user.click(action);
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/fleet/cloud/servers/web-6")).toHaveLength(1);
    });
    expect(backend.callsTo("DELETE /api/fleet/cloud/servers/web-6")[0]?.search.get("confirm")).toBe("web-6");
  });

  it("forgets which machine a server runs on without touching the machine", { timeout: 30_000 }, async () => {
    const backend = cloudBackend({
      extra: { "DELETE /api/fleet/cloud/servers/web-5/link": () => json(200, { node: "web-5", warnings: [] }) },
    });
    const { user } = renderConsole("/settings/cloud");
    await user.click(await screen.findByRole("button", { name: "Actions for web-5" }));
    await user.click(await screen.findByRole("menuitem", { name: "Forget which machine it runs on" }));

    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/fleet/cloud/servers/web-5/link")).toHaveLength(1);
    });
    expect(await screen.findByText("web-5 is no longer linked to a machine")).toBeInTheDocument();
    // The machine itself is deleted only by the other action, after its name is typed.
    expect(backend.callsTo("DELETE /api/fleet/cloud/servers/web-5")).toHaveLength(0);
  });

  it("says in Spanish what it says in English", { timeout: 30_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    cloudBackend();
    const { container } = renderConsole("/settings/cloud");
    expect((await screen.findAllByText("hetzner-prod")).length).toBeGreaterThan(0);
    expect(await screen.findByText("En la flota")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });
});
