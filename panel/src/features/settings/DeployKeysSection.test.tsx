import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { forgetTarget } from "./DeployKeysSection";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const KEY = {
  name: "shop-example-com",
  origin: "generated",
  key_type: "ssh-ed25519",
  fingerprint: "SHA256:Lq3fWcmM6o0Xo2Wx4p1xK9m1m8w5Zc2m0r2VnQe7xYk",
  public_key: "ssh-ed25519 AAAA shop",
  created_at: "2026-10-01T09:00:00+00:00",
  rotated_at: null,
  last_used_at: "2026-10-05T09:00:00+00:00",
  repositories: ["github.com/shop/storefront"],
  applications: ["shop.example.com"],
  pending: null,
};

const MACHINE_USER = {
  ...KEY,
  name: "machine-user",
  fingerprint: "SHA256:MachineUserKey",
  last_used_at: null,
  repositories: ["github.com/shop/api", "github.com/shop/web", "github.com/shop/docs"],
  applications: ["api.example.com", "web.example.com", "docs.example.com"],
  pending: { fingerprint: "SHA256:Next", public_key: "ssh-ed25519 BBBB next", since: "2026-10-06T09:00:00+00:00" },
};

const GITHUB_HOST = { host: "github.com", fingerprint: "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU", key_type: "ED25519" };

const HOSTS = {
  hosts: [GITHUB_HOST, { host: "[git.example.com]:2222", fingerprint: "SHA256:HostKeyOfTheOperatorsServer", key_type: "RSA" }],
  total: 2,
};

async function integrations(routes: Record<string, RouteHandler> = {}, session: typeof SESSION = ELEVATED) {
  const backend = fakeBackend({
    ...signedInRoutes(session),
    "GET /api/integrations/github": () => json(200, { configured: false, installations: [], hooks_url: null, hooks_active: false }),
    "GET /api/config": () => json(200, { config: { web: { hooks_url: "" } }, path: "/etc/noust/config.yaml", writable: true }),
    "GET /api/deploy-keys": () => json(200, { keys: [KEY, MACHINE_USER], total: 2 }),
    "GET /api/deploy-keys/hosts": () => json(200, HOSTS),
    ...routes,
  });
  const harness = renderConsole("/settings/deploy-keys");
  // The page holds the three slabs: the keys, the code hosts this server trusts and the form that adds one.
  await screen.findByRole("heading", { level: 2, name: "Keys of this server" });
  const page = screen.getByRole("heading", { level: 1, name: "Deploy keys" }).closest("[data-template=detail]");
  if (!(page instanceof HTMLElement)) throw new Error("no page");
  return { ...harness, backend, section: page };
}

