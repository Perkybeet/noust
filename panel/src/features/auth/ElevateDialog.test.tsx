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
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

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
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
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
    expect(within(dialog).getByRole("button", { name: "Confirmar" })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Cancelar" }));
    await deletion;
  });
});

const PERSON = {
  ...SESSION,
  grant: null,
  role: "admin",
  account: {
    id: 4,
    username: "dani",
    display_name: "",
    role: "admin",
    status: "active",
    mfa_enabled: true,
    passkeys: 1,
    backup_codes_remaining: 8,
    failures_since_login: 0,
    created_at: 1_790_000_000,
  },
};

describe("Confirm it's you, for a person", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(navigator, "credentials");
  });

  it("asks for the password and a code, and says the same thing whatever was wrong", async () => {
    let elevated = false;
    const backend = fakeBackend({
      ...signedInRoutes(PERSON),
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
    await user.type(within(dialog).getByLabelText("Password"), "nope");
    await user.type(within(dialog).getByLabelText("Authentication code"), "123456");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    expect(await within(dialog).findByText("Invalid credentials.")).toBeInTheDocument();
    // The session is not lost for a wrong password: the dialog stays.
    await user.clear(within(dialog).getByLabelText("Password"));
    await user.type(within(dialog).getByLabelText("Password"), "pw");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/elevate").at(-1)?.body).toEqual({ password: "pw", code: "123456" });
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
});
