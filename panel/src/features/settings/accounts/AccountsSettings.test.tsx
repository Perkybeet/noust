import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";

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
    backup_codes_remaining: 8,
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

function accountsBackend(extra: Record<string, RouteHandler> = {}) {
  let elevated = false;
  return fakeBackend({
    ...signedInRoutes(SECURITY),
    "GET /api/auth/accounts": () => json(200, { accounts: ACCOUNTS, conflicts: [] }),
    "GET /api/auth/exceptions": () => json(200, { exceptions: [] }),
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
  // A person confirms with their password and a code.
  await user.type(within(confirm).getByLabelText("Password"), "correct horse battery");
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
}

describe("Settings > Accounts", () => {
  it("lists every account with its state, role and second factor, and passes axe", { timeout: 20_000 }, async () => {
    accountsBackend();
    const { container } = renderConsole("/settings/accounts");
    const table = await screen.findByRole("region", { name: "Every account" });
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
    const header = screen.getByRole("heading", { level: 1, name: "Settings" }).closest("header");
    if (header === null) throw new Error("no page header");
    expect(header).toContainElement(invite);
    expect(invite).toHaveAttribute("data-variant", "primary");
  });

  it("invites a person and shows the link once, with the code after the #", { timeout: 20_000 }, async () => {
    const backend = accountsBackend({
      "POST /api/auth/invitations": (call) =>
        json(201, { account: account((call.body as { username: string }).username, "operator", { status: "invited" }), code: "noust_inv_abc123", expires_in: 86_400 }),
    });
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Username/), "fer");
    await user.type(within(dialog).getByLabelText(/^Email/), "fer@example.com");
    await user.click(within(dialog).getByRole("radio", { name: /^Operator/ }));
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create invitation" }));
    await confirmItsYou(user);

    const issued = await screen.findByRole("dialog", { name: "Send this invitation to fer" });
    expect(within(issued).getByTestId("invitation-link")).toHaveValue(`${window.location.origin}/invite#noust_inv_abc123`);
    expect(within(issued).getByText("Shown only now")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/invitations").at(-1)?.body).toEqual({
      username: "fer",
      expires_hours: 24,
      role: "operator",
      person_ref: "fer@example.com",
    });
  });

  it("warns before giving one person roles that go against each other", async () => {
    accountsBackend();
    const { user } = renderConsole("/settings/accounts");
    await screen.findByText("ana");
    await user.click(screen.getByRole("button", { name: "Invite a person" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite a person" });
    await user.type(within(dialog).getByLabelText(/^Username/), "ana.sec");
    await user.type(within(dialog).getByLabelText(/^Email/), "ana@example.com");
    await user.click(within(dialog).getByRole("radio", { name: /^Security officer/ }));
    expect(await within(dialog).findByText("Roles that go against each other")).toBeInTheDocument();
    expect(within(dialog).getByText(/The same person already has ana/)).toBeInTheDocument();
  });

  it("changes a role through a second person's approval, and runs it once approved", { timeout: 30_000 }, async () => {
    let state = "requested";
    const backend = accountsBackend({
      "PATCH /api/auth/accounts/ana": (call) =>
        call.headers.get("X-Noust-Approval") === "41"
          ? json(200, account("ana", "admin"))
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
          path: "/api/auth/accounts/ana",
          parameters: { method: "PATCH", path: "/api/auth/accounts/ana", query: [], body: { role: "admin" } },
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
    await user.click(await screen.findByRole("menuitem", { name: "Change role" }));
    const dialog = await screen.findByRole("dialog", { name: "Change the role of ana" });
    await user.click(within(dialog).getByRole("radio", { name: /^Administrator/ }));
    await user.click(within(dialog).getByRole("button", { name: "Change role" }));
    await confirmItsYou(user);

    const waiting = await screen.findByRole("dialog", { name: "Waiting for approval" });
    expect(within(waiting).getByText("#41")).toBeInTheDocument();
    expect(within(waiting).getByText("Waiting")).toBeInTheDocument();
    await expectNoAxeViolations(waiting);

    state = "approved";
    const approved = await screen.findByRole("dialog", { name: "Approved" }, { timeout: 8_000 });
    expect(within(approved).getByText("Go ahead.")).toBeInTheDocument();
    await user.click(within(approved).getByRole("button", { name: "Run it now" }));

    await waitFor(() => {
      expect(backend.callsTo("PATCH /api/auth/accounts/ana").at(-1)?.headers.get("X-Noust-Approval")).toBe("41");
    });
    expect(backend.callsTo("PATCH /api/auth/accounts/ana").map((call) => call.body)).toEqual([{ role: "admin" }, { role: "admin" }, { role: "admin" }]);
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
