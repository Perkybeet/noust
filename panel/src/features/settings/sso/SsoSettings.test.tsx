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

const SCIM_URL = "https://noust.acme.test/api/scim/v2";
const SCIM_SOURCE = {
  name: "entra",
  label: "Entra ID",
  enabled: true,
  token_hint: "k3Yz",
  token_created_at: 1_790_000_000,
  last_used_at: null,
  last_used_ip: null,
  default_role: "viewer",
  default_team: null,
  default_team_role: null,
  link_by_email: false,
  allowed_domains: ["acme.test"],
  group_sync: true,
  group_mappings: [{ group: "release", role: null, team: "acme", team_role: null, bind: "release-manager", project: "web", environment: null }],
  users: 12,
  created_at: 1_790_000_000,
  created_by: "ana",
};

const DIRECTORY = {
  name: "ad",
  label: "Acme directory",
  enabled: true,
  host: "ldap.acme.test",
  port: 636,
  tls: "ldaps",
  ca_file: null,
  bind_dn: "cn=noust,ou=system,dc=acme,dc=test",
  bind_password_set: true,
  user_base: "ou=people,dc=acme,dc=test",
  user_filter: "(&(objectClass=person)(uid={username}))",
  subject_attribute: "entryUUID",
  email_attribute: "mail",
  name_attribute: "displayName",
  username_attribute: "uid",
  groups_attribute: "memberOf",
  group_base: "",
  group_filter: "",
  timeout_seconds: 10,
  provisioning: "invite",
  default_role: "viewer",
  default_team: null,
  default_team_role: null,
  link_by_email: false,
  allowed_domains: [],
  require_email_verified: false,
  assume_email_verified: true,
  group_sync: true,
  group_mappings: [{ group: "cn=ops,ou=groups,dc=acme,dc=test", role: "admin", team: null, team_role: null, bind: null, project: null, environment: null }],
  created_at: 1_790_000_000,
  created_by: "ana",
};

const SAML_PEM = "-----BEGIN CERTIFICATE-----\nMIICvDCCAaSgAwIBAgIU\n-----END CERTIFICATE-----";
const SAML_PROVIDER = {
  name: "entra",
  label: "Entra ID",
  enabled: true,
  idp_entity_id: "https://sts.windows.net/tenant/",
  sso_url: "https://login.microsoftonline.com/tenant/saml2",
  certificates: [SAML_PEM],
  certificate_details: [{ subject: "CN=Microsoft Azure Federated SSO Certificate", fingerprint: "ab".repeat(32), not_after: 4_000_000_000 }],
  sp_entity_id: "",
  entity_id: "https://noust.acme.test/api/auth/saml/metadata/entra",
  acs_url: "https://noust.acme.test/api/auth/saml/acs",
  metadata_url: "https://noust.acme.test/api/auth/saml/metadata/entra",
  subject_attribute: "",
  email_attribute: "email",
  name_attribute: "displayName",
  username_attribute: "",
  groups_attribute: "groups",
  clock_skew_seconds: 60,
  provisioning: "invite",
  default_role: "viewer",
  default_team: null,
  default_team_role: null,
  link_by_email: false,
  allowed_domains: ["acme.test"],
  require_email_verified: false,
  assume_email_verified: true,
  require_amr: [],
  group_sync: true,
  group_mappings: [{ group: "ops", role: "operator", team: null, team_role: null, bind: null, project: null, environment: null }],
  created_at: 1_790_000_000,
  created_by: "ana",
  updated_at: 1_790_000_000,
  updated_by: null,
};

interface Fixture {
  providers: unknown[];
  identities: unknown[];
  scim: unknown[];
  directories: unknown[];
  saml: unknown[];
  secure: boolean;
  local: { enabled: boolean; exempt: string[]; providers: number; survivors: string[] };
}

