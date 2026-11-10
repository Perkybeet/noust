import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { findLoadedRegion } from "../../../test/regions";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

const NOW = Date.now() / 1000;

function account(username: string, role: string, extra: Record<string, unknown> = {}) {
  return {
    id: username.length,
    username,
    display_name: "",
    role,
    status: "active",
    person_ref: `${username}@example.com`,
    mfa_enabled: true,
    passkeys: 0,
    backup_codes_remaining: 8, owner: false,
    failures_since_login: 0,
    created_at: NOW - 86_400,
    last_login_at: NOW - 3600,
    ...extra,
  };
}

const ACCOUNTS = [
  account("ana", "operator"),
  account("bea", "security", { passkeys: 2 }),
  account("carlos", "viewer", { status: "locked", locked_until: NOW + 900 }),
  account("dani", "admin", { mfa_enabled: false, status: "invited", last_login_at: null }),
  account("eva", "auditor", { status: "disabled" }),
];

const SECURITY = {
  ...SESSION,
  grant: null,
  role: "security",
  account: account("bea", "security"),
  permissions: ["self", "apps.read", "accounts.read", "accounts.manage", "audit.read", "security.manage"],
};

function roleOut(name: string, label: string, permissions: string[], over: Record<string, unknown> = {}) {
  return {
    name,
    kind: "seeded",
    label,
    description: `${label} description`,
    permissions: ["self", ...permissions],
    shipped_permissions: ["self", ...permissions],
    edited: false,
    follows_noust: true,
    protected: false,
    offers: [],
    holders: [],
    holder_count: 0,
    bindings: 0,
    joins_duties: false,
    governing: [],
    running: [],
    can_edit: true,
    can_delete: false,
    can_reset: false,
    can_duplicate: true,
    created_at: 1_790_000_000,
    updated_at: 1_790_000_000,
    ...over,
  };
}

const SERVER_ROLES = [
  roleOut("viewer", "Viewer", ["apps.read"]),
  roleOut("operator", "Operator", ["apps.read", "apps.deploy"]),
  roleOut("admin", "Administrator", ["apps.read", "apps.deploy", "accounts.manage"]),
  roleOut("security", "Security officer", ["accounts.read", "audit.read"]),
  roleOut("auditor", "Auditor", ["apps.read", "audit.read"]),
  roleOut("owner", "Owner", ["apps.read", "apps.deploy", "accounts.manage"], { kind: "protected", protected: true, can_edit: false, can_duplicate: false }),
];

function accountsBackend(extra: Record<string, RouteHandler> = {}) {
  let elevated = false;
  return fakeBackend({
    ...signedInRoutes(SECURITY),
    "GET /api/auth/accounts": () => json(200, { accounts: ACCOUNTS, conflicts: [] }),
    "GET /api/auth/exceptions": () => json(200, { exceptions: [] }),
    "GET /api/roles": () => json(200, { roles: SERVER_ROLES, permissions: { "apps.read": "See applications", "apps.deploy": "Deploy applications", "accounts.manage": "Manage accounts", "accounts.read": "See accounts", "audit.read": "Read the audit log" }, owners: 1, duties_apart: false }),
    "GET /api/ens/access-review": () =>
      json(200, { accounts: ACCOUNTS, conflicts: [], exceptions: [], tokens_without_owner: 0, generated_at: "2026-09-30T00:00:00Z", digest: "ab12", reviews: [] }),
    "GET /api/approvals/policy": () => json(200, { enabled: true, approvers: ["security"], request_hours: 24, execute_minutes: 30, reason_required: false, rules: [] }),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    ...Object.fromEntries(
      Object.entries(extra).map(([route, handler]) => [
        route,
        (call: RecordedCall) => (route.startsWith("GET") || elevated ? handler(call) : problem(403, "elevation_required", "Confirm it's you to continue.")),
      ]),
    ),
  });
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  // A person confirms with a code: they gave the password and a code to sign in.
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Continue" }));
}

