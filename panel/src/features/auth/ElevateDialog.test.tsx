import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ElevationCancelledError, api } from "../../api/client";
import { authKeys } from "../../api/queries/auth";
import type { SessionInfo } from "../../api/queries/auth";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";

const UNTIL = "2026-09-25T18:10:00+00:00";

function backendNeedingElevation(session: SessionInfo = SESSION) {
  let elevated = false;
  const backend = fakeBackend({
    ...signedInRoutes(session),
    "POST /api/auth/elevate": (call: RecordedCall) => {
      const body = call.body as { code?: string; token?: string };
      const ok = session.totp_enabled ? body.code === "123456" : body.token === "noust_token";
      if (!ok) {
        return session.totp_enabled
          ? problem(401, "invalid_totp", "Invalid two-factor code. 4 attempts remaining.")
          : problem(401, "invalid_token", "Invalid token. 4 attempts remaining.");
      }
      elevated = true;
      return json(200, { elevated_until: UNTIL });
    },
    "DELETE /api/apps/shop.example.com": () =>
      elevated
        ? json(202, { job_id: "j1", status: "pending", message: "Deleting shop.example.com", job: {} })
        : problem(403, "elevation_required", "Confirm it's you to continue"),
  });
  return backend;
}

async function consoleOn(backend: ReturnType<typeof backendNeedingElevation>) {
  const harness = renderConsole("/apps");
  await screen.findByRole("heading", { level: 1, name: "Applications" });
  return { ...harness, backend };
}

describe("Confirm it's you", () => {
  it("opens when an action needs it, and retries the action once confirmed", async () => {
    const { user, backend, queryClient } = await consoleOn(backendNeedingElevation());
    const deletion = api("DELETE", "/api/apps/shop.example.com");

    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    const code = within(dialog).getByLabelText("Authentication code");
    await waitFor(() => {
      expect(code).toHaveFocus();
    });
    await user.type(code, "123456");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Confirm it's you" })).toBeNull();
    });
    expect(backend.callsTo("POST /api/auth/elevate").map((call) => call.body)).toEqual([{ code: "123456" }]);
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(2);
    expect(queryClient.getQueryData<SessionInfo>(authKeys.session)?.elevated_until).toBe(UNTIL);
  });

  it("stays open with the server's words when the code is wrong", async () => {
    const { user, backend } = await consoleOn(backendNeedingElevation());
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(dialog).getByLabelText("Authentication code"), "111111");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("Invalid two-factor code. 4 attempts remaining.")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Confirm it's you" })).toBeInTheDocument();
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(1);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await expect(deletion).resolves.toBeInstanceOf(ElevationCancelledError);
  });

  it("fails the action with a clear error when cancelled, without retrying it", async () => {
    const { user, backend } = await consoleOn(backendNeedingElevation());
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.keyboard("{Escape}");
    const error = await deletion;
    expect(error).toBeInstanceOf(ElevationCancelledError);
    expect((error as ElevationCancelledError).detail).toBe("Nothing was changed because the confirmation was cancelled.");
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(1);
    expect(backend.callsTo("POST /api/auth/elevate")).toHaveLength(0);
  });

  it("asks for the access token when two-factor authentication is off", async () => {
    const { user, backend } = await consoleOn(backendNeedingElevation({ ...SESSION, totp_enabled: false }));
    const deletion = api("DELETE", "/api/apps/shop.example.com");
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    const token = within(dialog).getByLabelText("Access token");
    expect(token).toHaveAttribute("type", "password");
    await user.type(token, "noust_token{Enter}");
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/elevate").map((call) => call.body)).toEqual([{ token: "noust_token" }]);
  });

  it("has no accessibility violations", async () => {
    const { user } = await consoleOn(backendNeedingElevation());
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch(() => undefined);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await deletion;
  });

  it("speaks Spanish once the language switches", async () => {
    const { user } = await consoleOn(backendNeedingElevation());
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch(() => undefined);
    await act(async () => {
      await setLocale("es");
    });
    const dialog = await screen.findByRole("dialog", { name: "Confirma que eres tú" });
    expect(within(dialog).getByLabelText("Código de autenticación")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Continuar" })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await deletion;
  });
});

