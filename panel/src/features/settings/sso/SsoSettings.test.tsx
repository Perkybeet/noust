import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const PROVIDER = {
  name: "corp",
  label: "Acme SSO",
  issuer: "https://sso.acme.test/realms/acme",
  client_id: "noust",
  client_secret_set: true,
  enabled: true,
  scopes: "openid email profile",
  provisioning: "invite",
  default_role: "viewer",
  default_team: null,
  default_team_role: null,
  link_by_email: false,
  require_email_verified: true,
  assume_email_verified: false,
  allowed_domains: ["acme.test"],
  require_amr: [],
  groups_claim: "groups",
  group_sync: true,
  group_mappings: [
    { group: "ops", role: "admin", team: null, team_role: null },
    { group: "devs", role: null, team: "acme", team_role: "operator" },
  ],
  clock_skew_seconds: 60,
  redirect_uri: "https://noust.acme.test/api/auth/oidc/callback",
  created_at: 1_790_000_000,
  updated_at: 1_790_000_000,
  created_by: "ana",
  updated_by: null,
};
const REDIRECT = "https://noust.acme.test/api/auth/oidc/callback";

const IDENTITY = {
  id: 7,
  provider: "corp",
  issuer: PROVIDER.issuer,
  subject: "00u1abcdEFG",
  account: "maria",
  email: "maria@acme.test",
  created_at: 1_790_000_000,
  last_login_at: 1_790_100_000,
};

function account(username: string, role: string, extra: Record<string, unknown> = {}) {
  return {
    id: 1,
    username,
    display_name: username,
    role,
    status: "active",
    mfa_enabled: true,
    passkeys: 0,
    backup_codes_remaining: 8,
    created_at: 1_790_000_000,
    failures_since_login: 0,
    ...extra,
  };
}

interface Fixture {
  providers: unknown[];
  identities: unknown[];
  local: { enabled: boolean; exempt: string[]; providers: number; survivors: string[] };
}

/** A server with its sign-in settings, which the routes below read and change as the real one does. */
function ssoBackend(initial: Partial<Fixture> = {}, extra: Record<string, RouteHandler> = {}) {
  const state: Fixture = {
    providers: [PROVIDER],
    identities: [IDENTITY],
    local: { enabled: true, exempt: [], providers: 1, survivors: ["ana"] },
    ...initial,
  };
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/oidc/config": () => json(200, { providers: state.providers, redirect_uri: REDIRECT }),
    "GET /api/auth/oidc/identities": () => json(200, { identities: state.identities }),
    "GET /api/auth/oidc/local-login": () => json(200, state.local),
    "PUT /api/auth/oidc/local-login": (call: RecordedCall) => {
      const body = call.body as { enabled?: boolean; exempt?: string[] };
      state.local = { ...state.local, enabled: body.enabled ?? state.local.enabled, exempt: body.exempt ?? state.local.exempt };
      return json(200, state.local);
    },
    "GET /api/auth/accounts": () =>
      json(200, { accounts: [account("ana", "admin"), account("bruno", "operator"), account("carla", "security")], conflicts: [] }),
    "GET /api/teams/mine": () => json(200, { teams: [{ id: 1, slug: "acme", name: "Acme", role: "owner", created_at: 1 }], active: null }),
    ...extra,
  });
  return { backend, state };
}

async function pageAt(initial: Partial<Fixture> = {}, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const world = ssoBackend(initial, extra);
  const harness = renderConsole("/settings/sso");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, ...world };
}