describe("Settings > Accounts", () => {
  it("lists every account with its state, role and second factor, and passes axe", { timeout: 20_000 }, async () => {
    accountsBackend();
    const { container } = renderConsole("/settings/accounts");
    const table = await findLoadedRegion("Every account");
    const row = (name: string) => {
      const found = within(table).getByText(name).closest("tr");
      if (found === null) throw new Error(`no row for ${name}`);
      return within(found);
    };
    await within(table).findByText("ana");
    // Active or disabled at a glance: green with a dot, grey with a ring (item 56).
    expect(row("ana").getByText("Active")).toHaveAttribute("data-state", "running");
    expect(row("ana").getByText("Operator")).toBeInTheDocument();
    expect(row("bea").getByText("2 passkeys")).toBeInTheDocument();
    expect(row("carlos").getByText(/^Locked until/)).toBeInTheDocument();
    expect(row("dani").getByText("Invited")).toBeInTheDocument();
    expect(row("dani").getByText("Not set up")).toBeInTheDocument();
    expect(row("eva").getByText("Disabled")).toHaveAttribute("data-state", "stopped");
    expect(screen.getByText("5 accounts")).toBeInTheDocument();
    // Your own account cannot be changed from here: another officer does it.
    await expectNoAxeViolations(container);
  });

  it("filters by role and state from the URL", async () => {
    accountsBackend();
    const { user, location } = renderConsole("/settings/accounts?role=viewer");
    await screen.findByText("carlos");
    expect(screen.queryByText("ana")).toBeNull();
    expect(screen.getByText("1 of 5 accounts")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("ana")).toBeInTheDocument();
    expect(location().search).toEqual({});
  });

  it("puts Invite a person in the page's header, as the view's one primary action", async () => {
    accountsBackend();
    renderConsole("/settings/accounts");
    await screen.findByText("ana");
    const invite = screen.getByRole("button", { name: "Invite a person" });
    const header = screen.getByRole("heading", { level: 1, name: "Accounts" }).closest("header");
    if (header === null) throw new Error("no page header");
    expect(header).toContainElement(invite);
    expect(invite).toHaveAttribute("data-variant", "primary");
  });

  it("invites a person with one email and what they may do, and shows the link once, with the code after the #", { timeout: 20_000 }, async () => {
    const backend = accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, { account: account((call.body as { username: string }).username, "operator", { status: "invited" }), code: "noust_inv_abc123", expires_in: 86_400 }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    // One address, and the account is named from it; there is no second field for the same address.
    expect(within(dialog).getAllByLabelText(/^Email/)).toHaveLength(1);
    await user.type(within(dialog).getByLabelText(/^Email/), "Lucia.Ferrer@example.com");
    expect(within(dialog).getByText("lucia.ferrer")).toBeInTheDocument();
    await user.click(await within(dialog).findByRole("radio", { name: /^Operator/ }));
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to lucia.ferrer" });
    expect(within(issued).getByTestId("invitation-link")).toHaveValue(`${window.location.origin}/invite#noust_inv_abc123`);
    expect(within(issued).getByText("The email did not go out")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/invitations").at(-1)?.body).toEqual({ username: "lucia.ferrer", expires_hours: 24, role: "operator", email: "Lucia.Ferrer@example.com" });
  });

  it("builds the link from the console's own address when the server has one, and says nothing of the tunnel (item 149)", { timeout: 20_000 }, async () => {
    accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, {
          account: account((call.body as { username: string }).username, "operator", { status: "invited" }),
          code: "noust_inv_abc123",
          expires_in: 86_400,
          public_url: "https://console.example.com",
        }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "lucia@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to lucia" });
    // The address the browser was opened at (here a local one, as through a tunnel) is not what the link says.
    expect(within(issued).getByTestId("invitation-link")).toHaveValue("https://console.example.com/invite#noust_inv_abc123");
    expect(within(issued).queryByText("This link only works on this computer")).not.toBeInTheDocument();
  });

  it("says that a link built from a local address is of no use to the person invited, and where to give an address", { timeout: 20_000 }, async () => {
    accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, { account: account((call.body as { username: string }).username, "operator", { status: "invited" }), code: "noust_inv_abc123", expires_in: 86_400, public_url: null }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "lucia@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to lucia" });
    expect(within(issued).getByText("This link only works on this computer")).toBeInTheDocument();
    expect(within(issued).getByText(/localhost/)).toBeInTheDocument();
    expect(within(issued).getByRole("link", { name: "Set the console's address" })).toHaveAttribute("href", "/settings/general");
    // The link is still there: it opens for whoever can reach this address.
    expect(within(issued).getByTestId("invitation-link")).toHaveValue(`${window.location.origin}/invite#noust_inv_abc123`);
  });

  it("lets the proposed account name be changed, and refuses one the server would", { timeout: 20_000 }, async () => {
    const backend = accountsBackend({
      "POST /api/auth/invitations": (call) => json(201, { account: account((call.body as { username: string }).username, "viewer", { status: "invited" }), code: "noust_inv_abc123", expires_in: 86_400, emailed: true }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "juan@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Change" }));
    const name = within(dialog).getByLabelText(/^Account name/);
    expect(name).toHaveValue("juan");
    await user.clear(name);
    await user.type(name, "Juan Angel");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    // Said before it is sent, beside the field: the server is not asked for a name it refuses.
    expect(await within(dialog).findByText("Use letters, digits, dots, hyphens and underscores, from 2 to 64.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/invitations")).toHaveLength(0);
    await user.clear(name);
    await user.type(name, "juan.angel");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);
    await screen.findByRole("dialog", { name: "Send this invitation to juan.angel" });
  });

  it("says the email was sent, and shows the link all the same", { timeout: 20_000 }, async () => {
    const backend = accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, {
          account: account((call.body as { username: string }).username, "operator", { status: "invited", email: "fer@work.example" }),
          code: "noust_inv_abc123",
          expires_in: 86_400,
          emailed: true,
        }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "fer@work.example");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to fer" });
    expect(within(issued).getByText("Invitation sent")).toBeInTheDocument();
    expect(within(issued).getByText("The link was emailed to fer@work.example. It works once.")).toBeInTheDocument();
    // The link is shown all the same: a mail is the convenience, the invitation is the link.
    expect(within(issued).getByTestId("invitation-link")).toHaveValue(`${window.location.origin}/invite#noust_inv_abc123`);
    expect(backend.callsTo("POST /api/auth/invitations").at(-1)?.body).toMatchObject({ username: "fer", email: "fer@work.example" });
  });

  it("says why the email did not go, in the console's language from the server's code, and still shows the link", { timeout: 20_000 }, async () => {
    accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, {
          account: account((call.body as { username: string }).username, "operator", { status: "invited" }),
          code: "noust_inv_abc123",
          expires_in: 86_400,
          emailed: false,
          email_error: "This server cannot send mail yet.",
          email_error_hint: "Set up a mail account.",
          email_error_coded: { code: "mail.no_transport", params: {} },
        }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "fer@work.example");
    await user.click(within(dialog).getByRole("button", { name: "Send invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to fer" });
    expect(within(issued).getByText("The email did not go out")).toBeInTheDocument();
    expect(within(issued).getByText("The invitation exists. Send them the link yourself.")).toBeInTheDocument();
    expect(within(issued).getByTestId("invitation-link")).toBeInTheDocument();
  });

  it("sets the address mail goes to, and clears it with an empty one", { timeout: 30_000 }, async () => {
    const backend = accountsBackend({
      "PATCH /api/auth/accounts/ana": (call) => json(200, account("ana", "operator", { email: (call.body as { email: string }).email })),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Actions for ana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Mail address…" }));
    const dialog = await screen.findByRole("dialog", { name: "Mail address of ana" });
    expect(within(dialog).getByRole("button", { name: "Save the address" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText("Address"), "ana@work.example");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Save the address" }));
    await confirmItsYou(user);

    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/auth/accounts/ana").length).toBeGreaterThan(0);
    });
    expect(backend.callsTo("PATCH /api/auth/accounts/ana").at(-1)?.body).toEqual({ email: "ana@work.example" });
  });

  it("warns before giving one person roles that go against each other", async () => {
    accountsBackend();
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Email/), "ana@example.com");
    // The account is named "ana" from the email, which is the person's own account: it needs another name.
    await user.click(within(dialog).getByRole("button", { name: "Change" }));
    const name = within(dialog).getByLabelText(/^Account name/);
    await user.clear(name);
    await user.type(name, "ana.sec");
    await user.click(await within(dialog).findByRole("radio", { name: /^Security officer/ }));
    expect(await within(dialog).findByText("Roles that go against each other")).toBeInTheDocument();
    expect(within(dialog).getByText(/The same person already has ana/)).toBeInTheDocument();
  });

  it("changes a role through a second person's approval, and runs it once approved", { timeout: 30_000 }, async () => {
    let state = "requested";
    const backend = accountsBackend({
      "PUT /api/auth/accounts/ana/roles": (call) =>
        call.headers.get("X-Noust-Approval") === "41"
          ? json(200, account("ana", "operator", { roles: ["operator", "admin"] }))
          : json(
              202,
              { error: "approval_required", detail: "This needs a second person's approval. Request 41 is waiting for a security account to decide it.", hint: null, fields: { approval: "41", state: "requested" }, output: null },
              { "X-Noust-Approval-Request": "41" },
            ),
      "GET /api/approvals/41": () =>
        json(200, {
          id: 41,
          action: "user.role_change",
          kind: "role_change",
          description: "Change an account's role",
          method: "PATCH",
          path: "/api/auth/accounts/ana/roles",
          parameters: { method: "PUT", path: "/api/auth/accounts/ana/roles", query: [], body: { role: "admin" } },
          fingerprint: "f00d",
          state,
          requester: { kind: "account", name: "bea", role: "security" },
          created_at: NOW,
          expires_at: NOW + 86_400,
          ...(state === "approved" ? { decider: { kind: "account", name: "gus", role: "security" }, decision_comment: "Go ahead." } : {}),
          mine: true,
          can_decide: false,
        }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Actions for ana" }));
    await user.click(await screen.findByRole("menuitem", { name: "Change roles" }));
    const dialog = await screen.findByRole("dialog", { name: "Roles of ana" });
    // A person holds several roles: the operator she has, and the administrator added.
    expect(await within(dialog).findByRole("checkbox", { name: /^Operator/ })).toBeChecked();
    await user.click(within(dialog).getByRole("checkbox", { name: /^Administrator/ }));
    await user.click(within(dialog).getByRole("button", { name: "Save roles" }));
    await confirmItsYou(user);

    const waiting = await screen.findByRole("dialog", { name: "Waiting for approval" });
    expect(within(waiting).getByText("#41")).toBeInTheDocument();
    expect(within(waiting).getByText("Pending")).toBeInTheDocument();
    await expectNoAxeViolations(waiting);

    state = "approved";
    const approved = await screen.findByRole("dialog", { name: "Approved" }, { timeout: 8_000 });
    expect(within(approved).getByText("Go ahead.")).toBeInTheDocument();
    await user.click(within(approved).getByRole("button", { name: "Run it now" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/auth/accounts/ana/roles").at(-1)?.headers.get("X-Noust-Approval")).toBe("41");
    });
    expect(backend.callsTo("PUT /api/auth/accounts/ana/roles").map((call) => call.body)).toEqual([{ roles: ["operator", "admin"] }, { roles: ["operator", "admin"] }, { roles: ["operator", "admin"] }]);
  });

  it("shows every role of a person and what they can do from all of them, and which role gives each permission", { timeout: 30_000 }, async () => {
    accountsBackend();
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    const row = (await screen.findByText("ana")).closest("tr");
    if (row === null) throw new Error("no row for ana");
    expect(within(row).getByText("Operator")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions for ana" }));
    await user.click(await screen.findByRole("menuitem", { name: "See what they can do" }));
    const drawer = await screen.findByRole("dialog", { name: /What .* can do/ });
    expect(within(drawer).getByText("apps.deploy")).toBeInTheDocument();
    expect(within(drawer).getByText("Deploy applications")).toBeInTheDocument();
  });

  it("removes an account only once its name is typed", { timeout: 20_000 }, async () => {
    const backend = accountsBackend({ "DELETE /api/auth/accounts/eva": () => json(200, { account: account("eva", "auditor"), message: "removed" }) });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("eva");
    await user.click(screen.getByRole("button", { name: "Actions for eva" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove account" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove eva?" });
    expect(within(dialog).getByText(/Prefer disabling/)).toBeInTheDocument();
    const action = within(dialog).getByRole("button", { name: "Remove account" });
    expect(action).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), "eva");
    await user.click(action);
    await confirmItsYou(user);
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/auth/accounts/eva").length).toBeGreaterThan(0);
    });
  });

  it("offers the first accounts when there is none yet, and speaks Spanish", async () => {
    fakeBackend({
      ...signedInRoutes({ ...SESSION, grant: "compat", accounts_exist: false }),
      "GET /api/auth/accounts": () => json(200, { accounts: [], conflicts: [] }),
    });
    renderConsole("/settings/accounts");
    expect(await screen.findByRole("heading", { name: "No accounts yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create the first accounts" })).toHaveAttribute("href", "/setup?next=%2Fsettings%2Faccounts");
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { name: "Aún no hay cuentas" })).toBeInTheDocument();
  });
});
