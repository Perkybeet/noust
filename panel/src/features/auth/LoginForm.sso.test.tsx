import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";

/** Before sign-in the server names itself only by the label its operator chose. */
const SIGNED_OUT = { ...ANONYMOUS, hostname: "web-01 (production)", version: "", login_label: "web-01 (production)" };

const ACCOUNT = {
  id: 3,
  username: "maria",
  display_name: "Maria Lopez",
  role: "operator",
  status: "active",
  person_ref: "maria@acme.test",
  mfa_enabled: true,
  passkeys: 0,
  backup_codes_remaining: 8,
  failures_since_login: 0,
  created_at: 1_790_000_000,
};

const MARIA = { ...SESSION, account: ACCOUNT, role: "operator", grant: null, permissions: ["self", "apps.read", "apps.operate", "apps.deploy"] };

const PROVIDERS = { providers: [{ name: "corp", label: "Acme SSO" }] };
const TICKET = "T1cket_of_a_sign_in_that_already_passed_the_provider";
const CHALLENGE = "step-2-challenge";
const CODE = "123456";

/** What `POST /api/auth/oidc/complete` answers once the account is in (no second factor owed). */
function signedInAnswer(extra: Record<string, unknown> = {}) {
  return {
    login: { success: true, expires_in: 28_800, csrf_token: "c", session_token: null, account: ACCOUNT, mfa_required: false, ...extra },
    next: "/apps/shop.example.com/logs",
    provider: "corp",
    label: "Acme SSO",
  };
}

/** An identity provider is configured; the sign-in goes through it and then, maybe, a second factor. */
function ssoBackend(options: { second?: string[]; providers?: unknown } = {}) {
  let signedIn = false;
  return fakeBackend({
    ...signedInRoutes(MARIA),
    "GET /api/auth/session": () => json(200, signedIn ? MARIA : SIGNED_OUT),
    "GET /api/auth/oidc/providers": () => json(200, options.providers ?? PROVIDERS),
    "POST /api/auth/oidc/complete": (call: RecordedCall) => {
      const body = call.body as { ticket: string };
      if (body.ticket !== TICKET) return problem(401, "sign_in_expired", "This sign-in has expired. Start again with your provider.");
      if (options.second) {
        return json(200, {
          ...signedInAnswer(),
          login: { success: false, expires_in: 0, csrf_token: "", second_factor: { challenge: CHALLENGE, expires_in: 300, methods: options.second } },
        });
      }
      signedIn = true;
      return json(200, signedInAnswer());
    },
    "POST /api/auth/login/second-factor": (call: RecordedCall) => {
      const body = call.body as { challenge: string; code: string };
      if (body.challenge !== CHALLENGE) return problem(401, "sign_in_expired", "This sign-in has expired.");
      if (body.code !== CODE) return problem(401, "invalid_totp", "Invalid two-factor code.");
      signedIn = true;
      return json(200, signedInAnswer().login);
    },
  });
}

afterEach(() => {
  toast.dismiss();
});