/** A server with its sign-in settings, which the routes below read and change as the real one does. */
function ssoBackend(initial: Partial<Fixture> = {}, extra: Record<string, RouteHandler> = {}) {
  const state: Fixture = {
    providers: [PROVIDER],
    identities: [IDENTITY],
    scim: [],
    directories: [],
    saml: [],
    secure: true,
    local: { enabled: true, exempt: [], providers: 1, survivors: ["ana"] },
    ...initial,
  };
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/oidc/config": () => json(200, { providers: state.providers, redirect_uri: REDIRECT }),
    "GET /api/auth/oidc/identities": () => json(200, { identities: state.identities }),
    "GET /api/auth/scim/sources": () => json(200, { base_url: SCIM_URL, sources: state.scim }),
    "GET /api/auth/ldap/directories": () => json(200, { directories: state.directories }),
    "GET /api/auth/saml/config": () =>
      json(200, { providers: state.saml, acs_url: "https://noust.acme.test/api/auth/saml/acs", secure: state.secure }),
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

type Pointer = ReturnType<typeof renderConsole>["user"];

/** Adds a connection of one of the three kinds from the one menu. */
async function addConnection(user: Pointer, kind: "OpenID Connect provider" | "SAML provider" | "Directory"): Promise<void> {
  await user.click(await screen.findByRole("button", { name: "Add a connection" }));
  await user.click(await screen.findByRole("menuitem", { name: kind }));
}

/** One of a row's actions, from the menu at its end. */
async function rowAction(user: Pointer, name: string, action: "Test" | "Edit" | "Remove"): Promise<void> {
  await user.click(await screen.findByRole("button", { name: `Actions for ${name}` }));
  await user.click(await screen.findByRole("menuitem", { name: action }));
}

/** The row of the connections table that holds a text. */
function rowOf(text: string): HTMLElement {
  const row = within(screen.getByRole("region", { name: "Sign-in connections" })).getByText(text).closest("tr");
  if (!row) throw new Error(`no connection row holds ${text}`);
  return row;
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
    const row = rowOf("Acme SSO");
    expect(within(row).getByText("OpenID Connect")).toBeInTheDocument();
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

  it("offers to add the first connection, of three kinds, when there is none", async () => {
    const { user } = await pageAt({ providers: [], identities: [], local: { enabled: true, exempt: [], providers: 0, survivors: ["ana"] } });
    expect(await screen.findByRole("heading", { name: "No sign-in connection yet" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add a connection" }));
    expect(await screen.findByRole("menuitem", { name: "OpenID Connect provider" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "SAML provider" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Directory" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    // With none, turning passwords off would lock everybody out: it is not offered, and says why.
    expect(screen.getByRole("switch", { name: "Sign in with passwords and passkeys" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText(/Add a provider and test it first/)).toBeInTheDocument();
  });

  it("adds a provider with the settings typed, the secret apart, and reads the list again", { timeout: 30_000 }, async () => {
    const { backend, state, user } = await pageAt({ providers: [], identities: [], local: { enabled: true, exempt: [], providers: 0, survivors: ["ana"] } });
    backend.on("PUT /api/auth/oidc/config/entra", (call: RecordedCall) => {
      const body = call.body as Record<string, unknown>;
      state.providers = [{ ...PROVIDER, name: "entra", label: body["label"], issuer: body["issuer"], client_id: body["client_id"], provisioning: body["provisioning"] }];
      return json(200, state.providers[0]);
    });
    await addConnection(user, "OpenID Connect provider");
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

  it("adds a GitHub provider with only the OAuth App's client id and secret", { timeout: 30_000 }, async () => {
    const { backend, state, user } = await pageAt({ providers: [], identities: [], local: { enabled: true, exempt: [], providers: 0, survivors: ["ana"] } });
    backend.on("PUT /api/auth/oidc/config/github", (call: RecordedCall) => {
      const body = call.body as Record<string, unknown>;
      state.providers = [{ ...PROVIDER, name: "github", label: body["label"], kind: "github", issuer: "https://github.com", client_id: body["client_id"] }];
      return json(200, state.providers[0]);
    });
    await addConnection(user, "OpenID Connect provider");
    const drawer = await screen.findByRole("dialog", { name: "Add an identity provider" });
    await user.click(within(drawer).getByRole("radio", { name: /^GitHub/ }));
    // Nothing OpenID Connect about it: no issuer to type, and no scopes to choose.
    expect(within(drawer).getByLabelText(/^GitHub Enterprise Server/)).toHaveValue("");
    expect(within(drawer).queryByLabelText(/^Issuer/)).not.toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Add provider" })).toBeDisabled();
    await user.type(within(drawer).getByLabelText(/^Name/), "github");
    await user.type(within(drawer).getByLabelText(/^Client ID/), "Iv1.abc123");
    await user.type(within(drawer).getByLabelText(/^Client secret/), "s3cret-value");
    await user.click(within(drawer).getByRole("button", { name: "Add provider" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/auth/oidc/config/github")).toHaveLength(1);
    });
    const body = backend.callsTo("PUT /api/auth/oidc/config/github")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({ kind: "github", client_id: "Iv1.abc123", client_secret: "s3cret-value" });
    expect(body).not.toHaveProperty("issuer");
    expect(body).not.toHaveProperty("scopes");
    expect(await screen.findByText("https://github.com")).toBeInTheDocument();
    expect(screen.getAllByText("GitHub").length).toBeGreaterThan(0);
  });

  it("shows the server's own words beside the field it refused", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, {});
    backend.on("PUT /api/auth/oidc/config/corp", () =>
      problem(422, "validation_error", "Invalid", { fields: { issuer: "The issuer must use HTTPS, or be a provider on this machine." } }),
    );
    await rowAction(user, "corp", "Edit");
    const drawer = await screen.findByRole("dialog", { name: "Edit corp" });
    expect(within(drawer).getByLabelText(/^Issuer/)).toHaveValue("https://sso.acme.test/realms/acme");
    await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
    expect((await within(drawer).findAllByText(/must use HTTPS/)).length).toBeGreaterThan(0);
  });

  it("keeps the stored secret unless one is typed, and reads group mappings back as lines", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt({}, { "PUT /api/auth/oidc/config/corp": () => json(200, PROVIDER) });
    await rowAction(user, "corp", "Edit");
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
    await rowAction(user, "corp", "Edit");
    const drawer = await screen.findByRole("dialog", { name: "Edit corp" });
    const box = within(drawer).getByLabelText(/^What each group means/);
    await user.clear(box);
    await user.type(box, "ops=admin{enter}nonsense");
    await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
    expect(await within(drawer).findByText("Line 2 is not a mapping. Write GROUP=ROLE, GROUP=TEAM:ROLE or GROUP=ROLE@TEAM/PROJECT.")).toBeInTheDocument();
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
    await rowAction(user, "corp", "Test");
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
    await rowAction(user, "corp", "Remove");
    const dialog = await screen.findByRole("alertdialog", { name: "Remove corp" });
    state.providers = [];
    await user.click(within(dialog).getByRole("button", { name: "Remove provider" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/auth/oidc/config/corp")).toHaveLength(1);
    });
    expect(await screen.findByRole("heading", { name: "No sign-in connection yet" })).toBeInTheDocument();
  });

  describe("passwords and passkeys", () => {
    it("turns them off keeping the password of the accounts chosen, and then says who keeps it", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt();
      expect(await screen.findByText("On", { selector: "[data-state]" })).toHaveAttribute("data-state", "running");
      await user.click(screen.getByRole("switch", { name: "Sign in with passwords and passkeys" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Turn off passwords and passkeys" });
      // The accounts that can administer first.
      const boxes = within(dialog).getAllByRole("checkbox");
      expect(boxes.map((box) => box.getAttribute("aria-label") ?? box.closest("label")?.textContent ?? "")).toHaveLength(3);
      await user.click(within(dialog).getByRole("checkbox", { name: /ana/ }));
      await user.click(within(dialog).getByRole("button", { name: "Turn off passwords and passkeys" }));

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/oidc/local-login")).toHaveLength(1);
      });
      expect(backend.callsTo("PUT /api/auth/oidc/local-login")[0]?.body).toEqual({ enabled: false, exempt: ["ana"] });
      expect(await screen.findByText("Off", { selector: "[data-state]" })).toHaveAttribute("data-state", "stopped");
      expect(screen.getByText("Accounts that keep their password")).toBeInTheDocument();
      expect(screen.getAllByText("ana").length).toBeGreaterThan(0);
    });

    it("shows the server's refusal as it wrote it and leaves the dialog open", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        {},
        {
          "PUT /api/auth/oidc/local-login": () =>
            problem(400, "configerror", "That would leave nobody able to administer without a password.", { hint: "Keep the password of at least one administrator." }),
        },
      );
      await user.click(await screen.findByRole("switch", { name: "Sign in with passwords and passkeys" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Turn off passwords and passkeys" });
      await user.click(within(dialog).getByRole("button", { name: "Turn off passwords and passkeys" }));
      expect(await within(dialog).findByText("That would leave nobody able to administer without a password.")).toBeInTheDocument();
      expect(within(dialog).getByText("Keep the password of at least one administrator.")).toBeInTheDocument();
    });

    it("says who keeps a password and who could administer, and turns them on again", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt({ local: { enabled: false, exempt: ["ana"], providers: 1, survivors: ["ana", "carla"] } });
      expect(await screen.findByText("Off", { selector: "[data-state]" })).toHaveAttribute("data-state", "stopped");
      expect(screen.getByText("Could still administer without the provider")).toBeInTheDocument();
      expect(screen.getByText("carla")).toBeInTheDocument();
      await user.click(screen.getByRole("switch", { name: "Sign in with passwords and passkeys" }));
      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/oidc/local-login")).toHaveLength(1);
      });
      expect(backend.callsTo("PUT /api/auth/oidc/local-login")[0]?.body).toEqual({ enabled: true });
      expect(await screen.findByText("On", { selector: "[data-state]" })).toHaveAttribute("data-state", "running");
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

  describe("directories (LDAP)", () => {
    it("lists the directories with their server and whether a password is kept, and passes axe", { timeout: 30_000 }, async () => {
      await pageAt({ directories: [DIRECTORY] });
      await screen.findByText("Acme directory");
      const row = rowOf("Acme directory");
      expect(within(row).getByText("Directory")).toBeInTheDocument();
      expect(within(row).getByText("ldap.acme.test:636 (ldaps)")).toBeInTheDocument();
      expect(within(row).getByText("Enabled")).toHaveAttribute("data-state", "running");
      await expectNoAxeViolations(screen.getByRole("main"));
    });

    it("adds a directory with the defaults of an OpenLDAP and the password apart", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt();
      backend.on("PUT /api/auth/ldap/directories/ad", (call: RecordedCall) => {
        const body = call.body as Record<string, unknown>;
        state.directories = [{ ...DIRECTORY, label: body["label"], host: body["host"] }];
        return json(200, state.directories[0]);
      });
      await addConnection(user, "Directory");
      const drawer = await screen.findByRole("dialog", { name: "Add a directory" });
      await user.type(within(drawer).getByLabelText(/^Name/), "ad");
      await user.type(within(drawer).getByLabelText(/^Label/), "Acme directory");
      await user.type(within(drawer).getByLabelText(/^Server/), "ldap.acme.test");
      await user.type(within(drawer).getByLabelText(/^Search from/), "ou=people,dc=acme,dc=test");
      await user.type(within(drawer).getByLabelText(/^Password/), "service password");
      await user.click(within(drawer).getByRole("button", { name: "Add directory" }));

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/ldap/directories/ad")).toHaveLength(1);
      });
      const body = backend.callsTo("PUT /api/auth/ldap/directories/ad")[0]?.body as Record<string, unknown>;
      expect(body).toMatchObject({
        label: "Acme directory",
        host: "ldap.acme.test",
        port: 636,
        tls: "ldaps",
        user_base: "ou=people,dc=acme,dc=test",
        user_filter: "(&(objectClass=person)(uid={username}))",
        subject_attribute: "entryUUID",
        provisioning: "off",
        // A directory is the organisation's own: its address is taken as verified.
        assume_email_verified: true,
        require_email_verified: false,
        bind_password: "service password",
        clear_bind_password: false,
      });
      expect(await screen.findByText("Acme directory")).toBeInTheDocument();
    });

    it("does not send a password that was not typed, and reads the group mappings back as lines", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt(
        { directories: [DIRECTORY] },
        { "PUT /api/auth/ldap/directories/ad": () => json(200, DIRECTORY) },
      );
      await rowAction(user, "ad", "Edit");
      const drawer = await screen.findByRole("dialog", { name: "Edit ad" });
      expect(within(drawer).getByText("A password is stored. Leave this empty to keep it.")).toBeInTheDocument();
      expect(within(drawer).getByLabelText(/^What each group means/)).toHaveValue("cn=ops,ou=groups,dc=acme,dc=test=admin");
      await user.click(within(drawer).getByRole("button", { name: "Save directory" }));
      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/ldap/directories/ad")).toHaveLength(1);
      });
      const body = backend.callsTo("PUT /api/auth/ldap/directories/ad")[0]?.body as Record<string, unknown>;
      expect(body).not.toHaveProperty("bind_password");
      expect(body["group_mappings"]).toEqual([{ group: "cn=ops,ou=groups,dc=acme,dc=test", role: "admin" }]);
    });

    it("says what the server refused beside the field it is about", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        {},
        {
          "PUT /api/auth/ldap/directories/ad": () =>
            problem(422, "validation_error", "Invalid", { fields: { user_filter: "The user filter must use {username}." } }),
        },
      );
      await addConnection(user, "Directory");
      const drawer = await screen.findByRole("dialog", { name: "Add a directory" });
      await user.type(within(drawer).getByLabelText(/^Name/), "ad");
      await user.type(within(drawer).getByLabelText(/^Server/), "ldap.acme.test");
      await user.type(within(drawer).getByLabelText(/^Search from/), "dc=acme");
      await user.click(within(drawer).getByRole("button", { name: "Add directory" }));
      expect((await within(drawer).findAllByText("The user filter must use {username}.")).length).toBeGreaterThan(0);
    });

    it("tests a directory and shows each check as it answered", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        { directories: [DIRECTORY] },
        {
          "POST /api/auth/ldap/directories/ad/test": () =>
            json(200, {
              ok: false,
              checks: [
                { name: "connection", ok: true, detail: "Reached ldap.acme.test:636 (ldaps)" },
                { name: "bind", ok: false, detail: "The directory refused the bind The directory said: invalid credentials" },
              ],
            }),
        },
      );
      await rowAction(user, "ad", "Test");
      const dialog = await screen.findByRole("dialog", { name: "Test of ad" });
      expect(within(dialog).getByText(/The directory said: invalid credentials/)).toBeInTheDocument();
      expect(within(dialog).getByText("Failed")).toHaveAttribute("data-state", "failed");
    });

    it("removes a directory after asking, and reads the list again", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt(
        { directories: [DIRECTORY] },
        { "DELETE /api/auth/ldap/directories/ad": () => json(200, { success: true, removed: "ad" }) },
      );
      await rowAction(user, "ad", "Remove");
      const dialog = await screen.findByRole("alertdialog", { name: "Remove ad?" });
      state.directories = [];
      await user.click(within(dialog).getByRole("button", { name: "Remove directory" }));
      await waitFor(() => {
        expect(backend.callsTo("DELETE /api/auth/ldap/directories/ad")).toHaveLength(1);
      });
      await waitFor(() => {
        expect(screen.queryByText("Acme directory")).not.toBeInTheDocument();
      });
    });
  });

  describe("SAML providers", () => {
    it("lists the providers with their entity id and when the certificate stops being good, and passes axe", { timeout: 30_000 }, async () => {
      await pageAt({ saml: [SAML_PROVIDER] });
      await screen.findByText("Entra ID");
      const row = rowOf("Entra ID");
      expect(within(row).getByText("SAML")).toBeInTheDocument();
      expect(within(row).getByText("https://sts.windows.net/tenant/")).toBeInTheDocument();
      expect(within(row).getByText("Enabled")).toHaveAttribute("data-state", "running");
      expect(within(row).getByRole("time")).toBeInTheDocument();
      expect(screen.queryByText("This console is not served over HTTPS")).not.toBeInTheDocument();
      await expectNoAxeViolations(screen.getByRole("main"));
    });

    it("says why a console over plain HTTP cannot take a SAML answer", { timeout: 30_000 }, async () => {
      await pageAt({ saml: [SAML_PROVIDER], secure: false });
      expect(await screen.findByText("This console is not served over HTTPS")).toBeInTheDocument();
      expect(screen.getByText(/no SAML sign-in can finish until the console is served over TLS/)).toBeInTheDocument();
    });

    it("adds a provider from its metadata alone, leaving the rest for the server to read", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt();
      backend.on("PUT /api/auth/saml/config/entra", () => {
        state.saml = [SAML_PROVIDER];
        return json(200, SAML_PROVIDER);
      });
      await addConnection(user, "SAML provider");
      const drawer = await screen.findByRole("dialog", { name: "Add a SAML provider" });
      const add = within(drawer).getByRole("button", { name: "Add provider" });
      await user.type(within(drawer).getByLabelText(/^Name/), "entra");
      // Nothing yet says who the provider is: the button waits.
      expect(add).toBeDisabled();
      await user.type(within(drawer).getByRole("textbox", { name: /^Its metadata/ }), "<md:EntityDescriptor entityID='x'/>");
      expect(add).toBeEnabled();
      await user.click(add);

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/saml/config/entra")).toHaveLength(1);
      });
      const body = backend.callsTo("PUT /api/auth/saml/config/entra")[0]?.body as Record<string, unknown>;
      expect(body).toMatchObject({
        idp_metadata: "<md:EntityDescriptor entityID='x'/>",
        provisioning: "off",
        // The organisation's own provider: its address is taken as verified.
        assume_email_verified: true,
        require_email_verified: false,
        email_attribute: "email",
        groups_attribute: "groups",
        clock_skew_seconds: 60,
      });
      expect(body).not.toHaveProperty("idp_entity_id");
      expect(body).not.toHaveProperty("sso_url");
      expect(body).not.toHaveProperty("certificates");
      expect(await screen.findByText("Entra ID")).toBeInTheDocument();
    });

    it("adds a provider from its three settings, the certificates split into blocks", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt(
        {},
        { "PUT /api/auth/saml/config/entra": () => json(200, SAML_PROVIDER) },
      );
      await addConnection(user, "SAML provider");
      const drawer = await screen.findByRole("dialog", { name: "Add a SAML provider" });
      await user.type(within(drawer).getByLabelText(/^Name/), "entra");
      await user.type(within(drawer).getByPlaceholderText("https://idp.acme.test/metadata"), "https://sts.windows.net/tenant/");
      await user.type(within(drawer).getByLabelText(/^Sign-in address/), "https://login.microsoftonline.com/tenant/saml2");
      await user.type(
        within(drawer).getByLabelText(/^Signing certificates/),
        `${SAML_PEM}\n${SAML_PEM.replace("MIICvD", "MIICvE")}`,
      );
      await user.click(within(drawer).getByRole("button", { name: "Add provider" }));

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/saml/config/entra")).toHaveLength(1);
      });
      const body = backend.callsTo("PUT /api/auth/saml/config/entra")[0]?.body as Record<string, unknown>;
      expect(body["idp_entity_id"]).toBe("https://sts.windows.net/tenant/");
      expect(body["sso_url"]).toBe("https://login.microsoftonline.com/tenant/saml2");
      expect(body["certificates"]).toEqual([SAML_PEM, SAML_PEM.replace("MIICvD", "MIICvE")]);
      expect(body).not.toHaveProperty("idp_metadata");
    });

    it("shows what to register with the provider, and reads the group mappings back as lines", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt(
        { saml: [SAML_PROVIDER] },
        { "PUT /api/auth/saml/config/entra": () => json(200, SAML_PROVIDER) },
      );
      await rowAction(user, "entra", "Edit");
      const drawer = await screen.findByRole("dialog", { name: "Edit entra" });
      const register = within(drawer).getByRole("region", { name: "Register this server with the provider" });
      expect(within(register).getAllByText("https://noust.acme.test/api/auth/saml/metadata/entra").length).toBeGreaterThan(0);
      expect(within(register).getAllByText("https://noust.acme.test/api/auth/saml/acs").length).toBeGreaterThan(0);
      expect(within(drawer).getByLabelText(/^What each group means/)).toHaveValue("ops=operator");
      await user.click(within(drawer).getByRole("button", { name: "Save provider" }));

      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/saml/config/entra")).toHaveLength(1);
      });
      const body = backend.callsTo("PUT /api/auth/saml/config/entra")[0]?.body as Record<string, unknown>;
      // What is on screen is what is saved, the certificates included; no metadata was pasted.
      expect(body["certificates"]).toEqual([SAML_PEM]);
      expect(body["idp_entity_id"]).toBe("https://sts.windows.net/tenant/");
      expect(body["group_mappings"]).toEqual([{ group: "ops", role: "operator" }]);
      expect(body).not.toHaveProperty("idp_metadata");
    });

    it("says what the server refused beside the field it is about", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        {},
        {
          "PUT /api/auth/saml/config/entra": () =>
            problem(422, "validation_error", "Invalid", { fields: { certificates: "Not a certificate: no start line." } }),
        },
      );
      await addConnection(user, "SAML provider");
      const drawer = await screen.findByRole("dialog", { name: "Add a SAML provider" });
      await user.type(within(drawer).getByLabelText(/^Name/), "entra");
      await user.type(within(drawer).getByRole("textbox", { name: /^Its metadata/ }), "<x/>");
      await user.click(within(drawer).getByRole("button", { name: "Add provider" }));
      expect((await within(drawer).findAllByText("Not a certificate: no start line.")).length).toBeGreaterThan(0);
    });

    it("says which mappings line is wrong instead of sending it", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt({ saml: [SAML_PROVIDER] });
      await rowAction(user, "entra", "Edit");
      const drawer = await screen.findByRole("dialog", { name: "Edit entra" });
      const box = within(drawer).getByLabelText(/^What each group means/);
      await user.clear(box);
      await user.type(box, "not a mapping");
      await user.click(within(drawer).getByRole("button", { name: "Save provider" }));
      expect(await within(drawer).findByText(/Line 1 is not a mapping/)).toBeInTheDocument();
      expect(backend.callsTo("PUT /api/auth/saml/config/entra")).toHaveLength(0);
    });

    it("tests a provider and shows each check as it answered", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        { saml: [SAML_PROVIDER] },
        {
          "POST /api/auth/saml/config/entra/test": () =>
            json(200, {
              ok: false,
              checks: [
                { name: "certificate", ok: true, detail: "CN=Microsoft: good for 600 more days" },
                { name: "assertion consumer address", ok: false, detail: "http://noust.acme.test/api/auth/saml/acs: serve the console over TLS" },
              ],
            }),
        },
      );
      await rowAction(user, "entra", "Test");
      const dialog = await screen.findByRole("dialog", { name: "Test of entra" });
      expect(within(dialog).getByText(/serve the console over TLS/)).toBeInTheDocument();
      expect(within(dialog).getByText("Failed")).toHaveAttribute("data-state", "failed");
    });

    it("removes a provider after asking, and reads the list again", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt(
        { saml: [SAML_PROVIDER] },
        { "DELETE /api/auth/saml/config/entra": () => json(200, { success: true, removed: "entra" }) },
      );
      await rowAction(user, "entra", "Remove");
      const dialog = await screen.findByRole("alertdialog", { name: "Remove entra?" });
      state.saml = [];
      await user.click(within(dialog).getByRole("button", { name: "Remove provider" }));
      await waitFor(() => {
        expect(backend.callsTo("DELETE /api/auth/saml/config/entra")).toHaveLength(1);
      });
      await waitFor(() => {
        expect(screen.queryByText("Entra ID")).not.toBeInTheDocument();
      });
    });
  });

  describe("provisioning (SCIM)", () => {
    it("offers to add the first source, and says it signs nobody in", { timeout: 30_000 }, async () => {
      await pageAt();
      const empty = await screen.findByRole("heading", { name: "No provisioning source" });
      const section = screen.getByRole("region", { name: "Provisioning (SCIM)" });
      expect(within(section).getByRole("heading", { name: "No provisioning source" })).toBe(empty);
      expect(within(section).getByText(/It does not sign anybody in/)).toBeInTheDocument();
      expect(within(section).getByRole("button", { name: "Add a source" })).toBeInTheDocument();
    });

    it("lists the sources with how many people each provisioned, and the address to give the provider", { timeout: 30_000 }, async () => {
      await pageAt({ scim: [SCIM_SOURCE] });
      // The skeleton has the table's name too: wait for the source, then take the real table.
      await screen.findByText("Entra ID");
      const table = screen.getByRole("region", { name: "Provisioning sources" });
      const row = within(table).getAllByRole("row")[1];
      if (!row) throw new Error("no source row");
      expect(within(row).getByText("Entra ID")).toBeInTheDocument();
      expect(within(row).getByText("Enabled")).toHaveAttribute("data-state", "running");
      expect(within(row).getByText("12")).toBeInTheDocument();
      expect(screen.getByText(SCIM_URL)).toBeInTheDocument();
      await expectNoAxeViolations(screen.getByRole("main"));
    });

    it("adds a source and shows its token once, with the address to give the provider", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt();
      backend.on("PUT /api/auth/scim/sources/entra", (call: RecordedCall) => {
        const body = call.body as Record<string, unknown>;
        state.scim = [{ ...SCIM_SOURCE, label: body["label"], users: 0 }];
        return json(200, { source: state.scim[0], token: "noust_scim_s3cr3t-value", base_url: SCIM_URL });
      });
      await user.click(await screen.findByRole("button", { name: "Add a source" }));
      const drawer = await screen.findByRole("dialog", { name: "Add a provisioning source" });
      await user.type(within(drawer).getByLabelText(/^Name/), "entra");
      await user.type(within(drawer).getByLabelText(/^Label/), "Entra ID");
      await user.click(within(drawer).getByRole("button", { name: "Add source" }));

      const once = await screen.findByRole("dialog", { name: "Give the provider this token" });
      expect(within(once).getByTestId("new-scim-token")).toHaveTextContent("noust_scim_s3cr3t-value");
      expect(within(once).getByText(SCIM_URL)).toBeInTheDocument();
      const body = backend.callsTo("PUT /api/auth/scim/sources/entra")[0]?.body as Record<string, unknown>;
      expect(body).toMatchObject({ label: "Entra ID", enabled: true, default_role: "viewer", link_by_email: false, rotate_token: false, group_sync: false });
      // Provisioning sources have no sign-in policy: nothing about provisioning or e-mail trust is sent.
      expect(body).not.toHaveProperty("provisioning");
      expect(body).not.toHaveProperty("require_email_verified");
      await user.click(within(once).getByRole("button", { name: "Done" }));
      expect(screen.queryByText("noust_scim_s3cr3t-value")).not.toBeInTheDocument();
      expect(await screen.findByText("Entra ID")).toBeInTheDocument();
    });

    it("changes a source without issuing a token, and issues one when asked", { timeout: 30_000 }, async () => {
      const { backend, user } = await pageAt(
        { scim: [SCIM_SOURCE] },
        {
          "PUT /api/auth/scim/sources/entra": (call: RecordedCall) => {
            const asked = (call.body as { rotate_token: boolean }).rotate_token;
            return json(200, { source: SCIM_SOURCE, token: asked ? "noust_scim_n3w" : null, base_url: SCIM_URL });
          },
        },
      );
      await rowAction(user, "entra", "Edit");
      let drawer = await screen.findByRole("dialog", { name: "Edit entra" });
      // The mapping reads back as the line it was written as.
      await user.click(within(drawer).getByText("What each group means", { exact: false }));
      expect(within(drawer).getByLabelText(/^What each group means/)).toHaveValue("release=release-manager@acme/web");
      await user.click(within(drawer).getByRole("button", { name: "Save source" }));
      await waitFor(() => {
        expect(backend.callsTo("PUT /api/auth/scim/sources/entra")).toHaveLength(1);
      });
      expect(screen.queryByRole("dialog", { name: "Give the provider this token" })).not.toBeInTheDocument();

      await rowAction(user, "entra", "Edit");
      drawer = await screen.findByRole("dialog", { name: "Edit entra" });
      await user.click(within(drawer).getByRole("checkbox", { name: /Issue a new token/ }));
      await user.click(within(drawer).getByRole("button", { name: "Save source" }));
      const once = await screen.findByRole("dialog", { name: "Give the provider this token" });
      expect(within(once).getByTestId("new-scim-token")).toHaveTextContent("noust_scim_n3w");
      expect(backend.callsTo("PUT /api/auth/scim/sources/entra")[1]?.body).toMatchObject({ rotate_token: true });
    });

    it("says what the server refused, beside the field it is about", { timeout: 30_000 }, async () => {
      const { user } = await pageAt(
        {},
        {
          "PUT /api/auth/scim/sources/entra": () =>
            problem(422, "validation_error", "Invalid", { fields: { default_team: "There is no team 'ghost'." } }),
        },
      );
      await user.click(await screen.findByRole("button", { name: "Add a source" }));
      const drawer = await screen.findByRole("dialog", { name: "Add a provisioning source" });
      await user.type(within(drawer).getByLabelText(/^Name/), "entra");
      await user.click(within(drawer).getByRole("button", { name: "Add source" }));
      expect((await within(drawer).findAllByText("There is no team 'ghost'.")).length).toBeGreaterThan(0);
    });

    it("removes a source after asking, and reads the list again", { timeout: 30_000 }, async () => {
      const { backend, state, user } = await pageAt(
        { scim: [SCIM_SOURCE] },
        { "DELETE /api/auth/scim/sources/entra": () => json(200, { success: true, removed: "entra" }) },
      );
      await rowAction(user, "entra", "Remove");
      const dialog = await screen.findByRole("alertdialog", { name: "Remove entra?" });
      state.scim = [];
      await user.click(within(dialog).getByRole("button", { name: "Remove source" }));
      await waitFor(() => {
        expect(backend.callsTo("DELETE /api/auth/scim/sources/entra")).toHaveLength(1);
      });
      expect(await screen.findByRole("heading", { name: "No provisioning source" })).toBeInTheDocument();
    });

    it("speaks Spanish", { timeout: 30_000 }, async () => {
      await pageAt({ scim: [SCIM_SOURCE] });
      await act(() => setLocale("es"));
      // The skeleton has the table's name too: wait for a button only the loaded table has.
      expect(await screen.findByRole("button", { name: "Acciones de entra" })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: "Fuentes de aprovisionamiento" })).toBeInTheDocument();
      await act(() => setLocale("en"));
    });
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    // The skeleton has the table's name too: wait for a button only the loaded table has.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Acciones de corp" })).toBeInTheDocument();
    });
    expect(screen.getByRole("region", { name: "Conexiones de inicio de sesión" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Añadir una conexión" })).toBeInTheDocument();
    });
    await act(() => setLocale("en"));
  });
});
