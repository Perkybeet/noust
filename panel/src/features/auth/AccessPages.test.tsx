import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { codeFromHash } from "./InvitePage";

const NOW = Date.now() / 1000;
const ENROLLMENT = {
  secret: "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
  uri: "otpauth://totp/Noust:ana%40web-01?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Noust",
};
const CODES = ["1a2b-3c4d", "5e6f-7a8b", "9c0d-1e2f", "3a4b-5c6d", "7e8f-9a0b", "1c2d-3e4f", "5a6b-7c8d", "9e0f-1a2b"];
const ACCOUNT = {
  id: 3,
  username: "ana",
  display_name: "Ana",
  role: "operator",
  status: "active",
  mfa_enabled: false,
  passkeys: 0,
  backup_codes_remaining: 0,
  failures_since_login: 0,
  created_at: NOW,
};

describe("the checks after sign-in", () => {
  it("has a new account enrol a second factor, then accept the notice, then opens the page asked for", { timeout: 30_000 }, async () => {
    let enrolled = false;
    let accepted = false;
    const session = () => ({
      ...SESSION,
      grant: null,
      role: "operator",
      account: { ...ACCOUNT, mfa_enabled: enrolled },
      mfa_required: !enrolled,
      notice: accepted ? null : { text: "Use of this system is monitored.\nAccess only what your duties need.", version: "v1" },
    });
    const backend = fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/session": () => json(200, session()),
      "POST /api/auth/2fa/enroll": () => json(200, ENROLLMENT),
      "POST /api/auth/2fa/confirm": (call: RecordedCall) => {
        if ((call.body as { code: string }).code !== "123456") return problem(400, "validation_error", "That code was not accepted.");
        enrolled = true;
        return json(200, { success: true, backup_codes: CODES });
      },
      "POST /api/auth/notice/accept": () => {
        accepted = true;
        return json(200, { success: true, message: "accepted" });
      },
    });
    const { user, location, container } = renderConsole("/backups");
    expect(await screen.findByRole("heading", { level: 1, name: "Set up a second factor" })).toBeInTheDocument();
    expect(location().pathname).toBe("/welcome");
    // Two checks, in order: said by the stepper.
    expect(screen.getByRole("list", { name: /./ })).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });

    await user.click(screen.getByRole("button", { name: "Use an authenticator app" }));
    expect(await screen.findByText("JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP")).toBeInTheDocument();
    expect(screen.getByText("Noust:ana@web-01")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: "Turn on" }));

    const codes = await screen.findByRole("list", { name: "Backup codes" });
    expect(within(codes).getAllByRole("listitem")).toHaveLength(8);
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("Save the codes and tick the box first. They cannot be shown again.")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "I have saved these codes somewhere safe" }));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Terms of use" })).toBeInTheDocument();
    expect(screen.getByText(/Use of this system is monitored\./)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));
    expect(await screen.findByText("Tick the box to accept the terms of use.")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "I have read the terms of use and accept them" }));
    await user.click(screen.getByRole("button", { name: "Accept and continue" }));

    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
    expect(backend.callsTo("POST /api/auth/notice/accept").map((call) => call.body)).toEqual([{ version: "v1" }]);
  });

  it("leaves nothing to do here for a session with no checks pending", async () => {
    fakeBackend(signedInRoutes());
    const { location } = renderConsole("/welcome?next=%2Fbackups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
  });
});

describe("the first accounts", () => {
  it("creates an administrator, recommends a security officer, then signs in as the person", { timeout: 30_000 }, async () => {
    let signedIn = true;
    const compat = { ...SESSION, grant: "compat", accounts_exist: false };
    const backend = fakeBackend({
      ...signedInRoutes(compat),
      "GET /api/auth/session": () => json(200, signedIn ? compat : ANONYMOUS),
      "POST /api/auth/elevate": () => json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() }),
      "POST /api/auth/accounts": (call: RecordedCall) => {
        const body = call.body as { username: string; role: string };
        return json(201, { ...ACCOUNT, id: body.role === "admin" ? 1 : 2, username: body.username, role: body.role });
      },
      "POST /api/auth/logout": () => {
        signedIn = false;
        return json(200, { success: true, message: "Signed out" });
      },
    });
    const { user, container, location } = renderConsole("/setup?next=%2Fbackups");
    await screen.findByRole("heading", { level: 1, name: "Create the first account" });
    await expectNoAxeViolations(container, { page: true });
    // What each field is for, where it is typed: the username signs in, the email is the person.
    const username = screen.getByLabelText(/^Username/);
    expect(username).toHaveAccessibleDescription(/^What you type to sign in/);
    expect(username).toHaveAttribute("autocomplete", "username");
    expect(screen.getByLabelText(/^Email/)).toHaveAccessibleDescription(/^Identifies the person, to keep one person's accounts apart\./);
    expect(screen.getByLabelText(/^Password/)).toHaveAttribute("autocomplete", "new-password");
    await user.type(username, "ana");
    await user.type(screen.getByLabelText(/^Email/), "ana@example.com");
    await user.type(screen.getByLabelText(/^Password/), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Create administrator" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Add a security officer" })).toBeInTheDocument();
    expect(screen.getByText("Why a separate account")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Username/), "sec");
    await user.type(screen.getByLabelText(/^Password/), "another long passphrase");
    await user.click(screen.getByRole("button", { name: "Create security officer" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Accounts created" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/accounts").map((call) => call.body)).toEqual([
      { username: "ana", role: "admin", password: "a long passphrase here", person_ref: "ana@example.com" },
      { username: "sec", role: "security", password: "another long passphrase" },
    ]);
    await user.click(screen.getByRole("button", { name: "Sign in as ana" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/login");
    });
  });
});