const PERSON = {
  ...SESSION,
  grant: null,
  role: "admin",
  elevation_factors: ["totp", "backup_code"],
  account: {
    id: 4,
    username: "dani",
    display_name: "",
    role: "admin",
    status: "active",
    mfa_enabled: true,
    passkeys: 1,
    backup_codes_remaining: 8, owner: false,
    failures_since_login: 0,
    created_at: 1_790_000_000,
  },
};

/** An action that is refused until the session confirms it's them. */
const NEEDS_ELEVATION = {
  "DELETE /api/apps/shop.example.com": () => problem(403, "elevation_required", "Confirm it's you"),
};

/** An account whose second factor was left for later: nothing to confirm an action with yet. */
const WITHOUT_FACTOR = {
  ...PERSON,
  totp_enabled: false,
  elevation_factors: [] as string[],
  account: { ...PERSON.account, mfa_enabled: false, passkeys: 0, backup_codes_remaining: 0 },
};

const ENROLMENT = { secret: "JBSWY3DPEHPK3PXP", uri: "otpauth://totp/Noust:dani@web-01?secret=JBSWY3DPEHPK3PXP&issuer=Noust" };
const BACKUP_CODES = ["abcd2345wx", "efgh6789yz", "jkmn2345pq", "rstu6789vw", "xyza2345bc", "defg6789hj", "kmnp2345rs", "tuvw6789xy"];

