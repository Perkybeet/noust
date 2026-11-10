import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { takeAccessToken } from "./accessProof";
import { toBase64url } from "./webauthn";

const TOKEN = "noust_secret_token";
const CODE = "123456";

/** Before sign-in the server names itself only by the label its operator chose. */
const SIGNED_OUT = { ...ANONYMOUS, hostname: "web-01 (production)", version: "", login_label: "web-01 (production)" };

const ACCOUNT = {
  id: 3,
  username: "ana",
  display_name: "Ana García",
  role: "operator",
  status: "active",
  person_ref: "ana@example.com",
  mfa_enabled: true,
  passkeys: 0,
  backup_codes_remaining: 8, owner: false,
  failures_since_login: 0,
  created_at: 1_790_000_000,
};

const ANA = { ...SESSION, account: ACCOUNT, role: "operator", grant: null, permissions: ["self", "apps.read", "apps.operate", "apps.deploy"] };

/** The master token with two-factor on: a signed-out session until a login succeeds. */
function tokenBackend() {
  let signedIn = false;
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, signedIn ? SESSION : SIGNED_OUT),
    "POST /api/auth/login": (call: RecordedCall) => {
      const body = call.body as { token: string; totp_code?: string };
      if (body.token !== TOKEN) return problem(401, "invalid_token", "Invalid token.");
      if (!body.totp_code) return problem(401, "totp_required", "Two-factor authentication is enabled. Include totp_code.");
      if (body.totp_code !== CODE) return problem(401, "invalid_totp", "Invalid two-factor code.");
      signedIn = true;
      return json(200, { success: true, expires_in: 28_800, csrf_token: "c", session_token: null, grant: "break_glass" });
    },
  });
  return backend;
}

const CHALLENGE = "step-2-challenge";

/**
 * An account's sign-in in two steps: the name (or the person's email) and the password, then
 * the second factor the account has. One answer to every refusal of the first step.
 */
function accountBackend(answer: Record<string, unknown> = {}, methods: string[] = ["totp", "backup_code"]) {
  let signedIn = false;
  const signedInAnswer = () => {
    signedIn = true;
    return json(200, {
      success: true,
      expires_in: 28_800,
      csrf_token: "c",
      session_token: null,
      account: ACCOUNT,
      previous_login_at: "2026-09-29T08:15:00+00:00",
      previous_login_ip: "203.0.113.7",
      failures_since: 0,
      ...answer,
    });
  };
  const backend = fakeBackend({
    ...signedInRoutes(ANA),
    "GET /api/auth/session": () => json(200, signedIn ? ANA : SIGNED_OUT),
    "POST /api/auth/login": (call: RecordedCall) => {
      const body = call.body as { username?: string; password?: string; totp_code?: string };
      if (!["ana", "ana@example.com"].includes(body.username ?? "") || body.password !== "correct horse battery") {
        return problem(401, "invalid_credentials", "Invalid credentials.");
      }
      return json(200, { success: false, expires_in: 0, csrf_token: "", second_factor: { challenge: CHALLENGE, expires_in: 300, methods } });
    },
    "POST /api/auth/login/second-factor": (call: RecordedCall) => {
      const body = call.body as { challenge: string; code: string };
      if (body.challenge !== CHALLENGE) return problem(401, "sign_in_expired", "This sign-in has expired. Start again with your username and password.");
      if (body.code !== CODE) return problem(401, "invalid_totp", "Invalid two-factor code.");
      return signedInAnswer();
    },
  });
  return backend;
}

