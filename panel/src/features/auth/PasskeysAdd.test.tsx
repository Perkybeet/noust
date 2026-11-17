import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import { CREDENTIAL, installPasskeyBrowser, removePasskeyBrowser } from "../../test/passkeyBrowser";
import { setCeremonyPause } from "./webauthn";

/**
 * Adding a passkey from Account > Sign-in security, the way the owner met "OperationError: A request is already pending." in production: the
 * server asks to confirm it's you, the person confirms with a passkey (a ceremony), the console asks the options again and, in the same
 * instant, opens the creation (a second one). Chrome refuses the second while it closes the first; the page has to carry on by itself.
 */

const UNTIL = "2026-09-25T18:10:00+00:00";

const PERSON = {
  ...SESSION,
  grant: null,
  role: "admin",
  elevation_factors: ["totp", "backup_code", "passkey"],
  account: {
    id: 4,
    username: "dani",
    display_name: "",
    role: "admin",
    status: "active",
    mfa_enabled: true,
    passkeys: 1,
    backup_codes_remaining: 8,
    owner: false,
    failures_since_login: 0,
    created_at: 1_790_000_000,
  },
};

const LAPTOP = { id: 1, name: "Laptop", owner: "dani", rp_id: "localhost", algorithm: "ES256", synced: false, transports: [], created_at: 1_790_000_000 };
const PHONE = { id: 2, name: "Phone", owner: "dani", rp_id: "localhost", algorithm: "ES256", synced: true, transports: [], created_at: 1_790_000_100 };

/** The server of someone with one passkey whose adding asks to confirm it's them first. */
function server() {
  let elevated = false;
  let added = false;
  const backend = fakeBackend({
    ...signedInRoutes(PERSON),
    "GET /api/auth/passkeys": () =>
      json(200, { passkeys: added ? [LAPTOP, PHONE] : [LAPTOP], availability: { supported: true, rp_id: "localhost" }, allow_synced: true }),
    "POST /api/auth/passkeys/elevate/options": () => json(200, { public_key: { challenge: "AQID", allowCredentials: [{ type: "public-key", id: "CQkJ" }] }, expires_in: 300 }),
    "POST /api/auth/passkeys/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: UNTIL });
    },
    "POST /api/auth/passkeys/registration/options": () =>
      elevated ? json(200, { public_key: { challenge: "AQID", user: { id: "AQ", name: "dani", displayName: "dani" } }, expires_in: 300 }) : problem(403, "elevation_required", "Confirm it's you"),
    "POST /api/auth/passkeys/registration": () => {
      added = true;
      return json(200, { passkey: PHONE, backup_codes: null });
    },
  });
  return backend;
}

afterEach(() => {
  setCeremonyPause(null);
  removePasskeyBrowser();
});

describe("adding a passkey, confirmed with a passkey first", () => {
  it("asks the browser again when it says the confirmation's window is still closing, and ends with the passkey in the list", { timeout: 30_000 }, async () => {
    const backend = server();
    const web = installPasskeyBrowser({ closesSlowly: true });
    // What the console waits between two attempts is the browser finishing: the test lets it finish instead of waiting a clock.
    const waits: number[] = [];
    setCeremonyPause((milliseconds) => {
      waits.push(milliseconds);
      web.finishClosing();
      return Promise.resolve();
    });
    const { user } = renderConsole("/account/security");
    const add = await screen.findByRole("button", { name: "Add a passkey" });
    await user.click(add);
    const adding = await screen.findByRole("dialog", { name: "Add a passkey" });
    await user.clear(within(adding).getByLabelText("Name"));
    await user.type(within(adding).getByLabelText("Name"), "Phone");
    await user.click(within(adding).getByRole("button", { name: "Create passkey" }));

    // The server asked to confirm it's you: the dialog that does it is the one over the add dialog.
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(await within(confirm).findByRole("button", { name: "Confirm with a passkey" }));
    await waitFor(() => {
      expect(web.windows).toHaveLength(1);
    });
    expect(web.windows[0]?.kind).toBe("get");
    web.windows[0]?.settle({ value: CREDENTIAL });

    // The creation reaches the browser, is refused as pending while the confirmation closes, and goes again.
    await waitFor(() => {
      expect(web.windows).toHaveLength(2);
    });
    expect(web.windows[1]?.kind).toBe("create");
    web.windows[1]?.settle({ value: CREDENTIAL });

    expect(await screen.findByText("Phone")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Add a passkey" })).toBeNull();
    });
    expect(web.calls).toEqual([
      { kind: "get", refused: false },
      { kind: "create", refused: true },
      { kind: "create", refused: false },
    ]);
    expect(waits).toEqual([250]);
    expect(web.overlaps).toBe(0);
    expect(backend.callsTo("POST /api/auth/passkeys/registration/options")).toHaveLength(2);
    expect(backend.callsTo("POST /api/auth/passkeys/registration")).toHaveLength(1);
  });

  it("says why above the browser's own words when it never stops saying so, and lets the person try again", { timeout: 30_000 }, async () => {
    server();
    const web = installPasskeyBrowser({ closesSlowly: true });
    // Never finishes closing: every attempt is refused as pending.
    setCeremonyPause(() => Promise.resolve());
    const { user } = renderConsole("/account/security");
    await user.click(await screen.findByRole("button", { name: "Add a passkey" }));
    const adding = await screen.findByRole("dialog", { name: "Add a passkey" });
    await user.clear(within(adding).getByLabelText("Name"));
    await user.type(within(adding).getByLabelText("Name"), "Phone");
    await user.click(within(adding).getByRole("button", { name: "Create passkey" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.click(await within(confirm).findByRole("button", { name: "Confirm with a passkey" }));
    await waitFor(() => {
      expect(web.windows).toHaveLength(1);
    });
    web.windows[0]?.settle({ value: CREDENTIAL });

    const alert = await within(adding).findByText("Could not add the passkey");
    expect(alert).toBeInTheDocument();
    expect(within(adding).getByText("The browser was still closing another passkey window. Try again.")).toBeInTheDocument();
    expect(within(adding).getByText("OperationError: A request is already pending.")).toBeInTheDocument();
    expect(web.calls.filter((call) => call.kind === "create")).toHaveLength(4);
    await expectNoAxeViolations(adding);
    // The button is back: the window of the browser is free for the person's next try.
    expect(within(adding).getByRole("button", { name: "Create passkey" })).toBeEnabled();
  });
});
