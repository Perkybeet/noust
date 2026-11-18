/**
 * The sign-in of a server that has no account yet (item 198): it opens on the access token, because there is no account to sign in with,
 * and it says where the token was shown as the server says it: the installer's box, or the log of a container (the NAS's app store).
 * Once an account exists the server says nothing of it and the page says what it always said.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { ANONYMOUS, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
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
    // The first sign-in needs no talk of the key of whoever runs the server: it is what is being done, with the installer's own box.
    expect(screen.queryByText(/It is the key of whoever runs this server/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Just installed Noust/)).not.toBeInTheDocument();
  });

  it("opens on the access token when the first read of the session failed and the next one says there is no account", async () => {
    // The page cannot know until the server answers, so it shows the account form while it waits; what it must not do is stay on it.
    let reads = 0;
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/session": () => {
        reads += 1;
        return reads === 1 ? problem(503, "unavailable", "The console could not be reached.") : json(200, { ...SIGNED_OUT, ...NO_ACCOUNTS, first_start: { token_shown_by: "installer" } });
      },
    });
    renderConsole("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeInTheDocument();
    expect(reads).toBeGreaterThan(1);
    expect(screen.queryByLabelText("Username or email")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "With a password" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Just installed Noust/)).not.toBeInTheDocument();
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
  it("opens on the account form and says what is true of it: ask whoever runs the server for an account, and the one who runs it has the token", async () => {
    server({ accounts_exist: true, first_start: null });
    renderConsole("/login");
    expect(await screen.findByLabelText("Username or email")).toBeInTheDocument();
    // The token of a server that has accounts is the way in of whoever runs it, not the way of somebody new, who is given an account:
    // it promises no account made here, and, not told where the token was shown, it names no installer (a container or a store's app had none).
    expect(
      screen.getByText(
        "No account yet? Ask whoever runs this server for one. If you run it and cannot get in, sign in with its access token: this command, on the server, prints a new one.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/create your own account/i)).toBeNull();
    expect(screen.queryByText(/installer/i)).toBeNull();
    expect(screen.getByText("noust web token --new")).toBeInTheDocument();
    expect(screen.getByText("A new token cancels the old one: the one in use stops working.")).toBeInTheDocument();
  });

  it("says it in Spanish, whole", async () => {
    server({ accounts_exist: true, first_start: null });
    const { setLocale } = await import("../../app/locale");
    await setLocale("es");
    renderConsole("/login");
    expect(
      await screen.findByText(
        "¿Aún no tienes cuenta? Pídesela a quien lleva este servidor. Si lo llevas tú y no puedes entrar, entra con su token de acceso: esta orden, en el servidor, imprime uno nuevo.",
      ),
    ).toBeInTheDocument();
    await setLocale("en");
  });

  it("says what the token is for, in Spanish, once it is chosen from the account form", async () => {
    server({ accounts_exist: true, first_start: null });
    const { setLocale } = await import("../../app/locale");
    await setLocale("es");
    try {
      const { user } = renderConsole("/login");
      await user.click(await screen.findByRole("button", { name: "Token de acceso" }));
      expect(await screen.findByText("Es la llave de quien lleva este servidor: úsala solo si no puedes entrar de otra forma, y guárdala para las emergencias.")).toBeInTheDocument();
    } finally {
      await setLocale("en");
    }
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