/** Types the first step and sends it. */
async function passwordStep(user: ReturnType<typeof renderConsole>["user"], name = "ana"): Promise<void> {
  await user.type(await screen.findByLabelText("Username or email"), name);
  await user.type(screen.getByLabelText("Password"), "correct horse battery");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

async function useAccessToken(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(await screen.findByRole("button", { name: "Access token" }));
  await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" });
}

afterEach(() => {
  toast.dismiss();
});

describe("sign-in with an account", () => {
  it("asks for the password first and the code after it, then opens the page asked for", async () => {
    const backend = accountBackend();
    const { user, location } = renderConsole("/login?next=%2Fapps%2Fshop.example.com%2Flogs");
    await screen.findByLabelText("Username or email");
    // Nothing to type for a factor the account may not have: the server says whether it does.
    expect(screen.queryByLabelText(/Two-factor code/)).toBeNull();
    await passwordStep(user);

    expect(await screen.findByText("Password accepted for ana.")).toBeInTheDocument();
    const code = screen.getByLabelText("Two-factor code");
    await waitFor(() => {
      expect(code).toHaveFocus();
    });
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    expect(screen.queryByLabelText("Password")).toBeNull();
    await user.type(code, CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByRole("heading", { level: 1, name: "shop.example.com" });
    expect(location().pathname).toBe("/apps/shop.example.com/logs");
    expect(backend.callsTo("POST /api/auth/login").map((call) => call.body)).toEqual([{ username: "ana", password: "correct horse battery", bearer: false }]);
    expect(backend.callsTo("POST /api/auth/login/second-factor").map((call) => call.body)).toEqual([{ challenge: CHALLENGE, code: CODE, bearer: false }]);
    // Once, right after signing in: when and where the last sign-in came from.
    const notes = within(await screen.findByRole("region", { name: "Notifications" }));
    expect(await notes.findByText("Signed in as Ana García")).toBeInTheDocument();
    expect(notes.getByText(/Your last sign-in was .* from 203\.0\.113\.7\./)).toBeInTheDocument();
  });

  it("signs in with the email of the person when it names one account", async () => {
    const backend = accountBackend();
    const { user } = renderConsole("/login");
    await passwordStep(user, "ana@example.com");
    await screen.findByText("Password accepted for ana@example.com.");
    expect(backend.callsTo("POST /api/auth/login")[0]?.body).toMatchObject({ username: "ana@example.com" });
  });

  it("signs an account without a second factor in on its password, and sends it to set one up", async () => {
    let signedIn = false;
    const fresh = { ...ANA, mfa_required: true, account: { ...ACCOUNT, mfa_enabled: false, backup_codes_remaining: 0 , owner: false} };
    const backend = fakeBackend({
      ...signedInRoutes(fresh),
      "GET /api/auth/session": () => json(200, signedIn ? fresh : SIGNED_OUT),
      "POST /api/auth/login": () => {
        signedIn = true;
        return json(200, { success: true, expires_in: 60, csrf_token: "c", session_token: null, account: fresh.account, mfa_required: true });
      },
    });
    const { user, location } = renderConsole("/login");
    await passwordStep(user);
    await screen.findByRole("heading", { level: 1, name: "Set up a second factor" });
    expect(location().pathname).toBe("/welcome");
    expect(backend.callsTo("POST /api/auth/login/second-factor")).toHaveLength(0);
  });

  it("says a wrong code at the code, and keeps the step open", async () => {
    const backend = accountBackend();
    const { user } = renderConsole("/login");
    await passwordStep(user);
    await user.type(await screen.findByLabelText("Two-factor code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Invalid two-factor code.")).toBeInTheDocument();
    const code = screen.getByLabelText("Two-factor code");
    expect(code).toHaveAccessibleDescription(/Invalid two-factor code\./);
    await user.clear(code);
    await user.type(code, CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await screen.findByRole("heading", { level: 1, name: "This server" });
    expect(backend.callsTo("POST /api/auth/login")).toHaveLength(1);
  });

  it("goes back to the password, in the server's words, when the step has expired", async () => {
    const backend = accountBackend();
    backend.on("POST /api/auth/login/second-factor", () =>
      problem(401, "sign_in_expired", "This sign-in has expired. Start again with your username and password."),
    );
    const { user } = renderConsole("/login");
    await passwordStep(user);
    await user.type(await screen.findByLabelText("Two-factor code"), CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("This sign-in has expired. Start again with your username and password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Username or email")).toHaveValue("ana");
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByLabelText("Two-factor code")).toBeNull();
  });

  it("goes back to the first step for another account", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await passwordStep(user);
    await user.click(await screen.findByRole("button", { name: "Use a different account" }));
    expect(await screen.findByLabelText("Username or email")).toHaveValue("ana");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("warns, and keeps the warning, when attempts failed since the last visit", async () => {
    accountBackend({ failures_since: 3, last_failure_at: "2026-09-30T01:02:00+00:00", last_failure_ip: "198.51.100.9" });
    const { user } = renderConsole("/login");
    await passwordStep(user);
    await user.type(await screen.findByLabelText("Two-factor code"), CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    const notes = within(await screen.findByRole("region", { name: "Notifications" }));
    expect(await notes.findByText("3 failed sign-in attempts since your last visit")).toBeInTheDocument();
    expect(notes.getByText(/The latest was .* from 198\.51\.100\.9\. If it was not you, tell a security officer\./)).toBeInTheDocument();
  });

  it("says the same thing whatever was wrong, with the server's words, and clears the secrets", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await user.type(await screen.findByLabelText("Username or email"), "ana");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const title = await screen.findByText("Could not sign in");
    const alert = title.closest<HTMLElement>("[role=alert]");
    if (alert === null) throw new Error("the refusal is not announced");
    expect(within(alert).getByText("Could not sign in")).toBeInTheDocument();
    expect(
      within(alert).getByText("Check your username and password, then try again. If your email is on more than one account, use the username."),
    ).toBeInTheDocument();
    expect(within(alert).getByText("Invalid credentials.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Username or email")).toHaveValue("ana");
  });

  it("asks for what is missing before sending anything", async () => {
    const backend = accountBackend();
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your username or email.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/login")).toHaveLength(0);
  });

  it("asks for the code before sending the second step", async () => {
    const backend = accountBackend();
    const { user } = renderConsole("/login");
    await passwordStep(user);
    await user.click(await screen.findByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Enter the code.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/login/second-factor")).toHaveLength(0);
  });

  it("offers the browser's passkeys in the username field, and fills the password as the saved one", async () => {
    accountBackend();
    renderConsole("/login");
    expect(await screen.findByLabelText("Username or email")).toHaveAttribute("autocomplete", "username webauthn");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autocomplete", "current-password");
  });

  it("names the server by its sign-in label only, never its hostname or version", async () => {
    accountBackend();
    renderConsole("/login");
    expect(await screen.findByText("web-01 (production)")).toBeInTheDocument();
    // Nor the product and its version, which the line above the form used to say: the slab carries the logo instead.
    expect(screen.queryByText(/Noust console/)).toBeNull();
  });

  it("sends a new server's first sign-in with the token to create its first accounts", async () => {
    let signedIn = false;
    const compat = { ...SESSION, grant: "compat", accounts_exist: false };
    fakeBackend({
      ...signedInRoutes(compat),
      "GET /api/auth/session": () => json(200, signedIn ? compat : { ...SIGNED_OUT, totp_enabled: false }),
      "POST /api/auth/login": () => {
        signedIn = true;
        return json(200, { success: true, expires_in: 60, csrf_token: "c", session_token: null, grant: "compat" });
      },
    });
    const { user, location } = renderConsole("/login");
    await useAccessToken(user);
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { level: 1, name: "Create the administrator" });
    expect(location().pathname).toBe("/setup");
    // The token that signed in is kept, for that page to confirm it's the person with it, once.
    expect(takeAccessToken()).toBe(TOKEN);
  });

  it("keeps the token for nothing else: a sign-in that does not lead to the first administrator forgets it", async () => {
    const backend = tokenBackend();
    const { user } = renderConsole("/login");
    await useAccessToken(user);
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.type(await screen.findByLabelText("Two-factor code"), CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/auth/login")).toHaveLength(2);
    });
    await waitFor(() => {
      expect(takeAccessToken()).toBeNull();
    });
  });

  it("has no accessibility violations on either step, and speaks Spanish", async () => {
    accountBackend();
    const { user } = renderConsole("/login?reason=expired");
    await screen.findByLabelText("Username or email");
    await expectNoAxeViolations(document.body, { page: true });
    await passwordStep(user);
    await screen.findByLabelText("Two-factor code");
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Un paso más" })).toBeInTheDocument();
    expect(screen.getByText("Contraseña aceptada para ana.")).toBeInTheDocument();
    expect(screen.getByLabelText("Código de verificación en dos pasos")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Usar otra cuenta" }));
    expect(await screen.findByLabelText("Usuario o correo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Token de acceso" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});

/** The browser's WebAuthn, answering `get` as the test says. */
function stubCredentials(get: () => Promise<unknown>): void {
  Object.defineProperty(navigator, "credentials", { value: { get, create: vi.fn() }, configurable: true });
}

describe("sign-in with a passkey", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("signs in with the passkey the browser offers, no name or password", async () => {
    let signedIn = false;
    const backend = fakeBackend({
      ...signedInRoutes(ANA),
      "GET /api/auth/session": () => json(200, signedIn ? ANA : SIGNED_OUT),
      "POST /api/auth/passkeys/login/options": () =>
        json(200, { public_key: { challenge: toBase64url(new Uint8Array([1, 2, 3])), rpId: "localhost", userVerification: "required" }, expires_in: 300 }),
      "POST /api/auth/passkeys/login": () => {
        signedIn = true;
        return json(200, { success: true, expires_in: 60, csrf_token: "c", session_token: null, account: ACCOUNT });
      },
    });
    const raw = new Uint8Array([9, 9, 9]).buffer;
    const credential = {
      id: toBase64url(raw),
      rawId: raw,
      type: "public-key",
      authenticatorAttachment: "platform",
      response: {
        clientDataJSON: new Uint8Array([123, 125]).buffer,
        authenticatorData: new Uint8Array([1]).buffer,
        signature: new Uint8Array([2]).buffer,
        userHandle: new Uint8Array([3]).buffer,
      },
      getClientExtensionResults: () => ({}),
    };
    const get = vi.fn(() => Promise.resolve(credential));
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", Object.assign(vi.fn(), { isConditionalMediationAvailable: () => Promise.resolve(false) }));
    stubCredentials(get);

    const { user, location } = renderConsole("/login?next=%2Fbackups");
    // The passkey is a door under the password form, which opens in the form's place.
    await user.click(await screen.findByRole("button", { name: "Passkey" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with a passkey" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Password")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Sign in with a passkey" }));
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
    expect(get).toHaveBeenCalledTimes(1);
    const [sent] = backend.callsTo("POST /api/auth/passkeys/login");
    expect(sent?.body).toMatchObject({ credential: { id: "CQkJ", rawId: "CQkJ", type: "public-key", response: { userHandle: "Aw" } }, bearer: false });
  });

  it("finishes the second step with the account's passkey, bound to that step", async () => {
    const backend = accountBackend({}, ["totp", "passkey", "backup_code"]);
    backend.on("POST /api/auth/passkeys/login/options", () =>
      json(200, { public_key: { challenge: toBase64url(new Uint8Array([1, 2, 3])), rpId: "localhost", userVerification: "required", allowCredentials: [] }, expires_in: 300 }),
    );
    backend.on("POST /api/auth/passkeys/login", () => {
      backend.on("GET /api/auth/session", () => json(200, ANA));
      return json(200, { success: true, expires_in: 60, csrf_token: "c", session_token: null, account: ACCOUNT });
    });
    const raw = new Uint8Array([9, 9, 9]).buffer;
    const get = vi.fn(() =>
      Promise.resolve({
        id: toBase64url(raw),
        rawId: raw,
        type: "public-key",
        authenticatorAttachment: "platform",
        response: { clientDataJSON: new Uint8Array([123, 125]).buffer, authenticatorData: new Uint8Array([1]).buffer, signature: new Uint8Array([2]).buffer, userHandle: null },
        getClientExtensionResults: () => ({}),
      }),
    );
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", Object.assign(vi.fn(), { isConditionalMediationAvailable: () => Promise.resolve(false) }));
    stubCredentials(get);

    const { user, location } = renderConsole("/login?next=%2Fbackups");
    await passwordStep(user);
    await user.click(await screen.findByRole("button", { name: "Use your passkey" }));
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
    expect(backend.callsTo("POST /api/auth/passkeys/login/options")[0]?.body).toEqual({ conditional: false, challenge: CHALLENGE });
    expect(backend.callsTo("POST /api/auth/passkeys/login")[0]?.body).toMatchObject({ bearer: false, challenge: CHALLENGE });
  });

  it("says nothing when the operator closes the browser's prompt", async () => {
    fakeBackend({
      "GET /api/auth/session": () => json(200, SIGNED_OUT),
      "POST /api/auth/passkeys/login/options": () => json(200, { public_key: { challenge: "AQID" }, expires_in: 300 }),
    });
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", Object.assign(vi.fn(), { isConditionalMediationAvailable: () => Promise.resolve(false) }));
    const get = vi.fn(() => Promise.reject(new DOMException("The operation either timed out or was not allowed.", "NotAllowedError")));
    stubCredentials(get);
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("button", { name: "Passkey" }));
    await user.click(await screen.findByRole("button", { name: "Sign in with a passkey" }));
    await waitFor(() => {
      expect(get).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByText("Could not sign in with a passkey")).toBeNull();
  });
});

describe("the access token", () => {
  it("is a door under the account form for whoever has no account yet, and says what it is for once chosen", async () => {
    tokenBackend();
    const { user } = renderConsole("/login");
    // Offered to everyone and always: this page cannot know whether there are accounts.
    const doors = within(await screen.findByRole("navigation", { name: "Other ways to sign in" }));
    expect(doors.getByRole("button", { name: "Access token" })).toBeInTheDocument();
    await useAccessToken(user);
    expect(screen.getByLabelText("Access token")).toHaveFocus();
    expect(screen.getByText(/The access token is the key of whoever is root on this machine\./)).toBeInTheDocument();
    expect(screen.queryByText("For recovery only")).toBeNull();
  });

  it("asks for the second factor after the token", async () => {
    const backend = tokenBackend();
    const { user, location } = renderConsole("/login?next=%2Fapps%2Fshop.example.com%2Flogs");
    await useAccessToken(user);
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const code = await screen.findByLabelText("Two-factor code");
    await waitFor(() => {
      expect(code).toHaveFocus();
    });
    expect(screen.getByText("accepted")).toBeInTheDocument();
    await user.type(code, CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByRole("heading", { level: 1, name: "shop.example.com" });
    expect(location().pathname).toBe("/apps/shop.example.com/logs");
    expect(backend.callsTo("POST /api/auth/login").map((call) => call.body)).toEqual([
      { token: TOKEN, bearer: false },
      { token: TOKEN, bearer: false, totp_code: CODE },
    ]);
  });

  it("opens on the token, with the field ready, from the link the installer prints, and warns of nothing", async () => {
    tokenBackend();
    renderConsole("/login?with=token");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByLabelText("Access token")).toHaveFocus();
    });
    expect(screen.getByText("The installer printed it once, in a box. Paste it here.")).toBeInTheDocument();
    // Whoever follows the installer's link needs no talk of recovery.
    expect(screen.queryByText(/The access token is the key of whoever is root/)).toBeNull();
    expect(screen.queryByText("For recovery only")).toBeNull();
  });

  it("tells a lost token the command that makes a new one, and what it costs", async () => {
    tokenBackend();
    renderConsole("/login?with=token");
    await screen.findByLabelText("Access token");

    // `noust web token` alone only reports; it is `--new` that gives a token, and it ends the one in use.
    expect(screen.getByText("noust web token --new")).toBeInTheDocument();
    expect(screen.queryByText("noust web token")).toBeNull();
    expect(screen.getByText(/the one in use stops working/i)).toBeInTheDocument();
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByText("noust web token --new")).toBeInTheDocument();
    expect(screen.getByText(/el que usas ahora deja de valer/i)).toBeInTheDocument();
  });

  it("closes every screen before the console with who holds the copyright and the licence", async () => {
    tokenBackend();
    renderConsole("/login?with=token");
    const legal = (await screen.findByText(/© 2024-2026 Yago López Prado/)).closest("p");
    expect(legal).not.toBeNull();
    const licence = within(legal as HTMLElement).getByRole("link", { name: /AGPL-3\.0-or-later/ });
    expect(licence).toHaveAttribute("href", "https://github.com/Perkybeet/noust/blob/main/LICENSE");
  });

  it("shows the server's words when the token or the code is wrong, and goes back to the token", async () => {
    tokenBackend();
    const { user } = renderConsole("/login?with=token");
    await user.type(await screen.findByLabelText("Access token"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Invalid token.")).toBeInTheDocument();
    expect(screen.getByLabelText("Access token")).toHaveAttribute("aria-invalid", "true");

    await user.clear(screen.getByLabelText("Access token"));
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await user.type(await screen.findByLabelText("Two-factor code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Invalid two-factor code.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use a different token" }));
    expect(await screen.findByLabelText("Access token")).toBeInTheDocument();
    expect(screen.queryByLabelText("Two-factor code")).toBeNull();
  });

  it("says how long a lockout lasts and holds the form until it ends", async () => {
    fakeBackend({
      "GET /api/auth/session": () => json(200, SIGNED_OUT),
      "POST /api/auth/login": () =>
        problem(429, "locked_out", "Too many failed attempts. Locked for 125 seconds.", { headers: { "Retry-After": "125" } }),
    });
    const { user } = renderConsole("/login?with=token");
    await user.type(await screen.findByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Too many failed attempts")).toBeInTheDocument();
    expect(screen.getByText("2:05")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });

  it("never follows a next that leaves the console", async () => {
    let signedIn = false;
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/session": () => json(200, signedIn ? SESSION : { ...SIGNED_OUT, totp_enabled: false }),
      "POST /api/auth/login": () => {
        signedIn = true;
        return json(200, { success: true, expires_in: 60, csrf_token: "c", session_token: null, grant: "break_glass" });
      },
    });
    const { user, location } = renderConsole("/login?with=token&next=%2F%2Fevil.example%2Fphish");
    await user.type(await screen.findByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { level: 1, name: "This server" });
    expect(location().pathname).toBe("/");
  });

  it("sends an operator who is already signed in straight on", async () => {
    fakeBackend(signedInRoutes());
    const { location } = renderConsole("/login?next=%2Fbackups");
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
  });

  it("has no accessibility violations on either step, and speaks Spanish", async () => {
    tokenBackend();
    const { user } = renderConsole("/login?with=token&reason=expired");
    await screen.findByLabelText("Access token");
    await expectNoAxeViolations(document.body, { page: true });
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByLabelText("Two-factor code");
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Inicia sesión con el token de acceso" })).toBeInTheDocument();
    expect(screen.getByLabelText("Código de verificación en dos pasos")).toBeInTheDocument();
    expect(screen.getByText("aceptado")).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});

describe("the ways in, as doors under the password form", () => {
  it("opens a way in the form's own place, with the way back above its title and the name kept", async () => {
    tokenBackend();
    const { user } = renderConsole("/login");
    await user.type(await screen.findByLabelText("Username or email"), "ana");
    await useAccessToken(user);
    expect(screen.queryByLabelText("Username or email")).toBeNull();
    // Above the title, as a step away from the password form and not as a footnote under the token.
    const back = screen.getByRole("button", { name: "With a password" });
    const title = screen.getByRole("heading", { level: 1 });
    expect(back.compareDocumentPosition(title) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(back);
    const heading = await screen.findByRole("heading", { level: 1, name: "Sign in" });
    // The title takes focus on arrival, as on every screen; what was typed in the form is still there.
    await waitFor(() => {
      expect(heading).toHaveFocus();
    });
    expect(screen.getByLabelText("Username or email")).toHaveValue("ana");
    expect(screen.getByRole("navigation", { name: "Other ways to sign in" })).toBeInTheDocument();
  });

  it("starts the token over from its first step when the way is opened again", async () => {
    tokenBackend();
    const { user } = renderConsole("/login");
    await useAccessToken(user);
    await user.type(screen.getByLabelText("Access token"), "noust_half");
    await user.click(screen.getByRole("button", { name: "With a password" }));
    await screen.findByLabelText("Username or email");
    await useAccessToken(user);
    expect(screen.getByLabelText("Access token")).toBeInTheDocument();
    expect(screen.queryByLabelText("Two-factor code")).toBeNull();
  });

  it("offers the token to everyone, and no door for what this browser or this server cannot do", async () => {
    accountBackend();
    renderConsole("/login");
    const doors = within(await screen.findByRole("navigation", { name: "Other ways to sign in" }));
    // Nothing in this browser can do a passkey, and no company's sign-in is configured.
    expect(doors.getAllByRole("button").map((door) => door.textContent)).toEqual(["Access token"]);
  });

  it("draws the three doors when there is a passkey, a company and the token", async () => {
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", Object.assign(vi.fn(), { isConditionalMediationAvailable: () => Promise.resolve(false) }));
    stubCredentials(() => Promise.reject(new Error("not asked")));
    try {
      const backend = accountBackend();
      backend.on("GET /api/auth/oidc/providers", () => json(200, { providers: [{ name: "corp", label: "Acme SSO" }] }));
      renderConsole("/login");
      const doors = within(await screen.findByRole("navigation", { name: "Other ways to sign in" }));
      await waitFor(() => {
        expect(doors.getAllByRole("button").map((door) => door.textContent)).toEqual(["Passkey", "Your company", "Access token"]);
      });
    } finally {
      vi.unstubAllGlobals();
      Reflect.deleteProperty(navigator, "credentials");
    }
  });

  it("says a session that ended in the password form itself, where the person is about to type", async () => {
    accountBackend();
    renderConsole("/login?reason=expired");
    const form = (await screen.findByLabelText("Username or email")).closest("form");
    if (form === null) throw new Error("the username is not in a form");
    expect(within(form).getByText("Your session expired. Sign in again to continue where you left off.")).toBeInTheDocument();
  });

  it("says, from the password's own line, who can help when the server cannot send a link", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("button", { name: "Forgot your password?" }));
    expect(await screen.findByText(/A security officer can send you a new invitation\./)).toBeInTheDocument();
  });

  it("names the second step, and leaves the doors out of it: the password was right", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await passwordStep(user);
    expect(await screen.findByRole("heading", { level: 1, name: "One more step" })).toBeInTheDocument();
    expect(screen.getByText("Confirm it's you with your second factor.")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Other ways to sign in" })).toBeNull();
    expect(screen.queryByRole("button", { name: "With a password" })).toBeNull();
  });

  it("is walked with the keyboard from the first field to the last door", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await user.click(await screen.findByLabelText("Username or email"));
    await user.tab();
    expect(screen.getByLabelText("Password")).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Forgot your password?" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Sign in" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Access token" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeInTheDocument();
  });

  it("has no accessibility violations on the password form with its doors, and speaks Spanish", async () => {
    accountBackend();
    renderConsole("/login");
    await screen.findByRole("navigation", { name: "Other ways to sign in" });
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("navigation", { name: "Otras formas de iniciar sesión" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Token de acceso" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "¿Has olvidado la contraseña?" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});
