import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
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
  backup_codes_remaining: 8,
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

/** An account's sign-in: one step, and one answer to every refusal. */
function accountBackend(answer: Record<string, unknown> = {}) {
  let signedIn = false;
  const backend = fakeBackend({
    ...signedInRoutes(ANA),
    "GET /api/auth/session": () => json(200, signedIn ? ANA : SIGNED_OUT),
    "POST /api/auth/login": (call: RecordedCall) => {
      const body = call.body as { username?: string; password?: string; totp_code?: string };
      if (body.username !== "ana" || body.password !== "correct horse battery" || body.totp_code !== CODE) {
        return problem(401, "invalid_credentials", "Invalid credentials.");
      }
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
    },
  });
  return backend;
}

async function useEmergencyAccess(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(await screen.findByRole("button", { name: "Emergency access" }));
  await screen.findByRole("heading", { level: 1, name: "Emergency access" });
}

afterEach(() => {
  toast.dismiss();
});

describe("sign-in with an account", () => {
  it("signs in with a username, a password and a code in one step, then opens the page asked for", async () => {
    const backend = accountBackend();
    const { user, location } = renderConsole("/login?next=%2Fapps%2Fshop.example.com%2Flogs");
    await user.type(await screen.findByLabelText("Username"), "ana");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(screen.getByLabelText(/Two-factor code/), CODE);
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("heading", { level: 1, name: "shop.example.com" });
    expect(location().pathname).toBe("/apps/shop.example.com/logs");
    expect(backend.callsTo("POST /api/auth/login").map((call) => call.body)).toEqual([
      { username: "ana", password: "correct horse battery", bearer: false, totp_code: CODE },
    ]);
    // Once, right after signing in: when and where the last sign-in came from.
    const notes = within(await screen.findByRole("region", { name: "Notifications" }));
    expect(await notes.findByText("Signed in as Ana García")).toBeInTheDocument();
    expect(notes.getByText(/Your last sign-in was .* from 203\.0\.113\.7\./)).toBeInTheDocument();
  });

  it("warns, and keeps the warning, when attempts failed since the last visit", async () => {
    accountBackend({ failures_since: 3, last_failure_at: "2026-09-30T01:02:00+00:00", last_failure_ip: "198.51.100.9" });
    const { user } = renderConsole("/login");
    await user.type(await screen.findByLabelText("Username"), "ana");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.type(screen.getByLabelText(/Two-factor code/), CODE);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const notes = within(await screen.findByRole("region", { name: "Notifications" }));
    expect(await notes.findByText("3 failed sign-in attempts since your last visit")).toBeInTheDocument();
    expect(notes.getByText(/The latest was .* from 198\.51\.100\.9\. If it was not you, tell a security officer\./)).toBeInTheDocument();
  });

  it("says the same thing whatever was wrong, with the server's words, and clears the secrets", async () => {
    accountBackend();
    const { user } = renderConsole("/login");
    await user.type(await screen.findByLabelText("Username"), "ana");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    const title = await screen.findByText("Could not sign in");
    const alert = title.closest<HTMLElement>("[role=alert]");
    if (alert === null) throw new Error("the refusal is not announced");
    expect(within(alert).getByText("Could not sign in")).toBeInTheDocument();
    expect(within(alert).getByText("Check your username, password and two-factor code, then try again.")).toBeInTheDocument();
    expect(within(alert).getByText("Invalid credentials.")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Username")).toHaveValue("ana");
  });

  it("asks for what is missing before sending anything", async () => {
    const backend = accountBackend();
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your username.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/login")).toHaveLength(0);
  });

  it("offers the browser's passkeys in the username field", async () => {
    accountBackend();
    renderConsole("/login");
    expect(await screen.findByLabelText("Username")).toHaveAttribute("autocomplete", "username webauthn");
  });

  it("names the server by its sign-in label only, never its hostname or version", async () => {
    accountBackend();
    renderConsole("/login");
    expect(await screen.findByText("web-01 (production)")).toBeInTheDocument();
    expect(screen.getByText("Noust console")).toBeInTheDocument();
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
    await useEmergencyAccess(user);
    await user.type(screen.getByLabelText("Access token"), TOKEN);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { level: 1, name: "Create the first account" });
    expect(location().pathname).toBe("/setup");
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    accountBackend();
    renderConsole("/login?reason=expired");
    await screen.findByLabelText("Username");
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Iniciar sesión" })).toBeInTheDocument();
    expect(screen.getByLabelText("Usuario")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acceso de emergencia" })).toBeInTheDocument();
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
    await user.click(await screen.findByRole("button", { name: "Sign in with a passkey" }));
    await screen.findByRole("heading", { level: 1, name: "Backups" });
    expect(location().pathname).toBe("/backups");
    expect(get).toHaveBeenCalledTimes(1);
    const [sent] = backend.callsTo("POST /api/auth/passkeys/login");
    expect(sent?.body).toMatchObject({ credential: { id: "CQkJ", rawId: "CQkJ", type: "public-key", response: { userHandle: "Aw" } }, bearer: false });
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
    await user.click(await screen.findByRole("button", { name: "Sign in with a passkey" }));
    await waitFor(() => {
      expect(get).toHaveBeenCalledTimes(1);
    });
    expect(screen.queryByText("Could not sign in with a passkey")).toBeNull();
  });
});

describe("emergency access with the access token", () => {
  it("says what the token is before it is typed, then asks for the second factor", async () => {
    const backend = tokenBackend();
    const { user, location } = renderConsole("/login?next=%2Fapps%2Fshop.example.com%2Flogs");
    await useEmergencyAccess(user);
    expect(screen.getByText("For recovery only")).toBeInTheDocument();
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

  it("opens on the token from a runbook's link", async () => {
    tokenBackend();
    renderConsole("/login?with=token");
    expect(await screen.findByRole("heading", { level: 1, name: "Emergency access" })).toBeInTheDocument();
    expect(screen.getByLabelText("Access token")).toBeInTheDocument();
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
    await screen.findByRole("heading", { level: 1, name: "Overview" });
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
    expect(screen.getByRole("heading", { level: 1, name: "Acceso de emergencia" })).toBeInTheDocument();
    expect(screen.getByLabelText("Código de verificación en dos pasos")).toBeInTheDocument();
    expect(screen.getByText("aceptado")).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});
