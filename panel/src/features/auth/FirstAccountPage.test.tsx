import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { holdAccessToken, takeAccessToken } from "./accessProof";

const ENROLLMENT = {
  secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
  uri: "otpauth://totp/Noust:ana%40web-01?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Noust",
};

const ACCOUNT = {
  id: 1,
  username: "ana",
  display_name: "Ana",
  role: "admin",
  status: "active",
  mfa_enabled: false,
  passkeys: 0,
  backup_codes_remaining: 0,
  failures_since_login: 0,
  created_at: Date.now() / 1000,
};

const compliance = (split: boolean) => ({
  frameworks: [],
  controls: [
    {
      id: "roles_split",
      group: "access",
      kind: "flag",
      standard: false,
      key: "security.roles_split",
      choices: [],
      unit: "",
      check_only: false,
      value: split,
      operator: null,
      floor: null,
      forced_by: [],
      locked: false,
      enabled: split,
    },
  ],
  legacy_profile: "standard",
  unknown: [],
});

interface Options {
  split?: boolean;
  mfaRequired?: boolean;
  loginFails?: boolean;
  accountsRefused?: boolean;
}

/**
 * A server with no accounts, entered with the access token. Making the administrator signs in with
 * it, so from then on the session is the account's.
 */
function newServer({ split = false, mfaRequired = false, loginFails = false, accountsRefused = false }: Options = {}) {
  let account: typeof ACCOUNT | null = null;
  let signedOut = false;
  const compat = { ...SESSION, grant: "compat", accounts_exist: false };
  const adminSession = () => ({ ...SESSION, grant: null, role: "admin", accounts_exist: true, account, mfa_required: mfaRequired });
  return fakeBackend({
    ...signedInRoutes(compat),
    "GET /api/auth/session": () => json(200, signedOut ? ANONYMOUS : account === null ? compat : adminSession()),
    "POST /api/auth/logout": () => {
      signedOut = true;
      return json(200, { success: true, message: "Signed out" });
    },
    "GET /api/compliance": () => json(200, compliance(split)),
    "POST /api/auth/accounts": (call: RecordedCall) => {
      if (accountsRefused) return problem(422, "validation_error", "Invalid input.", { fields: { person_ref: "That is not an email address." } });
      const body = call.body as { username: string; role: string };
      const made = { ...ACCOUNT, id: body.role === "admin" ? 1 : 2, username: body.username, role: body.role };
      if (body.role === "admin") account = made;
      return json(201, made);
    },
    "POST /api/auth/login": () =>
      loginFails ? problem(401, "invalid_credentials", "Invalid credentials.") : json(200, { success: true, expires_in: 28_800, csrf_token: "c", session_token: null, account }),
    "POST /api/auth/2fa/enroll": () => json(200, ENROLLMENT),
  });
}

