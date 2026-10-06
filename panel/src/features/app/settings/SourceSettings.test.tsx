import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings } from "./testKit";
import { deployKeysUrl } from "./sourceForge";

const REPOSITORY = "github.com/shop/storefront";
const PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPublicHalfOfTheShopKey shop-example-com";
const NEW_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINewHalfOfTheShopKey shop-example-com";

const KEY = {
  name: "shop-example-com",
  origin: "generated",
  key_type: "ssh-ed25519",
  fingerprint: "SHA256:Lq3fWcmM6o0Xo2Wx4p1xK9m1m8w5Zc2m0r2VnQe7xYk",
  public_key: PUBLIC_KEY,
  created_at: "2026-10-01T09:00:00+00:00",
  rotated_at: null,
  last_used_at: null,
  repositories: [REPOSITORY],
  applications: [DOMAIN],
  pending: null,
};

const PENDING = { fingerprint: "SHA256:Nn1yUeXr4d0p5f8w7Gq2K3m9b6v5Zc2m0r2VnQe7xZz", public_key: NEW_PUBLIC_KEY, since: "2026-10-06T10:00:00+00:00" };

const NO_KEY = {
  domain: DOMAIN,
  repository: REPOSITORY,
  source_is_ssh: true,
  ssh_url: null,
  key: null,
  shared_with: [],
  server_key_fingerprint: "SHA256:ServerWideKey0000000000000000000000000000000",
  warnings: [],
};

const WITH_KEY = { ...NO_KEY, key: KEY };

const FORGE = {
  domain: DOMAIN,
  enabled: false,
  state: "disabled",
  layout: "releases",
  inplace_warning: false,
  hooks: { exposed: false, base_url: null, hook_url: null, hook_url_public: false, content_type: "application/json", events: ["push"] },
  branch: { tracked: "main", pinned: true, any_push_deploys: false },
  forge: { forge: "github", host: "github.com", repository: "shop/storefront", settings_url: "https://github.com/shop/storefront/settings/hooks/new" },
  github_app: { configured: false, hooks_active: false, covers_repository: false, account: null, repository_selection: null, settings_url: null },
  deliveries: { total: 0, refused_since_last_verified: 0, last: null, last_verified_at: null, last_push_at: null },
};

const NO_CREDENTIAL = { domain: DOMAIN, repository: REPOSITORY, host: "github.com", configured: false, username: null, source_is_https: true };
const CREDENTIAL = { ...NO_CREDENTIAL, configured: true, username: "deploy-bot" };