describe("the deploy keys of the server, in Settings", () => {
  it("lists every key with what it reaches and the applications built from it", async () => {
    const { section } = await integrations();
    const table = await within(section).findByRole("table", { name: "Deploy keys of this server" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const own = rows.find((row) => within(row).queryByText("shop-example-com") !== null);
    if (!own) throw new Error("no row for the key");
    expect(within(own).getByText("ssh-ed25519")).toBeInTheDocument();
    expect(within(own).getByText(KEY.fingerprint)).toBeInTheDocument();
    expect(within(own).getByText("github.com/shop/storefront")).toBeInTheDocument();
    // A key in use is the common one and says nothing: only the one being replaced says its state.
    expect(within(own).queryByText("In use")).not.toBeInTheDocument();
    expect(within(own).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("href", "/apps/shop.example.com/settings/source");
  });

  it("says a key that is being replaced, and names two applications and counts the rest", async () => {
    const { section } = await integrations();
    const table = await within(section).findByRole("table", { name: "Deploy keys of this server" });
    const shared = within(table).getByText("machine-user").closest("tr");
    if (!shared) throw new Error("no row for the shared key");
    expect(within(shared).getByText("Being replaced")).toBeInTheDocument();
    // The applications are tags of the key's name, on its line: the first, and "+N" for the rest.
    expect(within(shared).getByRole("link", { name: "api.example.com" })).toBeInTheDocument();
    expect(within(shared).queryByRole("link", { name: "web.example.com" })).not.toBeInTheDocument();
    expect(within(shared).getByRole("button", { name: /2 more tags: web.example.com, docs.example.com/ })).toHaveTextContent("+2");
    expect(within(shared).getByText("Never used")).toBeInTheDocument();
  });

  it("says in one sentence what appears here the first time, and takes you to where a key is made", async () => {
    const { section } = await integrations({ "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }) });
    expect(await within(section).findByText("When you deploy from a private repository, its key appears here.")).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "Go to Applications" })).toHaveAttribute("href", "/apps");
  });

  it("is three slabs that say what they are, with no command that repeats a button (items 121 and 123)", async () => {
    const { section } = await integrations();
    await within(section).findByRole("table", { name: "Deploy keys of this server" });
    for (const name of ["Keys of this server", "Trusted code hosts", "Add one before the first connection"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    expect(screen.queryByText("noust app deploy-key list")).not.toBeInTheDocument();
    expect(screen.queryByText("The same, from a terminal")).not.toBeInTheDocument();
    // The old name of the trusted hosts is gone, and why they are trusted is behind the ? and not on the page.
    expect(screen.queryByText("Pinned host keys")).not.toBeInTheDocument();
    expect(screen.queryByText(/pose as GitHub/)).not.toBeInTheDocument();
  });

  it("keeps why a code host is trusted behind a ? beside its name", async () => {
    const { user, section } = await integrations();
    await within(section).findByRole("table", { name: "Deploy keys of this server" });
    await user.click(screen.getByRole("button", { name: "About Trusted code hosts" }));
    expect(await screen.findByRole("dialog", { name: "Trusted code hosts" })).toHaveTextContent("So nobody can pose as GitHub or your Gitea between this server and the real one.");
  });

  it("shows the keys and the hosts to a role that cannot change them: no forgetting, no form, one line on top", async () => {
    const { section, container } = await integrations({}, { ...SESSION, permissions: ["self", "apps.read"], role: "viewer" });
    await within(section).findByRole("table", { name: "Deploy keys of this server" });
    expect(await within(section).findByRole("table", { name: "Code hosts this server trusts" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Forget / })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Add one before the first connection" })).not.toBeInTheDocument();
    expect(container.querySelector("[data-slot=read-only]")).toHaveTextContent("Your role, viewer, can see this page and not change it. Changing it takes the permission server.host_access.");
    await expectNoAxeViolations(container);
  });

  it("says when they cannot be read", async () => {
    const { section } = await integrations({ "GET /api/deploy-keys": () => problem(500, "internal_error", "The key store is unreadable") });
    expect(await within(section).findByText("Could not load the deploy keys")).toBeInTheDocument();
    expect(within(section).getByText("The key store is unreadable")).toBeInTheDocument();
  });
});

describe("the host keys Noust pinned", () => {
  it("names a host on another port the way the API takes it, not the way known_hosts writes it", () => {
    expect(forgetTarget("[git.example.org]:2222")).toBe("git.example.org:2222");
    expect(forgetTarget("git.example.org:2222")).toBe("git.example.org:2222");
    expect(forgetTarget("github.com")).toBe("github.com");
  });

  it("lists each host with the key it showed the first time", async () => {
    const { section } = await integrations();
    const table = await within(section).findByRole("table", { name: "Code hosts this server trusts" });
    expect(within(table).getByText("github.com")).toBeInTheDocument();
    expect(within(table).getByText("[git.example.com]:2222")).toBeInTheDocument();
    expect(within(table).getByText("ED25519")).toBeInTheDocument();
    expect(within(table).getByText("SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU")).toBeInTheDocument();
  });

  it("says when nothing is pinned yet", async () => {
    const { section } = await integrations({ "GET /api/deploy-keys/hosts": () => json(200, { hosts: [], total: 0 }) });
    expect(await within(section).findByText(/Nothing is pinned yet/)).toBeInTheDocument();
  });

  it("forgets a host key only after saying that the next key it shows is the one pinned", async () => {
    let hosts = HOSTS;
    const { user, backend, section } = await integrations({
      "GET /api/deploy-keys/hosts": () => json(200, hosts),
      "DELETE /api/deploy-keys/hosts/git.example.com%3A2222": () => {
        hosts = { hosts: [GITHUB_HOST], total: 1 };
        return json(200, { host: "[git.example.com]:2222", forgotten: true });
      },
    });
    await user.click(await within(section).findByRole("button", { name: "Forget [git.example.com]:2222" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Forget the host key of [git.example.com]:2222?" });
    expect(within(dialog).getByText(/The next connection pins whatever key the host shows then/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Do it only after comparing the fingerprint/)).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "DELETE")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Forget host key" }));
    await waitFor(() => {
      expect(backend.calls.filter((call) => call.method === "DELETE")).toHaveLength(1);
    });
    await waitFor(() => {
      expect(within(section).queryByText("[git.example.com]:2222")).not.toBeInTheDocument();
    });
  });

  it("pins a host before the first connection by the fingerprint its code host publishes", async () => {
    let hosts: typeof HOSTS = { hosts: [], total: 0 };
    const { user, backend, section } = await integrations({
      "GET /api/deploy-keys/hosts": () => json(200, hosts),
      "POST /api/deploy-keys/hosts": () => {
        hosts = { hosts: [GITHUB_HOST], total: 1 };
        return json(201, GITHUB_HOST);
      },
    });
    const form = within(await within(section).findByRole("heading", { name: "Add one before the first connection" }).then((heading) => heading.closest("section") ?? section));
    expect(form.getByRole("button", { name: "Save the fingerprint" })).toBeDisabled();

    await user.type(form.getByRole("textbox", { name: /^Host/ }), "github.com");
    await user.type(form.getByRole("textbox", { name: /^Fingerprint/ }), GITHUB_HOST.fingerprint);
    await user.click(form.getByRole("button", { name: "Save the fingerprint" }));

    await waitFor(() => expect(backend.callsTo("POST /api/deploy-keys/hosts")).toHaveLength(1));
    expect(backend.callsTo("POST /api/deploy-keys/hosts")[0]?.body).toEqual({ host: "github.com", fingerprint: GITHUB_HOST.fingerprint });
    expect(await within(section).findByRole("table", { name: "Code hosts this server trusts" })).toBeInTheDocument();
    expect(form.getByRole("textbox", { name: /^Host/ })).toHaveValue("");
  });

  it("shows what the host showed instead when it has no key with that fingerprint", async () => {
    const { user, section } = await integrations({
      "POST /api/deploy-keys/hosts": () =>
        problem(400, "sourceerror", "github.com showed no key with the fingerprint SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", {
          hint: "It showed: SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU. Compare with the fingerprints the code host publishes.",
        }),
    });
    const form = within(await within(section).findByRole("heading", { name: "Add one before the first connection" }).then((heading) => heading.closest("section") ?? section));

    await user.type(form.getByRole("textbox", { name: /^Host/ }), "github.com");
    await user.type(form.getByRole("textbox", { name: /^Fingerprint/ }), "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA");
    await user.click(form.getByRole("button", { name: "Save the fingerprint" }));

    expect(await form.findByText("The host key of github.com was not pinned")).toBeInTheDocument();
    expect(form.getByText(/It showed: SHA256:\+DiY3wvvV6TuJJhbpZisF/)).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    const { section } = await integrations();
    await within(section).findByRole("table", { name: "Code hosts this server trusts" });
    await expectNoAxeViolations(section);
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Claves de este servidor" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Servidores de código de confianza" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Olvidar github.com" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guardar la huella" })).toBeInTheDocument();
    await expectNoAxeViolations(section);
  });
});