describe("Settings > Single sign-on", () => {
  it("lists the providers with their state, the address to register and who is linked, and passes axe", { timeout: 30_000 }, async () => {
    await pageAt();
    // The skeleton has the table's name too: wait for the provider, then take the real table.
    await screen.findByText("Acme SSO");
    const table = screen.getByRole("region", { name: "Configured providers" });
    const row = within(table).getAllByRole("row")[1];
    if (!row) throw new Error("no provider row");
    expect(within(row).getByText("Acme SSO")).toBeInTheDocument();
    expect(within(row).getByText("Enabled")).toHaveAttribute("data-state", "running");
    expect(within(row).getByText("https://sso.acme.test/realms/acme")).toBeInTheDocument();
    expect(screen.getByText(REDIRECT)).toBeInTheDocument();
    const people = await screen.findByRole("region", { name: "People linked to identity providers" });
    expect(within(people).getByText("maria")).toBeInTheDocument();
    expect(within(people).getByText("00u1abcdEFG")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is in the settings navigation, as the central's section", async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Single sign-on" })).toHaveAttribute("aria-current", "page");
  });

  it("offers to add the first provider when there is none", async () => {
    await pageAt({ providers: [], identities: [], local: { enabled: true, exempt: [], providers: 0, survivors: ["ana"] } });
    expect(await screen.findByRole("heading", { name: "No identity provider yet" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add a provider" })).toBeInTheDocument();
    // With none, turning passwords off would lock everybody out: it is not offered, and says why.
    expect(screen.getByRole("button", { name: "Turn off…" })).toBeDisabled();
    expect(screen.getByText(/Add a provider and test it first/)).toBeInTheDocument();
  });

  it("adds a provider with the settings typed, the secret apart, and reads the list again", { timeout: 30_000 }, async () => {
    const { backend, state, user } = await pageAt({ providers: [], identities: [], local: { enabled: true, exempt: [], providers: 0, survivors: ["ana"] } });
    backend.on("PUT /api/auth/oidc/config/entra", (call: RecordedCall) => {
      const body = call.body as Record<string, unknown>;
      state.providers = [{ ...PROVIDER, name: "entra", label: body["label"], issuer: body["issuer"], client_id: body["client_id"], provisioning: body["provisioning"] }];
      return json(200, state.providers[0]);
    });
    await user.click(await screen.findByRole("button", { name: "Add a provider" }));
    const drawer = await screen.findByRole("dialog", { name: "Add an identity provider" });
    await user.type(within(drawer).getByLabelText(/^Name/), "entra");
    await user.type(within(drawer).getByLabelText(/^Button label/), "Entra ID");
    await user.type(within(drawer).getByLabelText(/^Issuer/), "https://login.example.test/tenant/v2.0");
    await user.type(within(drawer).getByLabelText(/^Client ID/), "app-1234");
    await user.type(within(drawer).getByLabelText(/^Client secret/), "s3cret-value");
    await user.click(within(drawer).getByRole("button", { name: "Add provider" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/auth/oidc/config/entra")).toHaveLength(1);
    });
    const body = backend.callsTo("PUT /api/auth/oidc/config/entra")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({
      label: "Entra ID",
      issuer: "https://login.example.test/tenant/v2.0",
      client_id: "app-1234",
      client_secret: "s3cret-value",
      provisioning: "off",
      require_email_verified: true,
      clear_secret: false,
    });
    expect(await screen.findByText("Entra ID")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Add an identity provider" })).not.toBeInTheDocument();
  });

  it("shows the server's own words beside the field it refused", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, {});
    backend.on("PUT /api/auth/oidc/config/corp", () =>
      problem(422, "validation_error", "Invalid", { fields: { issuer: "The issuer must use HTTPS, or be a provider on this machine." } }),
    );
    await user.click(await screen.findByRole("button", { name: "Edit corp" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit corp" });
    expect(within(drawer).getByLabelText(/^Issuer/)).toHaveValue("https://sso.acme.test/realms/acme");
    await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
    expect((await within(drawer).findAllByText(/must use HTTPS/)).length).toBeGreaterThan(0);
  });

  it("keeps the stored secret unless one is typed, and reads group mappings back as lines", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "PUT /api/auth/oidc/config/corp": () => json(200, PROVIDER) });
    await user.click(await screen.findByRole("button", { name: "Edit corp" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit corp" });
    expect(within(drawer).getByText("A secret is stored. Leave this empty to keep it.")).toBeInTheDocument();
    expect(within(drawer).getByLabelText(/^What each group means/)).toHaveValue("ops=admin\ndevs=acme:operator");
    await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/auth/oidc/config/corp")).toHaveLength(1);
    });
    const body = backend.callsTo("PUT /api/auth/oidc/config/corp")[0]?.body as Record<string, unknown>;
    expect(body).not.toHaveProperty("client_secret");
    expect(body["group_mappings"]).toEqual([
      { group: "ops", role: "admin" },
      { group: "devs", team: "acme", team_role: "operator" },
    ]);
    expect(body["allowed_domains"]).toEqual(["acme.test"]);
  });

  it("says which mappings line is wrong instead of sending it", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt();
    await user.click(await screen.findByRole("button", { name: "Edit corp" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit corp" });
    const box = within(drawer).getByLabelText(/^What each group means/);
    await user.clear(box);
    await user.type(box, "ops=admin{enter}nonsense");
    await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
    expect(await within(drawer).findByText("Line 2 is not a mapping. Write GROUP=ROLE or GROUP=TEAM:ROLE.")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/auth/oidc/config/corp")).toHaveLength(0);
  });

  it("tests a provider and shows each check as the provider answered it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt(
      {},
      {
        "POST /api/auth/oidc/config/corp/test": () =>
          json(200, {
            ok: false,
            checks: [
              { name: "discovery", ok: true, detail: "Read https://sso.acme.test/realms/acme/.well-known/openid-configuration" },
              { name: "keys", ok: false, detail: "HTTP 503 from https://sso.acme.test/realms/acme/protocol/openid-connect/certs" },
            ],
          }),
      },
    );
    await user.click(await screen.findByRole("button", { name: "Test corp" }));
    const dialog = await screen.findByRole("dialog", { name: "Test of corp" });
    expect(within(dialog).getByText("The provider did not answer as expected.")).toBeInTheDocument();
    expect(within(dialog).getByText("HTTP 503 from https://sso.acme.test/realms/acme/protocol/openid-connect/certs")).toBeInTheDocument();
    expect(within(dialog).getByText("Passed")).toHaveAttribute("data-state", "running");
    expect(within(dialog).getByText("Failed")).toHaveAttribute("data-state", "failed");
  });

  it("removes a provider after asking, and reads the list again", { timeout: 30_000 }, async () => {
    const { backend, state, user } = await pageAt(
      {},
      {
        "DELETE /api/auth/oidc/config/corp": () => json(200, { success: true, removed: "corp" }),
      },
    );
    await user.click(await screen.findByRole("button", { name: "Remove corp" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove corp" });
    state.providers = [];
    await user.click(within(dialog).getByRole("button", { name: "Remove provider" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/auth/oidc/config/corp")).toHaveLength(1);
    });
    expect(await screen.findByRole("heading", { name: "No identity provider yet" })).toBeInTheDocument();
  });

  describe("passwords and passkeys", () => {
    it("turns them off keeping the password of the accounts chosen, and then says who keeps it", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt();
      expect(await screen.findByText("Passwords and passkeys sign accounts in")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Turn off…" }));
      const dialog = await screen.findByRole("dialog", { name: "Turn off passwords and passkeys" });
      // The accounts that can administer first.
      const boxes = within(dialog).getAllByRole("checkbox");
      expect(boxes.map((box) => box.getAttribute("aria-label") ?? box.closest("label")?.textContent ?? "")).toHaveLength(3);
      await user.click(within(dialog).getByRole("checkbox", { name: /ana/ }));
      await user.click(within(dialog).getByRole("button", { name: "Turn off passwords and passkeys" }));

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/oidc/local-login")).toHaveLength(1);
      });
      expect(backend.callsTo("PUT /api/auth/oidc/local-login")[0]?.body).toEqual({ enabled: false, exempt: ["ana"] });
      expect(await screen.findByText("Only the identity provider signs accounts in")).toBeInTheDocument();
      const keepers = screen.getByRole("group", { name: "Accounts that keep their password" });
      expect(within(keepers).getByText("ana")).toBeInTheDocument();
    });

    it("shows the server's refusal as it wrote it and leaves the dialog open", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        {},
        {
          "PUT /api/auth/oidc/local-login": () =>
            problem(400, "configerror", "That would leave nobody able to administer without a password.", { hint: "Keep the password of at least one administrator." }),
        },
      );
      await user.click(await screen.findByRole("button", { name: "Turn off…" }));
      const dialog = await screen.findByRole("dialog", { name: "Turn off passwords and passkeys" });
      await user.click(within(dialog).getByRole("button", { name: "Turn off passwords and passkeys" }));
      expect(await within(dialog).findByText("That would leave nobody able to administer without a password.")).toBeInTheDocument();
      expect(within(dialog).getByText("Keep the password of at least one administrator.")).toBeInTheDocument();
    });

    it("says who keeps a password and who could administer, and turns them on again", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt({ local: { enabled: false, exempt: ["ana"], providers: 1, survivors: ["ana", "carla"] } });
      expect(await screen.findByText("Only the identity provider signs accounts in")).toBeInTheDocument();
      const survivors = screen.getByRole("group", { name: "Could still administer without the provider" });
      expect(within(survivors).getByText("carla")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Turn on" }));
      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/oidc/local-login")).toHaveLength(1);
      });
      expect(backend.callsTo("PUT /api/auth/oidc/local-login")[0]?.body).toEqual({ enabled: true });
      expect(await screen.findByText("Passwords and passkeys sign accounts in")).toBeInTheDocument();
    });
  });

  describe("people linked to a provider", () => {
    it("unlinks a person after asking", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt({}, { "DELETE /api/auth/oidc/identities/7": () => json(200, { success: true, removed: "maria" }) });
      await user.click(await screen.findByRole("button", { name: "Unlink maria from corp" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Unlink maria" });
      state.identities = [];
      await user.click(within(dialog).getByRole("button", { name: "Unlink person" }));
      await waitFor(() => {
        expect(backend.callsTo("DELETE /api/auth/oidc/identities/7")).toHaveLength(1);
      });
      expect(await screen.findByText("Nobody is linked yet")).toBeInTheDocument();
    });

    it("links a person by hand to an account", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt(
        {},
        { "POST /api/auth/oidc/identities": (call: RecordedCall) => json(201, { ...IDENTITY, id: 8, account: (call.body as { username: string }).username }) },
      );
      await user.click(await screen.findByRole("button", { name: "Link a person…" }));
      const dialog = await screen.findByRole("dialog", { name: "Link a person to an account" });
      await user.click(within(dialog).getByRole("combobox", { name: "Account" }));
      await user.click(await screen.findByRole("option", { name: "bruno" }));
      await user.click(within(dialog).getByRole("combobox", { name: "Provider" }));
      await user.click(await screen.findByRole("option", { name: "Acme SSO" }));
      await user.type(within(dialog).getByLabelText(/^Subject/), "00uZZZ");
      await user.click(within(dialog).getByRole("button", { name: "Link person" }));
      await waitFor(() => {
        expect(backend.callsTo("POST /api/auth/oidc/identities")).toHaveLength(1);
      });
      expect(backend.callsTo("POST /api/auth/oidc/identities")[0]?.body).toEqual({ username: "bruno", provider: "corp", subject: "00uZZZ" });
    });
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Proveedores configurados" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Probar corp" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