describe("Confirm it's you, for an account that has no second factor yet (item 131)", () => {
  /** The server as an account in that state meets it: the factor can be set up, and then confirms. */
  function backendOfSomeoneWithoutAFactor(extra: Record<string, (call: RecordedCall) => Response> = {}) {
    let enrolled = false;
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes(WITHOUT_FACTOR),
      "GET /api/auth/session": () => json(200, enrolled ? { ...PERSON, elevation_factors: ["totp", "backup_code"], elevated_until: elevated ? UNTIL : null } : WITHOUT_FACTOR),
      "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "insecure_context", detail: "", hint: "" }, allow_synced: true }),
      "POST /api/auth/2fa/enroll": () => json(200, ENROLMENT),
      "POST /api/auth/2fa/confirm": () => {
        enrolled = true;
        return json(200, { success: true, backup_codes: BACKUP_CODES });
      },
      "POST /api/auth/elevate": (call: RecordedCall) => {
        if ((call.body as { code?: string }).code !== "654321") return problem(401, "invalid_totp", "Invalid two-factor code.");
        elevated = true;
        return json(200, { elevated_until: UNTIL });
      },
      "DELETE /api/apps/shop.example.com": () => (elevated ? json(202, { job_id: "j1" }) : problem(403, "elevation_required", "Confirm it's you")),
      ...extra,
    });
    return backend;
  }

  it("says so without blame, sets the factor up in the same dialog and carries on with the action", { timeout: 20_000 }, async () => {
    const backend = backendOfSomeoneWithoutAFactor();
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com");

    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    // Not a field for a code nobody has: what is missing, and that it takes a minute to put right.
    expect(await within(dialog).findByText(/no second factor yet/)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Authentication code")).toBeNull();
    // How long a confirmation lasts is for after there is one.
    expect(within(dialog).queryByText(/won't be asked again/)).toBeNull();
    await expectNoAxeViolations(dialog);

    // The ways to set a factor up are drawn once the dialog knows which this browser has: wait for the button.
    await user.click(await within(dialog).findByRole("button", { name: "Use an authenticator app" }));
    await user.type(await within(dialog).findByLabelText("Authentication code"), "123456");
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    // The codes are shown once, and the action waits behind them.
    await user.click(await within(dialog).findByRole("checkbox", { name: "I have saved these codes somewhere safe" }));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    // The dialog becomes the one every account has, in the same place, and says the factor is on.
    expect(await within(dialog).findByText(/Your second factor is on/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText("Authentication code"), "654321");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/2fa/confirm")).toHaveLength(1);
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(2);
  });

  it("asks for the password over itself when the session is old, and is still there, with its action, once it is given", { timeout: 20_000 }, async () => {
    let asked = 0;
    const backend = backendOfSomeoneWithoutAFactor({
      "POST /api/auth/2fa/enroll": (call: RecordedCall) => {
        asked += 1;
        return (call.body as { password?: string } | null)?.password === undefined ? problem(403, "identity_proof_required", "Confirm your password to set up a second factor") : json(200, ENROLMENT);
      },
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await within(dialog).findByText(/no second factor yet/);

    await user.click(await within(dialog).findByRole("button", { name: "Use an authenticator app" }));
    const proof = await screen.findByRole("dialog", { name: "Confirm your password" });
    await user.type(within(proof).getByLabelText("Password"), "correct horse");
    await user.click(within(proof).getByRole("button", { name: "Continue" }));

    // The press inside the second dialog was outside the first, which is not a reason to close it.
    expect(await within(dialog).findByLabelText("Authentication code")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Confirm it's you" })).toBeInTheDocument();
    expect(asked).toBe(2);
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(1);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await deletion;
  });

  it("can be left: the action is not run and nothing is set up", async () => {
    const backend = backendOfSomeoneWithoutAFactor();
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);

    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await within(dialog).findByText(/no second factor yet/);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await expect(deletion).resolves.toBeInstanceOf(ElevationCancelledError);
    expect(backend.callsTo("POST /api/auth/2fa/enroll")).toHaveLength(0);
    expect(backend.callsTo("DELETE /api/apps/shop.example.com")).toHaveLength(1);
  });

  it("says it in Spanish, in the words of a person and not of a rule", async () => {
    await act(() => setLocale("es"));
    backendOfSomeoneWithoutAFactor();
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Aplicaciones" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);

    const dialog = await screen.findByRole("dialog", { name: "Confirma que eres tú" });
    expect(await within(dialog).findByText(/todavía no tiene segundo factor/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Usar una app de autenticación" })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await deletion;
  });
});

describe("the password the server asks for before the first factor (item 132)", () => {
  const ENROL_ENDPOINT = "POST /api/auth/2fa/enroll";

  it("is asked in a dialog, and the call that needed it goes again with it", { timeout: 20_000 }, async () => {
    let asked = 0;
    const backend = fakeBackend({
      ...signedInRoutes(WITHOUT_FACTOR),
      [ENROL_ENDPOINT]: (call: RecordedCall) => {
        asked += 1;
        const body = call.body as { password?: string } | undefined;
        if (body?.password === undefined) return problem(403, "identity_proof_required", "Confirm your password to set up a second factor");
        return body.password === "correct horse" ? json(200, ENROLMENT) : problem(401, "invalid_credentials", "Invalid credentials.");
      },
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const enrolment = api("POST", "/api/auth/2fa/enroll");

    const dialog = await screen.findByRole("dialog", { name: "Confirm your password" });
    expect(dialog).toHaveAccessibleDescription(/signed in a while ago/);
    await expectNoAxeViolations(dialog);
    await user.type(within(dialog).getByLabelText("Password"), "correct horse");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    await expect(enrolment).resolves.toMatchObject({ secret: ENROLMENT.secret });
    expect(asked).toBe(2);
    expect(backend.callsTo(ENROL_ENDPOINT).map((call) => call.body)).toEqual([undefined, { password: "correct horse" }]);
  });

  it("is a wrong password the server's answer, and the call is not sent a third time", { timeout: 20_000 }, async () => {
    const backend = fakeBackend({
      ...signedInRoutes(WITHOUT_FACTOR),
      [ENROL_ENDPOINT]: (call: RecordedCall) =>
        (call.body as { password?: string } | undefined)?.password === undefined
          ? problem(403, "identity_proof_required", "Confirm your password to set up a second factor")
          : problem(401, "invalid_credentials", "Invalid credentials."),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const enrolment = api("POST", "/api/auth/2fa/enroll").catch((error: unknown) => error);

    const dialog = await screen.findByRole("dialog", { name: "Confirm your password" });
    await user.type(within(dialog).getByLabelText("Password"), "nope");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));

    expect(await enrolment).toMatchObject({ status: 401, error: "invalid_credentials" });
    expect(backend.callsTo(ENROL_ENDPOINT)).toHaveLength(2);
  });

  it("can be declined: the call fails as a confirmation declined does, and nothing is sent again", async () => {
    const backend = fakeBackend({
      ...signedInRoutes(WITHOUT_FACTOR),
      [ENROL_ENDPOINT]: () => problem(403, "identity_proof_required", "Confirm your password to set up a second factor"),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const enrolment = api("POST", "/api/auth/2fa/enroll").catch((error: unknown) => error);

    const dialog = await screen.findByRole("dialog", { name: "Confirm your password" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(await enrolment).toBeInstanceOf(ElevationCancelledError);
    expect(backend.callsTo(ENROL_ENDPOINT)).toHaveLength(1);
  });
});

/** The server asks for the password again: `auth.sudo.require_password`, and always under ENS. */
const PERSON_WITH_PASSWORD = { ...PERSON, elevation_requires_password: true };

describe("Confirm it's you, for a person", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("asks for a code alone, since the session already proved the password", async () => {
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes(PERSON),
      "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "ip_address" }, allow_synced: true }),
      "POST /api/auth/elevate": (call: RecordedCall) => {
        const body = call.body as { password?: string; code?: string };
        if (body.password !== undefined || body.code !== "123456") return problem(401, "invalid_totp", "Invalid two-factor code.");
        elevated = true;
        return json(200, { elevated_until: UNTIL });
      },
      "DELETE /api/apps/shop.example.com": () => (elevated ? json(202, { job_id: "j1" }) : problem(403, "elevation_required", "Confirm it's you")),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com");
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toHaveAccessibleDescription(/Enter a code from your authenticator app/);
    expect(within(dialog).queryByLabelText("Password")).toBeNull();
    const code = within(dialog).getByLabelText("Authentication code");
    await waitFor(() => {
      expect(code).toHaveFocus();
    });
    await user.type(code, "000000");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("Invalid two-factor code.")).toBeInTheDocument();
    // The code that signed in is refused too: the field says to wait for the next one.
    expect(within(dialog).getByLabelText("Authentication code")).toHaveAccessibleDescription(/wait for the next one/);
    // A wrong code does not lose the dialog.
    await user.clear(within(dialog).getByLabelText("Authentication code"));
    await user.type(within(dialog).getByLabelText("Authentication code"), "123 456");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/elevate").at(-1)?.body).toEqual({ code: "123456" });
  });

  it("says how long the confirmation lasts, with the server's own numbers", async () => {
    fakeBackend({
      ...signedInRoutes(PERSON),
      ...NEEDS_ELEVATION,
      "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "ip_address" }, allow_synced: true }),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(await within(dialog).findByText("You won't be asked again while you keep working, for up to 2 hours. It closes after 15 minutes without a change.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await deletion;
  });

  it("asks for the password with the code when the server asks for it again, and has no accessibility violations", async () => {
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes({ ...PERSON_WITH_PASSWORD, elevation_idle_minutes: 10, elevation_max_minutes: 30 }),
      "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "ip_address" }, allow_synced: true }),
      "POST /api/auth/elevate": (call: RecordedCall) => {
        const body = call.body as { password?: string; code?: string };
        if (body.password !== "pw" || body.code !== "123456") return problem(401, "invalid_credentials", "Invalid credentials.");
        elevated = true;
        return json(200, { elevated_until: UNTIL });
      },
      "DELETE /api/apps/shop.example.com": () => (elevated ? json(202, { job_id: "j1" }) : problem(403, "elevation_required", "Confirm it's you")),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com");
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(dialog).toHaveAccessibleDescription(/your password and a code/);
    expect(await within(dialog).findByText("You won't be asked again while you keep working, for up to 30 minutes. It closes after 10 minutes without a change.")).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.type(within(dialog).getByLabelText("Password"), "nope");
    await user.type(within(dialog).getByLabelText("Authentication code"), "123456");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("Invalid credentials.")).toBeInTheDocument();
    // The session is not lost for a wrong password: the dialog stays.
    await user.clear(within(dialog).getByLabelText("Password"));
    await user.type(within(dialog).getByLabelText("Password"), "pw");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/elevate").at(-1)?.body).toEqual({ password: "pw", code: "123456" });
  });

  it("has no accessibility violations asking for a code alone, and speaks Spanish", async () => {
    fakeBackend({
      ...signedInRoutes(PERSON),
      ...NEEDS_ELEVATION,
      "GET /api/auth/passkeys": () => json(200, { passkeys: [], availability: { supported: false, reason: "ip_address" }, allow_synced: true }),
    });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await expectNoAxeViolations(dialog);
    await act(async () => {
      await setLocale("es");
    });
    const spanish = await screen.findByRole("dialog", { name: "Confirma que eres tú" });
    expect(within(spanish).queryByLabelText("Contraseña")).toBeNull();
    expect(within(spanish).getByLabelText("Código de autenticación")).toBeInTheDocument();
    expect(await within(spanish).findByText("No te lo pediremos de nuevo mientras sigas trabajando, durante un máximo de 2 horas. Se cierra tras 15 minutos sin cambios.")).toBeInTheDocument();
    await expectNoAxeViolations(spanish);
    await user.click(within(spanish).getByRole("button", { name: "Cancelar" }));
    await deletion;
  });

  it("offers the passkey first, and confirms with it", async () => {
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes(PERSON),
      "GET /api/auth/passkeys": () =>
        json(200, {
          passkeys: [{ id: 1, name: "Laptop", owner: "dani", rp_id: "localhost", algorithm: "ES256", synced: false, transports: [], created_at: 1_790_000_000 }],
          availability: { supported: true, rp_id: "localhost" },
          allow_synced: true,
        }),
      "POST /api/auth/passkeys/elevate/options": () => json(200, { public_key: { challenge: "AQID", allowCredentials: [{ type: "public-key", id: "CQkJ" }] }, expires_in: 300 }),
      "POST /api/auth/passkeys/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: UNTIL });
      },
      "DELETE /api/apps/shop.example.com": () => (elevated ? json(202, { job_id: "j1" }) : problem(403, "elevation_required", "Confirm it's you")),
    });
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", vi.fn());
    const get = vi.fn(() =>
      Promise.resolve({
        rawId: new Uint8Array([9, 9, 9]).buffer,
        type: "public-key",
        response: { clientDataJSON: new Uint8Array([1]).buffer, authenticatorData: new Uint8Array([2]).buffer, signature: new Uint8Array([3]).buffer, userHandle: null },
      }),
    );
    Object.defineProperty(navigator, "credentials", { value: { get, create: vi.fn() }, configurable: true });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com");
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(await within(dialog).findByRole("button", { name: "Confirm with a passkey" }));
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(get).toHaveBeenCalledTimes(1);
    expect(backend.callsTo("POST /api/auth/passkeys/elevate")[0]?.body).toMatchObject({ credential: { id: "CQkJ", type: "public-key" } });
    await expectNoAxeViolations(document.body, { page: true });
  });

  it("offers only the passkey to an account that has nothing else to type", async () => {
    fakeBackend({
      ...signedInRoutes({ ...PERSON, elevation_factors: ["passkey"] }),
      ...NEEDS_ELEVATION,
      "GET /api/auth/passkeys": () =>
        json(200, {
          passkeys: [{ id: 1, name: "Laptop", owner: "dani", rp_id: "localhost", algorithm: "ES256", synced: false, transports: [], created_at: 1_790_000_000 }],
          availability: { supported: true, rp_id: "localhost" },
          allow_synced: true,
        }),
    });
    vi.stubGlobal("isSecureContext", true);
    vi.stubGlobal("PublicKeyCredential", vi.fn());
    Object.defineProperty(navigator, "credentials", { value: { get: vi.fn(), create: vi.fn() }, configurable: true });
    const { user } = renderConsole("/apps");
    await screen.findByRole("heading", { level: 1, name: "Applications" });
    const deletion = api("DELETE", "/api/apps/shop.example.com").catch((error: unknown) => error);
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    expect(await within(dialog).findByRole("button", { name: "Confirm with a passkey" })).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Authentication code")).toBeNull();
    expect(within(dialog).queryByRole("button", { name: "Continue" })).toBeNull();
    expect(dialog).toHaveAccessibleDescription("This action needs a recent confirmation. Use your passkey.");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await deletion;
  });
});
