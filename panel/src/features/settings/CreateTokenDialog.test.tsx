import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionInfo } from "../../api/queries/auth";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall } from "../../test/fakes";
import { organisation } from "../teams/testFixtures";

const NOW = Date.now() / 1000;

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

const ADMIN = ["self", "apps.read", "apps.operate", "apps.deploy", "apps.manage", "server.read", "server.manage", "databases.read", "backups.read", "backups.run"];

/** An account: the token it makes is its own, and holds at most what it does. */
const PERSON: SessionInfo = {
  ...SESSION,
  grant: null,
  role: "admin",
  permissions: ADMIN,
  account: { id: 4, username: "dani", display_name: "", role: "admin", status: "active", mfa_enabled: true, passkeys: 0, backup_codes_remaining: 8, owner: false, failures_since_login: 0, created_at: NOW },
};

const ROLES = {
  roles: { viewer: ["self", "apps.read", "server.read", "databases.read", "backups.read"], admin: ADMIN },
  permissions: {},
  token_presets: [
    { id: "read_apps", permissions: ["self", "apps.read"] },
    { id: "deploy_only", permissions: ["self", "apps.read", "apps.deploy"] },
    { id: "deploy_and_restart", permissions: ["self", "apps.read", "apps.operate", "apps.deploy"] },
    { id: "backups", permissions: ["self", "backups.read", "backups.run"] },
  ],
  tenant_permissions: ["self", "apps.read", "apps.operate", "apps.deploy", "apps.manage", "databases.read", "backups.read", "backups.run"],
};

function tokensBackend(session: SessionInfo = PERSON, roles: Record<string, unknown> = ROLES, extra: Record<string, (call: RecordedCall) => Response> = {}) {
  let elevated = false;
  return fakeBackend({
    ...signedInRoutes(session),
    ...organisation(),
    "GET /api/auth/tokens": () => json(200, { tokens: [] }),
    "GET /api/auth/roles": () => json(200, roles),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "POST /api/auth/tokens": (call: RecordedCall) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const body = call.body as Record<string, unknown>;
      return json(201, { id: 9, name: body["name"], scope: body["scope"], created_at: NOW, expires_at: null, token: "noust_tok_s3cr3t" });
    },
    ...extra,
  });
}

async function openDialog(user: ReturnType<typeof renderConsole>["user"]): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: "Create token" }));
  return screen.findByRole("dialog", { name: "Create an API token" });
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"], code = "123456"): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText(/Authentication code|Access token/), code);
  await user.click(within(confirm).getByRole("button", { name: "Continue" }));
}

describe("Create an API token, signed in with the access token (item 133)", () => {
  it("says before anything is filled in that the token will have no owner, and offers no reach it cannot have", { timeout: 30_000 }, async () => {
    tokensBackend(SESSION);
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    // At the top, where the form begins: what is different about this token, and why.
    expect(within(dialog).getByText("You signed in with the access token")).toBeInTheDocument();
    expect(within(dialog).getByText(/has no owner/)).toBeInTheDocument();
    // The choice of team, project or environment is not offered: it needs a token that belongs to somebody.
    expect(within(dialog).queryByRole("radio", { name: /One team/ })).not.toBeInTheDocument();
    expect(within(dialog).getByText(/needs a token that belongs to an account/)).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });

  it("says nothing of an owner to a person, and keeps the reach they may choose", { timeout: 30_000 }, async () => {
    tokensBackend(PERSON);
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    expect(within(dialog).queryByText("You signed in with the access token")).not.toBeInTheDocument();
    expect(await within(dialog).findByRole("radio", { name: /One team/ })).toBeInTheDocument();
  });

  it("says that a server that wants every token to belong to an account will refuse it, before the form is filled", { timeout: 30_000 }, async () => {
    tokensBackend({ ...SESSION, security_profile: "ens-medium" });
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    expect(within(dialog).getByText(/every token belongs to an account/)).toBeInTheDocument();
  });

  it("brings the server's refusal to the eye and the keyboard, with its words, when the form is longer than the window", { timeout: 30_000 }, async () => {
    const scrolled = vi.spyOn(Element.prototype, "scrollIntoView").mockImplementation(() => undefined);
    tokensBackend(SESSION, ROLES, { "POST /api/auth/tokens": () => problem(400, "securityerror", "On this server an API token belongs to an account", { hint: "Issue it signed in as that account." }) });
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/^Name/), "ci");
    await user.click(within(dialog).getByRole("button", { name: "Create token" }));

    const block = await within(dialog).findByRole("alert");
    expect(block).toHaveTextContent("On this server an API token belongs to an account");
    expect(block).toHaveTextContent("Issue it signed in as that account.");
    await waitFor(() => {
      expect(scrolled).toHaveBeenCalled();
    });
    // Focus is on what failed: a keyboard user starts from the refusal, not from the top of the form.
    await waitFor(() => {
      expect(block.closest("[tabindex='-1']")).toHaveFocus();
    });
  });
});

