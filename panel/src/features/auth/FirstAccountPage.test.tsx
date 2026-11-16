import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { holdAccessToken, takeAccessToken } from "./accessProof";
import { factorOffer, putOff, returnOf, standing } from "./later";

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
  /** The access token has a second factor: it cannot confirm on its own. */
  totp?: boolean;
}

/**
 * A server with no accounts, entered with the access token. Making the administrator signs in with
 * it, so from then on the session is the account's.
 */
function newServer({ split = false, mfaRequired = false, loginFails = false, accountsRefused = false, totp = false }: Options = {}) {
  let account: typeof ACCOUNT | null = null;
  let signedOut = false;
  const compat = { ...SESSION, grant: "compat", accounts_exist: false, totp_enabled: totp };
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
    await screen.findByRole("heading", { level: 1, name: "Create your administrator account" });
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

  it("does not try the token alone when it has a second factor: the dialog asks for the code, and nothing is refused first", { timeout: 30_000 }, async () => {
    const backend = newServer({ totp: true });
    backend.on("POST /api/auth/elevate", () => problem(401, "invalid_totp", "Invalid two-factor code."));
    holdAccessToken("noust_tok_just_typed");
    const { user } = renderConsole("/setup");
    await screen.findByRole("heading", { level: 1, name: "Create your administrator account" });
    await user.type(screen.getByLabelText(/^Username/), "ana");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));
    await screen.findByRole("heading", { level: 1, name: "Protect your account" });
    expect(backend.callsTo("POST /api/auth/elevate")).toHaveLength(0);
    expect(backend.callsTo("POST /api/auth/accounts")).toHaveLength(1);
  });

  it("asks for a name and a password, signs in with them and offers to protect the account, which can wait", { timeout: 30_000 }, async () => {
    const backend = newServer();
    const { user, container, location } = renderConsole("/setup?next=%2Fbackups");
    await screen.findByRole("heading", { level: 1, name: "Create your administrator account" });
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
    // What leaving it for later costs, said before it is chosen (item 131).
    expect(screen.getByText(/until you have it you cannot confirm delicate actions/)).toBeInTheDocument();
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

  describe("what the page says of itself and of leaving it (item 158)", () => {
    it("says the account is the person's own, and what the other account's password is for", { timeout: 30_000 }, async () => {
      newServer({ split: true });
      const { user } = renderConsole("/setup");
      expect(await screen.findByText(/The one you create now is yours, the administrator's/)).toBeInTheDocument();
      // The administrator is the person at the screen: its password is theirs to change, not "the person's".
      expect(screen.getByLabelText(/^Password/)).toHaveAccessibleDescription(/^Long and unique\. You can change it later from your account/);
      expect(screen.queryByText(/The person changes it/)).toBeNull();
      await user.type(screen.getByLabelText(/^Username/), "ana");
      await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
      await user.click(screen.getByRole("button", { name: "Create administrator" }));
      await screen.findByRole("heading", { level: 1, name: "Add a security officer" });
      // The security officer is another person's account: whoever holds it changes the password.
      expect(screen.getByLabelText(/^Password/)).toHaveAccessibleDescription(/^Whoever holds this account changes it after signing in/);
    });

    it("tells a person who is not the administrator that Not now changes nothing, and that the offer comes back once", async () => {
      newServer();
      renderConsole("/setup");
      expect(await screen.findByText(/If you do not manage this server, choose Not now: nothing changes, and this offer comes back once, a week from now\./)).toBeInTheDocument();
    });

    it("remembers Not now in this browser, so the next sign-in does not bring the page back", async () => {
      newServer();
      const { user } = renderConsole("/setup?next=%2Fbackups");
      expect(standing("account")).toBe("new");
      await user.click(await screen.findByRole("button", { name: "Not now" }));
      expect(standing("account")).toBe("waiting");
    });

    it("remembers Set up later for that account, and for no other", { timeout: 30_000 }, async () => {
      newServer();
      const { user, location } = renderConsole("/setup?next=%2Fbackups");
      await user.type(await screen.findByLabelText(/^Username/), "ana");
      await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
      await user.click(screen.getByRole("button", { name: "Create administrator" }));
      await screen.findByRole("heading", { level: 1, name: "Protect your account" });
      await user.click(await screen.findByRole("button", { name: "Set up later" }));
      await waitFor(() => {
        expect(location().pathname).toBe("/backups");
      });
      expect(standing(factorOffer("ana"))).toBe("waiting");
      expect(standing(factorOffer("marcos"))).toBe("new");
      // The first account's offer was made and answered by making the account: it is not put off.
      expect(standing("account")).toBe("new");
    });
  });

  describe("the second factor coming back (item 158)", () => {
    /** An account that exists, signed in, with no second factor yet. */
    function existingAccount(over: Record<string, unknown> = {}) {
      const account = { ...ACCOUNT, mfa_enabled: false, owner: false };
      const session = { ...SESSION, grant: null, role: "admin", accounts_exist: true, account, mfa_required: false, totp_enabled: false, ...over };
      return fakeBackend({ ...signedInRoutes(session), "GET /api/compliance": () => json(200, compliance(false)), "POST /api/auth/2fa/enroll": () => json(200, ENROLLMENT) });
    }

    it("opens on the second factor alone, with no steps to count and no administrator to make", async () => {
      existingAccount();
      const { container } = renderConsole("/setup?step=protect&next=%2Fbackups");
      expect(await screen.findByRole("heading", { level: 1, name: "Protect your account" })).toBeInTheDocument();
      expect(screen.queryByRole("list", { name: /steps/i })).toBeNull();
      expect(screen.queryByLabelText(/^Username/)).toBeNull();
      expect(screen.queryByRole("button", { name: "Not now" })).toBeNull();
      expect(await screen.findByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
      await expectNoAxeViolations(container, { page: true });
    });

    it("lets it be put off again, and then it does not come back a third time", async () => {
      existingAccount();
      putOff(factorOffer("ana"), Date.now() - 8 * 24 * 60 * 60 * 1000);
      expect(returnOf(factorOffer("ana"))).toBe(true);
      const { user, location } = renderConsole("/setup?step=protect&next=%2Fbackups");
      // The authenticator's code replaces the first draw of the step (which offers the ways): the button is the one that stays.
      await screen.findByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP");
      await user.click(screen.getByRole("button", { name: "Set up later" }));
      await waitFor(() => {
        expect(location().pathname).toBe("/backups");
      });
      expect(standing(factorOffer("ana"))).toBe("spent");
    });

    it("sends a person who is already protected, or has no account, straight on: a stale link strands nobody", async () => {
      existingAccount({ account: { ...ACCOUNT, mfa_enabled: true } });
      const protectedOne = renderConsole("/setup?step=protect&next=%2Fbackups");
      await waitFor(() => {
        expect(protectedOne.location().pathname).toBe("/backups");
      });
      protectedOne.unmount();

      newServer();
      const token = renderConsole("/setup?step=protect&next=%2Fbackups");
      await waitFor(() => {
        expect(token.location().pathname).toBe("/backups");
      });
    });
  });
});
