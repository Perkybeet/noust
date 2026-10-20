import { act, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, fakeBackend, json, problem } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

function server(options: { offered?: boolean; extra?: Record<string, RouteHandler> } = {}) {
  return fakeBackend({
    "GET /api/auth/session": () => json(200, ANONYMOUS),
    "GET /api/auth/oidc/providers": () => json(200, { providers: [], local_login: true, password_reset: options.offered ?? true }),
    ...options.extra,
  });
}

describe("the sign-in page offers a way back", () => {
  it("links to choosing a new password only when the server can mail one", async () => {
    server({ offered: true });
    renderConsole("/login");
    expect(await screen.findByRole("link", { name: "Forgot your password?" })).toHaveAttribute("href", "/reset-password");
  });

  it("does not, when it cannot", async () => {
    server({ offered: false });
    renderConsole("/login");
    await screen.findByRole("button", { name: "Sign in" });
    expect(screen.queryByRole("link", { name: "Forgot your password?" })).not.toBeInTheDocument();
  });
});

describe("asking for the link", () => {
  it("sends the name or address and says the same thing whoever it was, and passes axe", { timeout: 30_000 }, async () => {
    window.location.hash = "";
    const backend = server({
      extra: { "POST /api/auth/password-reset/request": () => json(200, { message: "If that account can be recovered...", expires_in_minutes: 60 }) },
    });
    const { user, container } = renderConsole("/reset-password");
    expect(await screen.findByRole("heading", { level: 1, name: "Forgot your password?" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Account name or address"), "  ana@example.com ");
    await user.click(screen.getByRole("button", { name: "Send the link" }));

    expect(await screen.findByText("Check your mail")).toBeInTheDocument();
    expect(screen.getByText(/It works once and for 60 minutes/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/password-reset/request").map((call) => call.body)).toEqual([{ identifier: "ana@example.com" }]);
    await expectNoAxeViolations(container, { page: true });
  });

  it("asks for a name before it asks the server", async () => {
    window.location.hash = "";
    const backend = server();
    const { user } = renderConsole("/reset-password");
    await user.click(await screen.findByRole("button", { name: "Send the link" }));

    expect(await screen.findByText("Enter the account name or address.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/password-reset/request")).toHaveLength(0);
  });

  it("says so when the server does not recover accounts this way", async () => {
    window.location.hash = "";
    server({ offered: false });
    renderConsole("/reset-password");

    expect(await screen.findByText(/A security officer can send you a new invitation/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send the link" })).not.toBeInTheDocument();
  });
});

describe("choosing the password with the link", () => {
  it("takes the code out of the address, asks for the password and a second-factor code, and sends them", { timeout: 30_000 }, async () => {
    window.location.hash = "#code=noust_reset_abc";
    const backend = server({
      extra: { "POST /api/auth/password-reset/confirm": () => json(200, { success: true, message: "Password changed." }) },
    });
    const { user, container, location } = renderConsole("/reset-password");
    expect(await screen.findByRole("heading", { level: 1, name: "Choose a new password" })).toBeInTheDocument();
    // The code is in the address only until it is read.
    expect(window.location.hash).toBe("");
    await expectNoAxeViolations(container, { page: true });

    await user.type(screen.getByLabelText("New password"), "a long passphrase here");
    await user.type(screen.getByLabelText("New password again"), "another one entirely");
    await user.type(screen.getByLabelText("Code from your authenticator"), "123 456");
    await user.click(screen.getByRole("button", { name: "Change the password" }));
    expect(await screen.findByText("The two passwords are not the same.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/password-reset/confirm")).toHaveLength(0);

    await user.clear(screen.getByLabelText("New password again"));
    await user.type(screen.getByLabelText("New password again"), "a long passphrase here");
    await user.click(screen.getByRole("button", { name: "Change the password" }));

    expect(await screen.findByText("Password changed")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/password-reset/confirm").map((call) => call.body)).toEqual([
      { code: "noust_reset_abc", password: "a long passphrase here", second_factor: "123456" },
    ]);
    await user.click(screen.getByRole("button", { name: "Continue to sign in" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/login");
    });
  });

  it("shows the server's refusal as it wrote it, beside the field it is about", async () => {
    window.location.hash = "#noust_reset_abc";
    server({
      extra: {
        "POST /api/auth/password-reset/confirm": () =>
          problem(400, "weak_password", "That password is too short.", { fields: { password: "At least 14 characters." } }),
      },
    });
    const { user } = renderConsole("/reset-password");
    await user.type(await screen.findByLabelText("New password"), "short");
    await user.type(screen.getByLabelText("New password again"), "short");
    await user.type(screen.getByLabelText("Code from your authenticator"), "123456");
    await user.click(screen.getByRole("button", { name: "Change the password" }));

    expect(await screen.findByText("At least 14 characters.")).toBeInTheDocument();
  });

  it("shows a link that is no good, with the fix", async () => {
    window.location.hash = "#noust_reset_old";
    server({
      extra: {
        "POST /api/auth/password-reset/confirm": () =>
          problem(401, "invalid_reset", "This link is not valid: it may have expired or been used.", { hint: "Ask for a new one." }),
      },
    });
    const { user, location } = renderConsole("/reset-password");
    await user.type(await screen.findByLabelText("New password"), "a long passphrase here");
    await user.type(screen.getByLabelText("New password again"), "a long passphrase here");
    await user.type(screen.getByLabelText("Code from your authenticator"), "123456");
    await user.click(screen.getByRole("button", { name: "Change the password" }));

    expect(await screen.findByText("This link is not valid: it may have expired or been used.")).toBeInTheDocument();
    expect(screen.getByText("Ask for a new one.")).toBeInTheDocument();
    // A bad link is a credential refused, not a lost session: the page stays.
    expect(location().pathname).toBe("/reset-password");
  });

  it("speaks Spanish", async () => {
    window.location.hash = "";
    server();
    renderConsole("/reset-password");
    await screen.findByRole("heading", { level: 1, name: "Forgot your password?" });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "¿Has olvidado la contraseña?" })).toBeInTheDocument();
  });
});
