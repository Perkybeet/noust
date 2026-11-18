/**
 * The sign-in of a server that has no account yet (item 198): it opens on the access token, because there is no account to sign in with,
 * and it says where the token was shown as the server says it: the installer's box, or the log of a container (the NAS's app store).
 * Once an account exists the server says nothing of it and the page says what it always said.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { ANONYMOUS, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { hasNoAccounts, newTokenCommand, tokenShownBy } from "./firstStart";

const SIGNED_OUT = { ...ANONYMOUS, hostname: "nas-casa", version: "", login_label: "nas-casa" };

function server(session: Record<string, unknown>) {
  return fakeBackend({ ...signedInRoutes(), "GET /api/auth/session": () => json(200, { ...SIGNED_OUT, ...session }) });
}

const NO_ACCOUNTS = { accounts_exist: false };

describe("a server with no account, where the installer printed the token", () => {
  it("opens on the access token, says the installer printed it, and gives the command that issues another one", async () => {
    server({ ...NO_ACCOUNTS, first_start: { token_shown_by: "installer" } });
    renderConsole("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeInTheDocument();
    expect(screen.getByText("The installer printed it once, in a box. Paste it here.")).toBeInTheDocument();
    expect(screen.getByText("noust web token --new")).toBeInTheDocument();
    // There is no account to sign in with, so the account form is not what the page opens on.
    expect(screen.queryByLabelText("Username or email")).not.toBeInTheDocument();
  });

  it("offers no way back to the account form, which asks for what nobody has yet", async () => {
    server({ ...NO_ACCOUNTS, first_start: { token_shown_by: "installer" } });
    renderConsole("/login");
    await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" });
    expect(screen.queryByRole("button", { name: "With a password" })).not.toBeInTheDocument();
  });
});

describe("a server with no account, where the token is in a container's log", () => {
  it("opens on the access token, says it is in the container's log, and names the installer nowhere", async () => {
    server({ ...NO_ACCOUNTS, first_start: { token_shown_by: "container_log" } });
    renderConsole("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeInTheDocument();
    expect(screen.getByText(/Noust wrote it once in the container's log, in a box, the first time it started/)).toBeInTheDocument();
    expect(screen.queryByText(/installer/i)).not.toBeInTheDocument();
  });

  it("gives the command that is run inside a container, without the name of one, and says where the name is", async () => {
    server({ ...NO_ACCOUNTS, first_start: { token_shown_by: "container_log" } });
    renderConsole("/login");
    await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" });
    expect(screen.getByText("docker exec -it <container> noust web token --new")).toBeInTheDocument();
    expect(screen.getByText("The log of the first start says which container is Noust's.")).toBeInTheDocument();
    expect(screen.queryByText("noust web token --new")).not.toBeInTheDocument();
  });

  it("says it in Spanish", async () => {
    server({ ...NO_ACCOUNTS, first_start: { token_shown_by: "container_log" } });
    const { setLocale } = await import("../../app/locale");
    await setLocale("es");
    renderConsole("/login");
    expect(await screen.findByText(/Noust lo escribió una vez en el registro del contenedor/)).toBeInTheDocument();
    await setLocale("en");
  });
});

describe("a server that has accounts", () => {
  it("opens on the account form and says what it always said: the page is not told where the token was shown", async () => {
    server({ accounts_exist: true, first_start: null });
    renderConsole("/login");
    expect(await screen.findByLabelText("Username or email")).toBeInTheDocument();
    // Not told where the token was shown, so it does not name an installer: a central installed as a container or from a NAS's store had none.
    expect(screen.getByText(/First time on this server\? Sign in with this server's access token, under/)).toBeInTheDocument();
    expect(screen.queryByText(/installer/i)).toBeNull();
    expect(screen.getByText("noust web token --new")).toBeInTheDocument();
  });

  it("offers the way back from the token to the account form, which has accounts to sign in with", async () => {
    server({ accounts_exist: true, first_start: null });
    const { user } = renderConsole("/login?with=token");
    await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" });
    await user.click(screen.getByRole("button", { name: "With a password" }));
    expect(await screen.findByLabelText("Username or email")).toBeInTheDocument();
  });

  it("does not open on the token for a server that says nothing of it (an older one)", async () => {
    server({ accounts_exist: undefined, first_start: undefined });
    renderConsole("/login");
    expect(await screen.findByLabelText("Username or email")).toBeInTheDocument();
  });
});

describe("what the page reads of the first start", () => {
  it("reads the server's two words and nothing else", () => {
    expect(hasNoAccounts({ ...SIGNED_OUT, accounts_exist: false })).toBe(true);
    expect(hasNoAccounts({ ...SIGNED_OUT, accounts_exist: true })).toBe(false);
    expect(hasNoAccounts({ ...SIGNED_OUT, accounts_exist: null })).toBe(false);
    expect(hasNoAccounts(undefined)).toBe(false);
    expect(tokenShownBy({ ...SIGNED_OUT, first_start: { token_shown_by: "container_log" } })).toBe("container_log");
    expect(tokenShownBy({ ...SIGNED_OUT, first_start: null })).toBeNull();
    expect(tokenShownBy(undefined)).toBeNull();
  });

  it("issues a new token by the command of the place the token was shown", () => {
    expect(newTokenCommand("installer")).toBe("noust web token --new");
    expect(newTokenCommand(null)).toBe("noust web token --new");
    expect(newTokenCommand("container_log")).toBe("docker exec -it <container> noust web token --new");
  });
});