describe("an invitation", () => {
  it("reads the code from the link, sets the password and the authenticator, and shows the backup codes once", { timeout: 30_000 }, async () => {
    const backend = fakeBackend({
      "GET /api/auth/session": () => json(200, { ...ANONYMOUS, login_label: "web-01" }),
      "POST /api/auth/invitations/open": (call: RecordedCall) =>
        (call.body as { code: string }).code === "noust_inv_abc"
          ? json(200, {
              username: "fer",
              display_name: "Fer",
              role: "operator",
              totp_secret: ENROLLMENT.secret,
              totp_uri: "otpauth://totp/Noust:fer%40web-01?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=Noust",
              password_min_length: 14,
              notice: { text: "Use of this system is monitored.", version: "v1" },
            })
          : problem(401, "invalid_invitation", "This invitation is not valid: it may have expired or been used.", { hint: "Ask for a new invitation." }),
      "POST /api/auth/invitations/accept": () => json(200, { username: "fer", backup_codes: CODES }),
    });
    window.location.hash = "#noust_inv_abc";
    const { user, container } = renderConsole("/invite");
    expect(await screen.findByRole("heading", { level: 1, name: "Accept an invitation" })).toBeInTheDocument();
    expect(screen.getByLabelText("Invitation code")).toHaveValue("noust_inv_abc");
    await user.click(screen.getByRole("button", { name: "Open invitation" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Set up your account" })).toBeInTheDocument();
    expect(screen.getByText("Noust:fer@web-01")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
    // The new password is saved under the name it signs in with, not whatever the manager guesses.
    expect(container.querySelector('input[autocomplete="username"]')).toHaveValue("fer");
    expect(screen.getByLabelText(/^Password$/)).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Authentication code")).toHaveAttribute("autocomplete", "one-time-code");
    await user.type(screen.getByLabelText(/^Password$/), "a long passphrase here");
    await user.type(screen.getByLabelText("Password again"), "a different one");
    await user.type(screen.getByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: "Activate account" }));
    expect(await screen.findByText("The two passwords are not the same.")).toBeInTheDocument();
    expect(screen.getByText("Tick the box to accept the terms of use.")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Password again"));
    await user.type(screen.getByLabelText("Password again"), "a long passphrase here");
    await user.click(screen.getByRole("checkbox", { name: "I have read the terms of use and accept them" }));
    await user.click(screen.getByRole("button", { name: "Activate account" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Your account is ready" })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Backup codes" })).getAllByRole("listitem")).toHaveLength(8);
    expect(backend.callsTo("POST /api/auth/invitations/accept").map((call) => call.body)).toEqual([
      { code: "noust_inv_abc", password: "a long passphrase here", totp_code: "123456", notice_version: "v1" },
    ]);
  });

  it("says a used or expired invitation opens nothing, with the fix, and speaks Spanish", async () => {
    fakeBackend({
      "GET /api/auth/session": () => json(200, ANONYMOUS),
      "POST /api/auth/invitations/open": () =>
        problem(401, "invalid_invitation", "This invitation is not valid: it may have expired or been used.", { hint: "Ask for a new invitation." }),
    });
    window.location.hash = "";
    const { user, location } = renderConsole("/invite");
    await user.type(await screen.findByLabelText("Invitation code"), "noust_inv_old");
    await user.click(screen.getByRole("button", { name: "Open invitation" }));
    expect(await screen.findByText("This invitation is not valid: it may have expired or been used.")).toBeInTheDocument();
    expect(screen.getByText("Ask for a new invitation.")).toBeInTheDocument();
    // A wrong code is a credential refused, not a lost session: the page stays.
    expect(location().pathname).toBe("/invite");
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Aceptar una invitación" })).toBeInTheDocument();
  });

  it("takes the code from the link in either form", () => {
    expect(codeFromHash("#noust_inv_abc")).toBe("noust_inv_abc");
    expect(codeFromHash("#code=noust_inv_abc")).toBe("noust_inv_abc");
    expect(codeFromHash("")).toBe("");
  });
});