describe("the first account", () => {
  it("confirms it's the person with the token that signed in, once and unasked, before the account is made", { timeout: 30_000 }, async () => {
    const backend = newServer();
    backend.on("POST /api/auth/elevate", () => json(200, { elevated_until: Date.now() / 1000 + 900 }));
    holdAccessToken("noust_tok_just_typed");
    const { user } = renderConsole("/setup");
    await screen.findByRole("heading", { level: 1, name: "Create the administrator" });
    await user.type(screen.getByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));
    await screen.findByRole("heading", { level: 1, name: "Protect your account" });

    // The confirmation came first, with the token, and no dialog asked for it.
    expect(backend.callsTo("POST /api/auth/elevate").map((call) => call.body)).toEqual([{ token: "noust_tok_just_typed" }]);
    expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).toBeNull();
    // It was spent: a second use finds nothing.
    expect(takeAccessToken()).toBeNull();
  });

  it("makes the account anyway when the token cannot confirm (a second factor on it), leaving the dialog to ask", { timeout: 30_000 }, async () => {
    const backend = newServer();
    backend.on("POST /api/auth/elevate", () => problem(401, "invalid_totp", "Invalid two-factor code."));
    holdAccessToken("noust_tok_just_typed");
    const { user } = renderConsole("/setup");
    await screen.findByRole("heading", { level: 1, name: "Create the administrator" });
    await user.type(screen.getByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));
    await screen.findByRole("heading", { level: 1, name: "Protect your account" });
    expect(backend.callsTo("POST /api/auth/accounts")).toHaveLength(1);
  });

  it("asks for a name and a password, signs in with them and offers to protect the account, which can wait", { timeout: 30_000 }, async () => {
    const backend = newServer();
    const { user, container, location } = renderConsole("/setup?next=%2Fbackups");
    await screen.findByRole("heading", { level: 1, name: "Create the administrator" });
    await expectNoAxeViolations(container, { page: true });
    // What each field is for, where it is typed: the username signs in; who it belongs to is a click away.
    const username = screen.getByLabelText(/^Username/);
    expect(username).toHaveAccessibleDescription(/^What you type to sign in/);
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/^Password/)).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByRole("button", { name: "Name and email (optional)" })).toHaveAttribute("aria-expanded", "false");
    await user.type(username, "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    // No screen that asks to sign in again with what was just typed.
    expect(await screen.findByRole("heading", { level: 1, name: "Protect your account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Sign in as/ })).toBeNull();
    expect(backend.callsTo("POST /api/auth/accounts").map((call) => call.body)).toEqual([{ username: "ana", role: "admin", password: "a long passphrase here" }]);
    expect(backend.callsTo("POST /api/auth/login").map((call) => call.body)).toEqual([{ username: "ana", password: "a long passphrase here", bearer: false }]);
    // The authenticator's code is already there, to turn on or to leave.
    expect(await screen.findByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Turn on" })).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
    await user.click(screen.getByRole("button", { name: "Set up later" }));

    await waitFor(() => {
      expect(location().pathname).toBe("/backups");
    });
  });

  it("opens the name and the email where the server refused one", { timeout: 30_000 }, async () => {
    newServer({ accountsRefused: true });
    const { user } = renderConsole("/setup");
    await user.type(await screen.findByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    // The refusal is about a field in the disclosure: it opens, and says so beside the field.
    expect(await screen.findByText("That is not an email address.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Name and email (optional)" })).toHaveAttribute("aria-expanded", "true");
  });

  it("sends the person to sign in when the new account cannot, saying why", { timeout: 30_000 }, async () => {
    newServer({ loginFails: true });
    const { user, location } = renderConsole("/setup");
    await user.type(await screen.findByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    expect(await screen.findByText("The account ana was created, but signing in with it failed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign in as ana" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/login");
    });
  });

  it("asks for a security officer only where a framework separates duties, then signs in as the administrator", { timeout: 30_000 }, async () => {
    const backend = newServer({ split: true });
    const { user } = renderConsole("/setup?next=%2Fbackups");
    await user.type(await screen.findByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Add a security officer" })).toBeInTheDocument();
    expect(screen.getByText("Why a separate account")).toBeInTheDocument();
    // Not signed in yet: the access token still holds the session while the second account is made.
    expect(backend.callsTo("POST /api/auth/login")).toHaveLength(0);
    await user.type(screen.getByLabelText(/^Username/), "sec");
    await user.type(screen.getByLabelText(/^Password/), "another long passphrase");
    await user.click(screen.getByRole("button", { name: "Create security officer" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Protect your account" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/accounts").map((call) => call.body)).toEqual([
      { username: "ana", role: "admin", password: "a long passphrase here" },
      { username: "sec", role: "security", password: "another long passphrase" },
    ]);
    // The administrator signs in, not the officer.
    expect(backend.callsTo("POST /api/auth/login").map((call) => call.body)).toEqual([{ username: "ana", password: "a long passphrase here", bearer: false }]);
  });

  it("lets the security officer wait: skipping it signs in as the administrator", { timeout: 30_000 }, async () => {
    const backend = newServer({ split: true });
    const { user } = renderConsole("/setup");
    await user.type(await screen.findByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));
    await screen.findByRole("heading", { level: 1, name: "Add a security officer" });
    await user.click(screen.getByRole("button", { name: "Skip for now" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Protect your account" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/accounts")).toHaveLength(1);
  });

  it("hands the account to the console's own gate where a second factor is required", { timeout: 30_000 }, async () => {
    newServer({ mfaRequired: true });
    const { user, location } = renderConsole("/setup?next=%2Fbackups");
    await user.type(await screen.findByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    await screen.findByRole("heading", { level: 1, name: "Set up a second factor" });
    expect(location().pathname).toBe("/welcome");
  });

  it("leaves the access token as it was when the person is not ready", async () => {
    const backend = newServer();
    const { user, location } = renderConsole("/setup?next=%2Fbackups");
    await user.click(await screen.findByRole("button", { name: "Not now" }));

    await waitFor(() => {
      expect(location().pathname).toBe("/backups");
    });
    expect(backend.callsTo("POST /api/auth/accounts")).toHaveLength(0);
  });
});