/** Renders the Source subsection and waits for its two cards, which come after the repository is read. */
async function source(view: object | (() => object), routes: Record<string, RouteHandler> = {}, app: object = RELEASE_APP, { cards = true } = {}) {
  const rendered = await renderSettings("/source", "Source", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/deploy-key`]: () => json(200, typeof view === "function" ? view() : view),
    [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, NO_CREDENTIAL),
    [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, FORGE),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    ...routes,
  });
  if (cards) {
    await screen.findByRole("heading", { name: "Deploy key" });
    await screen.findByRole("heading", { name: "HTTPS credential" });
  }
  return rendered;
}

const deployKey = () => part("Deploy key");
const credential = () => part("HTTPS credential");

describe("the deploy key of an application", () => {
  it("says the repository is reached with the server's own key, and makes one of its own on a click", async () => {
    let view: object = NO_KEY;
    const { user, backend } = await source(() => view, {
      [`POST /api/apps/${DOMAIN}/deploy-key`]: () => {
        view = WITH_KEY;
        return json(201, WITH_KEY);
      },
    });
    const card = deployKey();
    expect(await within(card).findByText("No key of its own")).toBeInTheDocument();
    expect(within(card).getByText(/SHA256:ServerWideKey/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Generate key" }));
    expect(await within(card).findByText("Has its own key")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key`)[0]?.body).toEqual({});
  });

  it("shows the public half to add at the code host, with a link to its page, and never the private half", async () => {
    await source(WITH_KEY);
    const card = deployKey();
    expect(await within(card).findByText("Has its own key")).toBeInTheDocument();
    const box = within(card).getByRole("group", { name: "Public key of shop-example-com" });
    expect(within(box).getByText(PUBLIC_KEY)).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Copy the public key" })).toBeInTheDocument();
    expect(within(card).getByText(/Add this key to GitHub as a deploy key with read access only/)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /Open the deploy keys of github\.com\/shop\/storefront on GitHub/ })).toHaveAttribute(
      "href",
      "https://github.com/shop/storefront/settings/keys/new",
    );
    // The facts: what compares with what the host shows.
    expect(within(card).getByText(KEY.fingerprint)).toBeInTheDocument();
    expect(within(card).getByText("ssh-ed25519")).toBeInTheDocument();
    expect(within(card).getByText("Never used yet")).toBeInTheDocument();
    expect(screen.queryByText(/PRIVATE KEY/)).not.toBeInTheDocument();
  });

  it("names the other applications built from the same repository, which use the key too", async () => {
    await source({ ...WITH_KEY, key: { ...KEY, applications: [DOMAIN, "blog.example.com"] } });
    expect(await within(deployKey()).findByText(/blog\.example\.com/)).toHaveTextContent("blog.example.com");
    expect(within(deployKey()).getByText(/are built from the same repository, so they use this key too/)).toBeInTheDocument();
  });

  it("asks the repository with the key as a deploy would, and says what it answered", async () => {
    const { user } = await source(WITH_KEY, {
      [`POST /api/apps/${DOMAIN}/deploy-key/test`]: () => json(200, { ok: true, message: "The repository answered (branch main)." }),
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Test the connection" }));
    expect(await within(deployKey()).findByText("The repository answered")).toBeInTheDocument();
    expect(within(deployKey()).getByText("The repository answered (branch main).")).toBeInTheDocument();
  });

  it("shows git's own words when the host refuses the key", async () => {
    const { user } = await source(WITH_KEY, {
      [`POST /api/apps/${DOMAIN}/deploy-key/test`]: () =>
        problem(502, "source_error", "Cannot read the branches of git@github.com:shop/storefront.git", {
          hint: "Add the key's public half at the code host.",
          output: "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.",
        }),
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Test the connection" }));
    expect(await within(deployKey()).findByText("The repository could not be read with this key")).toBeInTheDocument();
    expect(within(deployKey()).getByText("Add the key's public half at the code host.")).toBeInTheDocument();
    expect(within(deployKey()).getByText(/git@github\.com: Permission denied \(publickey\)\./)).toBeInTheDocument();
  });

  it("says a key is not used while the source is https, and where to deploy from", async () => {
    await source({ ...WITH_KEY, source_is_ssh: false, ssh_url: "git@github.com:shop/storefront.git" });
    const card = deployKey();
    expect(await within(card).findByText("Has a key that is not used")).toBeInTheDocument();
    expect(within(card).getByText(/Deploy from/)).toHaveTextContent("Deploy from git@github.com:shop/storefront.git to use it.");
  });
});

describe("replacing the deploy key", () => {
  it("makes the new key wait beside the old, shows it to add at the host, and ends when the host accepts it", async () => {
    let view: object = WITH_KEY;
    const { user, backend } = await source(() => view, {
      [`POST /api/apps/${DOMAIN}/deploy-key/rotate`]: () => {
        view = { ...WITH_KEY, key: { ...KEY, pending: PENDING } };
        return json(200, view);
      },
      [`POST /api/apps/${DOMAIN}/deploy-key/test`]: () => {
        view = { ...WITH_KEY, key: { ...KEY, public_key: NEW_PUBLIC_KEY, fingerprint: PENDING.fingerprint, rotated_at: "2026-10-06T10:05:00+00:00" } };
        return json(200, { ok: true, message: `The new key works; it is the deploy key of ${DOMAIN} now (branch main).` });
      },
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Replace the key" }));
    const rotation = await within(deployKey()).findByRole("group", { name: "Replacing the key" });
    // The progress, as a state with a shape and a word, and the two steps.
    expect(within(rotation).getByText("Waiting for GitHub to accept the new key")).toBeInTheDocument();
    expect(within(rotation).getByText("Add the new public key")).toBeInTheDocument();
    expect(within(rotation).getByText(NEW_PUBLIC_KEY)).toBeInTheDocument();
    expect(within(rotation).getByText(/Leave the old one there until the test passes/)).toBeInTheDocument();
    expect(within(rotation).getByText("Test it")).toBeInTheDocument();
    // The old key is still the key meanwhile, and its actions wait.
    expect(within(deployKey()).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();

    await user.click(within(rotation).getByRole("button", { name: "Test the new key" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key/test`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(within(deployKey()).queryByRole("group", { name: "Replacing the key" })).not.toBeInTheDocument();
    });
    expect(within(deployKey()).getByText(`The new key works; it is the deploy key of ${DOMAIN} now (branch main).`)).toBeInTheDocument();
    // The new key is the key now: its public half is the one shown.
    expect(within(deployKey()).getByText(NEW_PUBLIC_KEY)).toBeInTheDocument();
  });

  it("gives the replacement up, keeping the old key", async () => {
    let view: object = { ...WITH_KEY, key: { ...KEY, pending: PENDING } };
    const { user, backend } = await source(() => view, {
      [`DELETE /api/apps/${DOMAIN}/deploy-key/rotate`]: () => {
        view = WITH_KEY;
        return json(200, WITH_KEY);
      },
    });
    const rotation = await within(deployKey()).findByRole("group", { name: "Replacing the key" });
    await user.click(within(rotation).getByRole("button", { name: "Cancel the replacement" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/deploy-key/rotate`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(within(deployKey()).queryByRole("group", { name: "Replacing the key" })).not.toBeInTheDocument();
    });
    expect(within(deployKey()).getByText("Has its own key")).toBeInTheDocument();
  });

  it("says the host has not accepted the new key yet, in git's words, and keeps the progress", async () => {
    const { user } = await source({ ...WITH_KEY, key: { ...KEY, pending: PENDING } }, {
      [`POST /api/apps/${DOMAIN}/deploy-key/test`]: () => json(200, { ok: true, message: "The repository answered with the current key, not the new one (branch main): add the new public key at the code host first." }),
    });
    const rotation = await within(deployKey()).findByRole("group", { name: "Replacing the key" });
    await user.click(within(rotation).getByRole("button", { name: "Test the new key" }));
    expect(await within(deployKey()).findByText(/answered with the current key, not the new one/)).toBeInTheDocument();
    expect(within(deployKey()).getByRole("group", { name: "Replacing the key" })).toBeInTheDocument();
  });
});

describe("removing and importing a deploy key", () => {
  it("asks first, saying the host still trusts the public key, then goes back to the server's key", async () => {
    let view: object = WITH_KEY;
    const { user, backend } = await source(() => view, {
      [`DELETE /api/apps/${DOMAIN}/deploy-key`]: () => {
        view = NO_KEY;
        return json(200, NO_KEY);
      },
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Remove the deploy key of ${DOMAIN}?` });
    expect(within(dialog).getByText(/GitHub still trusts the public key until you remove it there/)).toBeInTheDocument();
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/deploy-key`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Remove key" }));
    expect(await within(deployKey()).findByText("No key of its own")).toBeInTheDocument();
  });

  it("imports a private key that is typed once, and shows only its public half afterwards", async () => {
    let view: object = NO_KEY;
    const { user, backend } = await source(() => view, {
      [`POST /api/apps/${DOMAIN}/deploy-key/import`]: () => {
        view = { ...WITH_KEY, key: { ...KEY, origin: "imported" } };
        return json(201, view);
      },
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Import a key…" }));
    const dialog = await screen.findByRole("dialog", { name: `Import a deploy key for ${REPOSITORY}` });
    await user.type(within(dialog).getByRole("textbox", { name: "Private key" }), "-----BEGIN OPENSSH PRIVATE KEY-----");
    await user.click(within(dialog).getByRole("button", { name: "Import key" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key/import`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key/import`)[0]?.body).toEqual({ private_key: "-----BEGIN OPENSSH PRIVATE KEY-----" });
    expect(await within(deployKey()).findByText("Imported")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/BEGIN OPENSSH PRIVATE KEY/)).not.toBeInTheDocument();
  });

  it("keeps the dialog open with the server's words when the key has a passphrase", async () => {
    const { user } = await source(NO_KEY, {
      [`POST /api/apps/${DOMAIN}/deploy-key/import`]: () =>
        problem(400, "validation_error", "The private key has a passphrase", { hint: "Remove it first: ssh-keygen -p -N '' -f key" }),
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Import a key…" }));
    const dialog = await screen.findByRole("dialog", { name: `Import a deploy key for ${REPOSITORY}` });
    await user.type(within(dialog).getByRole("textbox", { name: "Private key" }), "key");
    await user.click(within(dialog).getByRole("button", { name: "Import key" }));
    expect(await within(dialog).findByText("The key was not imported")).toBeInTheDocument();
    expect(within(dialog).getByText("The private key has a passphrase")).toBeInTheDocument();
    expect(within(dialog).getByText("Remove it first: ssh-keygen -p -N '' -f key")).toBeInTheDocument();
  });

  it("asks for a key to import before sending anything", async () => {
    const { user, backend } = await source(NO_KEY);
    await user.click(await within(deployKey()).findByRole("button", { name: "Import a key…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Import key" }));
    expect(await within(dialog).findByText("Paste the private key.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key/import`)).toHaveLength(0);
  });

  it("uses a key that exists for this repository too, by name", async () => {
    let view: object = NO_KEY;
    const { user, backend } = await source(() => view, {
      "GET /api/deploy-keys": () =>
        json(200, { keys: [{ ...KEY, name: "machine-user", fingerprint: "SHA256:MachineUser", repositories: ["github.com/shop/api"], applications: ["api.example.com"] }], total: 1 }),
      [`POST /api/apps/${DOMAIN}/deploy-key`]: () => {
        view = { ...WITH_KEY, key: { ...KEY, name: "machine-user", applications: ["api.example.com", DOMAIN] } };
        return json(201, view);
      },
    });
    await user.click(await within(deployKey()).findByRole("button", { name: "Use a key that exists…" }));
    const dialog = await screen.findByRole("dialog", { name: `Use a key that exists for ${REPOSITORY}` });
    await user.click(within(dialog).getByRole("combobox", { name: "Key" }));
    await user.click(await screen.findByRole("option", { name: /machine-user, SHA256:MachineUser/ }));
    await user.click(within(dialog).getByRole("button", { name: "Use this key" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/deploy-key`)[0]?.body).toEqual({ key: "machine-user" });
    });
    expect(await within(deployKey()).findByText("Has its own key")).toBeInTheDocument();
  });
});

describe("the https credential of an application", () => {
  it("keeps a user and a token as a secret, shows the user, and never the token", async () => {
    let state: object = NO_CREDENTIAL;
    const { user, backend } = await source(NO_KEY, {
      [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, state),
      [`PUT /api/apps/${DOMAIN}/git-credential`]: () => {
        state = CREDENTIAL;
        return json(200, CREDENTIAL);
      },
    });
    expect(await within(credential()).findByText("No credential")).toBeInTheDocument();
    await user.click(within(credential()).getByRole("button", { name: "Set credential…" }));
    const dialog = await screen.findByRole("dialog", { name: `Set the credential for ${REPOSITORY}` });
    await user.type(within(dialog).getByRole("textbox", { name: "User" }), "deploy-bot");
    const token = within(dialog).getByLabelText("Token");
    expect(token).toHaveAttribute("type", "password");
    await user.type(token, "ghp_secret_token_value");
    await user.click(within(dialog).getByRole("button", { name: "Save credential" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/git-credential`)[0]?.body).toEqual({ username: "deploy-bot", secret: "ghp_secret_token_value" });
    });
    expect(await within(credential()).findByText("A credential is kept")).toBeInTheDocument();
    expect(within(credential()).getByText("deploy-bot")).toBeInTheDocument();
    expect(screen.queryByText("ghp_secret_token_value")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("ghp_secret_token_value")).not.toBeInTheDocument();
  });

  it("asks for both before sending anything", async () => {
    const { user, backend } = await source(NO_KEY);
    await user.click(await within(credential()).findByRole("button", { name: "Set credential…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Save credential" }));
    expect(await within(dialog).findByText("Enter the user.")).toBeInTheDocument();
    expect(within(dialog).getByText("Enter the token.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/git-credential`)).toHaveLength(0);
  });

  it("starts the user from what is kept when it is replaced, and the token from nothing", async () => {
    const { user } = await source(NO_KEY, { [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, CREDENTIAL) });
    await user.click(await within(credential()).findByRole("button", { name: "Replace…" }));
    const dialog = await screen.findByRole("dialog", { name: `Replace the credential for ${REPOSITORY}` });
    expect(within(dialog).getByRole("textbox", { name: "User" })).toHaveValue("deploy-bot");
    expect(within(dialog).getByLabelText("Token")).toHaveValue("");
  });

  it("tells the user and the token a Bitbucket repository takes", async () => {
    const bitbucket = { ...FORGE, forge: { forge: "bitbucket", host: "bitbucket.org", repository: "shop/storefront", settings_url: "https://bitbucket.org/shop/storefront/admin/webhooks" } };
    const { user } = await source(NO_KEY, { [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, bitbucket) });
    await user.click(await within(credential()).findByRole("button", { name: "Set credential…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/x-bitbucket-api-token-auth with an API token, or x-token-auth with a repository or workspace access token/)).toBeInTheDocument();
    expect(within(dialog).getByText(/App passwords stopped working on 28 July 2026/)).toBeInTheDocument();
  });

  it("removes it after one question", async () => {
    let state: object = CREDENTIAL;
    const { user, backend } = await source(NO_KEY, {
      [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, state),
      [`DELETE /api/apps/${DOMAIN}/git-credential`]: () => {
        state = NO_CREDENTIAL;
        return json(200, NO_CREDENTIAL);
      },
    });
    await user.click(await within(credential()).findByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Remove the credential of ${DOMAIN}?` });
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/git-credential`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Remove credential" }));
    expect(await within(credential()).findByText("No credential")).toBeInTheDocument();
  });

  it("says a kept credential is not used while the source is SSH", async () => {
    await source(NO_KEY, { [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, { ...CREDENTIAL, source_is_https: false }) });
    expect(await within(credential()).findByText("Kept, but not used")).toBeInTheDocument();
  });
});

describe("the source of an application that is not a git repository", () => {
  it("says there is nothing to sign in to, and offers neither", async () => {
    await source({ ...NO_KEY, repository: null, source_is_ssh: false }, {}, RELEASE_APP, { cards: false });
    expect(await screen.findByText("Not built from a git repository")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Deploy key" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "HTTPS credential" })).not.toBeInTheDocument();
  });

  it("says when the key cannot be read, and reads it again on request", async () => {
    let answers = 0;
    const { user } = await source(
      NO_KEY,
      {
        [`GET /api/apps/${DOMAIN}/deploy-key`]: () => {
          answers += 1;
          return answers === 1 ? problem(500, "internal_error", "The key store is unreadable") : json(200, NO_KEY);
        },
      },
      RELEASE_APP,
      { cards: false },
    );
    expect(await screen.findByText("Could not read the deploy key")).toBeInTheDocument();
    expect(screen.getByText("The key store is unreadable")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No key of its own")).toBeInTheDocument();
  });
});

describe("the source settings, for everyone", () => {
  it("leads to where a push is set up to deploy, and to every deploy key of the server", async () => {
    await source(NO_KEY);
    await screen.findByText("No key of its own", { exact: true });
    const push = screen.getByText(/A push to the repository can deploy this application: GitHub, GitLab, Gitea and Bitbucket are set up in/);
    expect(within(push).getByRole("link", { name: "Deploy on push" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/deploy-on-push`);
    const keys = screen.getByText(/Every deploy key on this server is listed in/);
    expect(within(keys).getByRole("link", { name: "Settings, Integrations" })).toHaveAttribute("href", "/settings/integrations");
  });

  it("has no accessibility violations, with a key, a replacement waiting and a credential", async () => {
    await source({ ...WITH_KEY, key: { ...KEY, pending: PENDING } }, { [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, CREDENTIAL) });
    await within(deployKey()).findByRole("group", { name: "Replacing the key" });
    await within(credential()).findByText("A credential is kept");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("speaks Spanish", async () => {
    await source(WITH_KEY);
    await within(deployKey()).findByText("Has its own key");
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Origen" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Clave de despliegue" })).toBeInTheDocument();
    expect(screen.getByText("Tiene clave propia")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Probar la conexión" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Credencial HTTPS" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("where a deploy key is added", () => {
  it("knows the page of each code host, and none for a server it has not seen", () => {
    const repo = { repository: "shop/storefront" };
    expect(deployKeysUrl({ ...repo, forge: "github", host: "github.com", settings_url: null })).toBe("https://github.com/shop/storefront/settings/keys/new");
    expect(deployKeysUrl({ ...repo, forge: "gitlab", host: "gitlab.example.com", settings_url: null })).toBe("https://gitlab.example.com/shop/storefront/-/settings/repository");
    expect(deployKeysUrl({ ...repo, forge: "gitea", host: "codeberg.org", settings_url: null })).toBe("https://codeberg.org/shop/storefront/settings/keys");
    expect(deployKeysUrl({ ...repo, forge: "bitbucket", host: "bitbucket.org", settings_url: null })).toBe("https://bitbucket.org/shop/storefront/admin/access-keys/");
    expect(deployKeysUrl({ ...repo, forge: null, host: "git.example.com", settings_url: null })).toBeNull();
    expect(deployKeysUrl({ forge: "github", host: "github.com", repository: null, settings_url: null })).toBeNull();
    expect(deployKeysUrl(undefined)).toBeNull();
  });
});