describe("the sign-in page with an identity provider", () => {
  it("offers a button for each provider, as a link that starts the sign-in on the server", async () => {
    ssoBackend();
    renderConsole("/login?next=%2Fapps");

    const button = await screen.findByRole("link", { name: "Sign in with Acme SSO" });

    expect(button).toHaveAttribute("href", "/api/auth/oidc/start?provider=corp&next=%2Fapps");
    expect(button).toHaveAttribute("data-variant", "secondary");
    // The password form stays: a provider is one more way in, not the only one.
    expect(screen.getByLabelText("Username or email")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });

  it("leads with the providers and keeps the account form behind a link once passwords are turned off", async () => {
    ssoBackend({ providers: { ...PROVIDERS, local_login: false } });
    const { user } = renderConsole("/login");

    expect(await screen.findByRole("link", { name: "Sign in with Acme SSO" })).toBeInTheDocument();
    // A way back for the accounts that were left a password, not an equal choice beside the provider.
    const more = screen.getByRole("button", { name: "Sign in with a password or a passkey" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    await user.click(more);
    expect(more).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByLabelText("Username or email")).toBeVisible();
    // The emergency access stays where it was.
    expect(screen.getByRole("button", { name: "Emergency access" })).toBeInTheDocument();
  });

  it("does not carry a destination that is the overview", async () => {
    ssoBackend();
    renderConsole("/login");
    const button = await screen.findByRole("link", { name: "Sign in with Acme SSO" });
    expect(button).toHaveAttribute("href", "/api/auth/oidc/start?provider=corp");
  });

  it("offers one button per provider, in the order the server gives them", async () => {
    ssoBackend({
      providers: {
        providers: [
          { name: "corp", label: "Acme SSO" },
          { name: "google", label: "Google" },
        ],
      },
    });
    renderConsole("/login");
    await screen.findByRole("link", { name: "Sign in with Acme SSO" });
    const links = screen.getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("/api/auth/oidc/start"));
    expect(links.map((link) => link.textContent)).toEqual(["Sign in with Acme SSO", "Sign in with Google"]);
  });

  it("starts a SAML provider where the SAML sign-in keeps its state, with the same destination", async () => {
    ssoBackend({
      providers: {
        providers: [
          { name: "corp", label: "Acme SSO", protocol: "oidc" },
          { name: "entra", label: "Entra ID", protocol: "saml" },
        ],
      },
    });
    renderConsole("/login?next=%2Fapps");

    const oidc = await screen.findByRole("link", { name: "Sign in with Acme SSO" });
    const saml = screen.getByRole("link", { name: "Sign in with Entra ID" });

    expect(oidc).toHaveAttribute("href", "/api/auth/oidc/start?provider=corp&next=%2Fapps");
    expect(saml).toHaveAttribute("href", "/api/auth/saml/start?provider=entra&next=%2Fapps");
  });

  it("shows no button when no provider is configured, and none when the server cannot say", async () => {
    ssoBackend({ providers: { providers: [] } });
    const first = renderConsole("/login");
    await screen.findByLabelText("Username or email");
    expect(screen.queryByRole("link", { name: /Sign in with/ })).toBeNull();
    first.cleanup();
    first.unmount();

    const backend = ssoBackend();
    backend.on("GET /api/auth/oidc/providers", () => problem(500, "internal_error", "boom"));
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    expect(screen.queryByRole("link", { name: /Sign in with/ })).toBeNull();
    // A missing button is not an error to show: the form is all there is, and it works.
    expect(screen.queryByText("boom")).toBeNull();
    expect(screen.queryByText("Could not sign in")).toBeNull();
  });

  it("collects the ticket on arrival, takes it out of the address, and opens the page the server named", async () => {
    const backend = ssoBackend();
    const { location } = renderConsole(`/login?sso=${TICKET}`);

    await screen.findByRole("heading", { level: 1, name: "shop.example.com" });

    expect(backend.callsTo("POST /api/auth/oidc/complete").map((call) => call.body)).toEqual([{ ticket: TICKET, bearer: false }]);
    expect(location().pathname).toBe("/apps/shop.example.com/logs");
    expect(location().search).not.toMatchObject({ sso: expect.anything() as unknown });
    // Not the password form, not a second request: one answer, one sign-in.
    expect(backend.callsTo("POST /api/auth/login")).toHaveLength(0);
  });

  it("says what it is doing while the ticket is collected", async () => {
    const backend = ssoBackend();
    let release: (value: Response) => void = () => undefined;
    backend.on("POST /api/auth/oidc/complete", () => new Promise<Response>((resolve) => (release = resolve)));
    renderConsole(`/login?sso=${TICKET}`);

    expect(await screen.findByText("Finishing your sign-in")).toBeInTheDocument();
    expect(screen.queryByLabelText("Username or email")).toBeNull();
    await act(async () => {
      release(json(200, signedInAnswer()));
      await Promise.resolve();
    });
  });

  it("owes the second factor after the provider, and says which provider vouched", async () => {
    const backend = ssoBackend({ second: ["totp", "backup_code"] });
    const { user } = renderConsole(`/login?sso=${TICKET}`);

    expect(await screen.findByText("Signed in with Acme SSO.")).toBeInTheDocument();
    const code = screen.getByLabelText("Two-factor code");
    await waitFor(() => {
      expect(code).toHaveFocus();
    });
    expect(screen.queryByLabelText("Password")).toBeNull();
    await user.type(code, CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(backend.callsTo("POST /api/auth/login/second-factor").map((call) => call.body)).toEqual([{ challenge: CHALLENGE, code: CODE, bearer: false }]);
  });

  it("keeps the step open on a wrong code, as after a password", async () => {
    ssoBackend({ second: ["totp"] });
    const { user } = renderConsole(`/login?sso=${TICKET}`);
    await user.type(await screen.findByLabelText("Two-factor code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("Invalid two-factor code.")).toBeInTheDocument();
    expect(screen.getByLabelText("Two-factor code")).toBeInTheDocument();
  });

  it("goes back to the first step when the step has expired", async () => {
    const backend = ssoBackend({ second: ["totp"] });
    backend.on("POST /api/auth/login/second-factor", () => problem(401, "sign_in_expired", "This sign-in has expired."));
    const { user } = renderConsole(`/login?sso=${TICKET}`);
    await user.type(await screen.findByLabelText("Two-factor code"), CODE);
    await user.click(screen.getByRole("button", { name: "Verify" }));
    expect(await screen.findByText("This sign-in has expired.")).toBeInTheDocument();
    expect(screen.getByLabelText("Username or email")).toBeInTheDocument();
  });

  it("says in the console's words that a ticket that is spent or expired cannot be used", async () => {
    ssoBackend();
    renderConsole(`/login?sso=${"x".repeat(43)}`);

    expect(await screen.findByText("Could not sign in with your provider")).toBeInTheDocument();
    expect(screen.getByText("That sign-in expired or was already used. Start it again.")).toBeInTheDocument();
    // The form is back, and so is the button to try again.
    expect(screen.getByLabelText("Username or email")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in with Acme SSO" })).toBeInTheDocument();
  });

  it.each([
    ["unavailable", "The provider did not answer. Try again in a moment, or use your account."],
    ["expired", "That sign-in expired or was already used. Start it again."],
    ["denied", "The provider did not let you in. Start again, or use your account."],
    ["failed", "The provider's answer could not be checked. Start again, and if it keeps failing tell a security officer."],
    ["refused", "The provider knows you, but this server has no account for you. Ask a security officer to invite you or link your account."],
    ["locked", "Sign-in is locked for this address. Try again later."],
  ])("explains why the provider's answer was not accepted: %s", async (word, sentence) => {
    const backend = ssoBackend();
    renderConsole(`/login?sso_error=${word}`);

    const title = await screen.findByText("Could not sign in with your provider");
    expect(title.closest("[role=alert]")).not.toBeNull();
    expect(screen.getByText(sentence)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/auth/oidc/complete")).toHaveLength(0);
  });

  it("ignores a reason it does not know and a ticket that is not one", async () => {
    const backend = ssoBackend();
    renderConsole("/login?sso_error=<script>&sso=../../etc");

    await screen.findByLabelText("Username or email");
    expect(screen.queryByText("Could not sign in with your provider")).toBeNull();
    expect(backend.callsTo("POST /api/auth/oidc/complete")).toHaveLength(0);
  });

  it("is refused for an address that is locked out, in the server's words", async () => {
    const backend = ssoBackend();
    backend.on("POST /api/auth/oidc/complete", () => problem(429, "locked_out", "Too many failed attempts. Locked for 90 seconds.", { headers: { "Retry-After": "90" } }));
    renderConsole(`/login?sso=${TICKET}`);

    expect(await screen.findByText("Too many failed attempts")).toBeInTheDocument();
  });

  it("has no accessibility violations with the buttons or with a reason, and speaks Spanish", async () => {
    ssoBackend();
    renderConsole("/login?sso_error=refused");
    await screen.findByRole("link", { name: "Sign in with Acme SSO" });
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("link", { name: "Iniciar sesión con Acme SSO" })).toBeInTheDocument();
    expect(screen.getByText("No se pudo iniciar sesión con tu proveedor")).toBeInTheDocument();
    expect(screen.getByText(/Pide a un responsable de seguridad que te invite/)).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});


const DIRECTORY = { providers: [{ name: "corp", label: "Acme directory", protocol: "ldap" }], local_login: true };

/** A directory is configured: the form asks for the name and password people have there. */
function directoryBackend(options: { providers?: unknown; second?: boolean; answer?: () => Response } = {}) {
  let signedIn = false;
  return fakeBackend({
    ...signedInRoutes(MARIA),
    "GET /api/auth/session": () => json(200, signedIn ? MARIA : SIGNED_OUT),
    "GET /api/auth/oidc/providers": () => json(200, options.providers ?? DIRECTORY),
    "POST /api/auth/ldap/login": () => {
      if (options.answer) return options.answer();
      if (options.second) {
        return json(200, { success: false, expires_in: 0, csrf_token: "", second_factor: { challenge: CHALLENGE, expires_in: 300, methods: ["totp"] } });
      }
      signedIn = true;
      return json(200, signedInAnswer().login);
    },
    "POST /api/auth/login": () => problem(401, "invalid_credentials", "Invalid username or password."),
    "POST /api/auth/login/second-factor": (call: RecordedCall) => {
      const body = call.body as { challenge: string; code: string };
      if (body.challenge !== CHALLENGE || body.code !== CODE) return problem(401, "invalid_totp", "Invalid two-factor code.");
      signedIn = true;
      return json(200, signedInAnswer().login);
    },
  });
}

describe("the sign-in page with a directory", () => {
  it("asks which account to sign in with, and shows the directory as a choice rather than a button", async () => {
    directoryBackend();
    const { user } = renderConsole("/login");

    const choice = await screen.findByRole("combobox", { name: "Sign in with" });
    // No button leaves for it: its name and password are typed here.
    expect(screen.queryByRole("link", { name: /Acme directory/ })).not.toBeInTheDocument();
    await user.click(choice);
    expect(await screen.findByRole("option", { name: "An account of this server" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Acme directory" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body);
  });

  it("sends the name and password to the directory when it is chosen, and to the server when it is not", async () => {
    const backend = directoryBackend();
    const { user } = renderConsole("/login");
    await screen.findByRole("combobox", { name: "Sign in with" });

    await user.type(screen.getByLabelText("Username or email"), "maria");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/auth/login")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/auth/ldap/login")).toHaveLength(0);

    await user.click(screen.getByRole("combobox", { name: "Sign in with" }));
    await user.click(await screen.findByRole("option", { name: "Acme directory" }));
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/auth/ldap/login")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/auth/ldap/login")[0]?.body).toEqual({
      directory: "corp",
      username: "maria",
      password: "correct horse",
      bearer: false,
    });
  });

  it("owes the second factor after the directory, and says who vouched", async () => {
    directoryBackend({ second: true });
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("combobox", { name: "Sign in with" }));
    await user.click(await screen.findByRole("option", { name: "Acme directory" }));
    await user.type(screen.getByLabelText("Username or email"), "maria");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Signed in with Acme directory.")).toBeInTheDocument();
    expect(screen.getByLabelText("Two-factor code")).toBeInTheDocument();
  });

  it("says a person the policy does not admit is not a wrong password", async () => {
    directoryBackend({
      answer: () => problem(403, "sign_in_refused", "This sign-in is not allowed on this server."),
    });
    const { user } = renderConsole("/login");
    await user.click(await screen.findByRole("combobox", { name: "Sign in with" }));
    await user.click(await screen.findByRole("option", { name: "Acme directory" }));
    await user.type(screen.getByLabelText("Username or email"), "maria");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alerts = await screen.findAllByRole("alert");
    const said = alerts.map((alert) => alert.textContent).join(" ");
    expect(said).toContain("This sign-in is not allowed on this server.");
    expect(said).not.toContain("Check your username");
  });

  it("leads with the directory once passwords are turned off, with no passkey offered", async () => {
    directoryBackend({ providers: { ...DIRECTORY, local_login: false } });
    renderConsole("/login");

    const choice = await screen.findByRole("combobox", { name: "Sign in with" });

    expect(choice).toHaveTextContent("Acme directory");
    expect(screen.queryByRole("button", { name: /passkey/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Username or email")).toBeVisible();
  });
});