describe("Create an API token, narrowed to what a job needs (item 163)", () => {
  it("offers 'only what a job needs' with the presets the server serves, and sends the one chosen", { timeout: 30_000 }, async () => {
    const backend = tokensBackend();
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByLabelText(/^Name/), "ci-deploy");

    await user.click(within(dialog).getByRole("radio", { name: /^Only what a job needs/ }));
    const preset = within(dialog).getByRole("combobox", { name: "What it does" });
    // The list is the server's: its ids, said in the console's words.
    await user.click(preset);
    expect((await screen.findAllByRole("option")).map((option) => option.textContent)).toEqual([
      expect.stringContaining("Read applications"),
      expect.stringContaining("Deploy only"),
      expect.stringContaining("Deploy and restart"),
      expect.stringContaining("Back up"),
    ]);
    await user.click(screen.getByRole("option", { name: /Deploy only/ }));
    // What it will hold is on screen before it is made, and it is not the scope's: no server, no databases.
    expect(await within(dialog).findByText("apps.deploy")).toBeInTheDocument();
    expect(within(dialog).queryByText("server.read")).not.toBeInTheDocument();
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Create token" }));
    await confirmItsYou(user);

    await screen.findByRole("dialog", { name: "Copy your new token" });
    expect(backend.callsTo("POST /api/auth/tokens").at(-1)?.body).toMatchObject({ name: "ci-deploy", preset: "deploy_only" });
    expect(backend.callsTo("POST /api/auth/tokens").at(-1)?.body).not.toHaveProperty("permissions");
  });

  it("starts the choice at the one a pipeline most often needs", { timeout: 30_000 }, async () => {
    tokensBackend();
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    await user.click(within(dialog).getByRole("radio", { name: /^Only what a job needs/ }));

    expect(within(dialog).getByRole("combobox", { name: "What it does" })).toHaveTextContent("Deploy only");
  });

  it("says what its owner cannot give it, as for a scope", { timeout: 30_000 }, async () => {
    // The person may not deploy: a preset is cut to them like a scope is.
    tokensBackend({ ...PERSON, role: "viewer", permissions: ["self", "apps.read"] });
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    await user.click(within(dialog).getByRole("radio", { name: /^Only what a job needs/ }));

    expect(await within(dialog).findByText(/Left out, since the owner may not: .*apps\.deploy/)).toBeInTheDocument();
  });

  it("is not offered by a server that does not serve the list: a token would come out as plain read", { timeout: 30_000 }, async () => {
    tokensBackend(PERSON, { roles: ROLES.roles, permissions: {} });
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);

    expect(within(dialog).queryByRole("radio", { name: /Only what a job needs/ })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("radio", { name: /^Read/ })).toBeInTheDocument();
  });

  it("says it in Spanish", { timeout: 30_000 }, async () => {
    await act(() => setLocale("es"));
    tokensBackend();
    const { user } = renderConsole("/settings/tokens");
    await user.click(await screen.findByRole("button", { name: "Crear token" }));
    const dialog = await screen.findByRole("dialog", { name: "Crear un token de API" });

    await user.click(within(dialog).getByRole("radio", { name: /^Solo lo que necesita una tarea/ }));

    expect(within(dialog).getByRole("combobox", { name: "Qué hace" })).toHaveTextContent("Solo desplegar");
    await expectNoAxeViolations(dialog);
  });
});

describe("Create an API token, what it will hold when it is tied to a team (item 133)", () => {
  it("shows what a token tied to a team can ever hold, and what that leaves out of the scope chosen", { timeout: 30_000 }, async () => {
    tokensBackend();
    const { user } = renderConsole("/settings/tokens");
    const dialog = await openDialog(user);
    await user.click(within(dialog).getByRole("radio", { name: /^Admin/ }));
    expect(await within(dialog).findByText("server.manage")).toBeInTheDocument();

    await user.click(await within(dialog).findByRole("radio", { name: /One team/ }));
    await user.click(within(dialog).getByRole("combobox", { name: "Team" }));
    await user.click(await screen.findByRole("option", { name: "Acme Corp" }));

    // The preview follows the reach: inside a team a token never reaches the server.
    await waitFor(() => {
      expect(within(dialog).getByText(/never reaches the server: .*server\.manage/)).toBeInTheDocument();
    });
    expect(within(dialog).getByText("apps.manage")).toBeInTheDocument();
  });
});
