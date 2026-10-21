import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import type { RouteHandler } from "../../../test/fakes";
import { canPush } from "./GitPush";
import { DOMAIN, ELEVATED, IN_PLACE_APP, PUSH_OFF, RELEASE_APP, last, part, renderSettings } from "./testKit";

const KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB1b0tQ8sQ3h1b0tQ8sQ3h1b0tQ8sQ3h1b0tQ8sQ3hAb";

const WEBHOOK = {
  domain: DOMAIN,
  enabled: false,
  state: "disabled",
  layout: "releases",
  inplace_warning: false,
  hooks: { exposed: false, base_url: null, hook_url: `http://localhost:3000/hooks/deploy/${DOMAIN}`, hook_url_public: false, content_type: "application/json", events: ["push"] },
  branch: { tracked: "main", pinned: true, any_push_deploys: false },
  forge: { forge: null, host: null, repository: null, settings_url: null },
  github_app: { configured: false, hooks_active: false, covers_repository: false, account: null, repository_selection: null, settings_url: null },
  deliveries: { total: 0, refused_since_last_verified: 0, last: null, last_verified_at: null, last_push_at: null },
};

const ON = {
  ...PUSH_OFF,
  enabled: true,
  branch: "main",
  repository: "/var/lib/noust-git/shop.example.com.git",
  remote: `noust-git@${DOMAIN}:${DOMAIN}.git`,
  keys: [{ label: "laptop", key_type: "ssh-ed25519", fingerprint: "SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG" }],
  hook_ok: true,
  enabled_at: "2026-10-16T10:00:00",
};

function page(state: object | (() => object), routes: Record<string, RouteHandler> = {}, app: object = RELEASE_APP) {
  return renderSettings("/deploy-on-push", "Deploy on push", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, WEBHOOK),
    [`GET /api/apps/${DOMAIN}/webhook/received`]: () => json(200, { domain: DOMAIN, items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/push`]: () => json(200, typeof state === "function" ? state() : state),
    ...routes,
  });
}

const card = () => part("Push from your computer");

/**
 * The card's form to turn push on, once it has been read. The heading is there from the card's first
 * frame, over a skeleton, and the card is only drawn after the webhook's status, so a test that
 * reached for a field the moment it saw the heading would be racing the second query.
 */
async function turnOnForm() {
  await screen.findByRole("heading", { name: "Push from your computer" });
  const form = within(card());
  await form.findByRole("button", { name: "Turn on git push" });
  return form;
}

describe("which applications a push can deploy", () => {
  it("are the ones on releases that are not built from their own files", () => {
    expect(canPush({ layout: "releases", app_type: "nodejs" })).toBe("yes");
    expect(canPush({ layout: "inplace", app_type: "nodejs" })).toBe("inPlace");
    expect(canPush({ layout: "releases", app_type: "docker-compose" })).toBe("type");
    expect(canPush({ layout: "inplace", app_type: "container" })).toBe("type");
  });
});

describe("git push, beside the webhook's status", () => {
  it("is drawn once the status has been read, never beside its skeleton to be pushed out of view by what replaces it", { timeout: 40_000 }, async () => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { backend } = await page(PUSH_OFF, {
      [`GET /api/apps/${DOMAIN}/webhook`]: async () => {
        await held;
        return json(200, WEBHOOK);
      },
    });
    await waitFor(() => expect(backend.callsTo(`GET /api/apps/${DOMAIN}/webhook`)).toHaveLength(1));

    expect(screen.queryByRole("heading", { name: "Push from your computer" })).not.toBeInTheDocument();
    expect(screen.queryByText(`noust app webhook show ${DOMAIN}`)).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/push`)).toHaveLength(0);

    release();
    expect(await screen.findByRole("heading", { name: "Push from your computer" })).toBeInTheDocument();
    expect(screen.getByText(`noust app webhook show ${DOMAIN}`)).toBeInTheDocument();
  });
});

describe("git push, off", () => {
  it("offers to turn it on, with a branch and a first key that are both optional", { timeout: 40_000 }, async () => {
    const { container } = await page(PUSH_OFF);
    const form = await turnOnForm();

    expect(form.getByLabelText(/^Branch/)).toHaveValue("");
    expect(form.getByLabelText(/^Name of the key/)).toBeInTheDocument();
    expect(form.getByLabelText(/^Public key/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Turn on git push" })).toBeEnabled();
    expect(form.queryByText("This server does not take git pushes yet")).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("sends nothing but what was typed, and shows the remote once it is on", { timeout: 40_000 }, async () => {
    let state: object = PUSH_OFF;
    const { user, backend } = await page(() => state, {
      [`PUT /api/apps/${DOMAIN}/push`]: () => {
        state = ON;
        return json(200, ON);
      },
    });
    const form = await turnOnForm();

    await user.click(form.getByRole("button", { name: "Turn on git push" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/push`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/push`)[0]?.body).toEqual({});
    expect(await within(card()).findByText(`git remote add noust noust-git@${DOMAIN}:${DOMAIN}.git`)).toBeInTheDocument();
    expect(within(card()).getByText("git push noust main")).toBeInTheDocument();
  });

  it("sends the branch and the key with the name it was given", { timeout: 40_000 }, async () => {
    const { user, backend } = await page(PUSH_OFF, { [`PUT /api/apps/${DOMAIN}/push`]: () => json(200, ON) });
    const form = await turnOnForm();

    await user.type(form.getByLabelText(/^Branch/), "release");
    await user.type(form.getByLabelText(/^Name of the key/), "laptop");
    await user.type(form.getByLabelText(/^Public key/), KEY);
    await user.click(form.getByRole("button", { name: "Turn on git push" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/push`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/push`)[0]?.body).toEqual({ branch: "release", key: KEY, label: "laptop" });
  });

  it("asks for the key's name before sending a key, and the key before sending a name", { timeout: 40_000 }, async () => {
    const { user, backend } = await page(PUSH_OFF);
    const form = await turnOnForm();

    await user.type(form.getByLabelText(/^Public key/), KEY);
    await user.click(form.getByRole("button", { name: "Turn on git push" }));
    expect(await form.findByText("Name the key.")).toBeInTheDocument();

    await user.clear(form.getByLabelText(/^Public key/));
    await user.type(form.getByLabelText(/^Name of the key/), "laptop");
    await user.click(form.getByRole("button", { name: "Turn on git push" }));
    expect(await form.findByText("Paste the key's line.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/push`)).toHaveLength(0);
  });

  it("says when the server does not take pushes yet, shows why verbatim, and prepares it", { timeout: 40_000 }, async () => {
    let ready = false;
    const { user, backend } = await page(
      () => ({ ...PUSH_OFF, host_ready: ready, problems: ready ? [] : ["The account noust-git does not exist yet: run 'noust push setup' as root to create it and restrict it in sshd."] }),
      {
        "POST /api/push/setup": () => {
          ready = true;
          return json(200, { account: "noust-git", account_created: true, home: "/var/lib/noust-git", sshd_policy: "/etc/ssh/sshd_config.d/00-noust-push.conf", keys_file: "/etc/ssh/noust/noust-git.keys", changed: true, ssh_port: 22 });
        },
      },
    );
    await screen.findByRole("heading", { name: "Push from your computer" });
    const form = within(card());

    expect(await form.findByText("This server does not take git pushes yet")).toBeInTheDocument();
    expect(form.getByText(/The account noust-git does not exist yet: run 'noust push setup'/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Turn on git push" })).toBeDisabled();

    await user.click(form.getByRole("button", { name: "Prepare this server" }));

    await waitFor(() => expect(backend.callsTo("POST /api/push/setup")).toHaveLength(1));
    await waitFor(() => expect(within(card()).queryByText("This server does not take git pushes yet")).not.toBeInTheDocument());
    expect(within(card()).getByRole("button", { name: "Turn on git push" })).toBeEnabled();
  });

  it("shows the server's refusal, which names the field, and stays off", { timeout: 40_000 }, async () => {
    const { user } = await page(PUSH_OFF, {
      [`PUT /api/apps/${DOMAIN}/push`]: () => problem(400, "validationerror", "The RSA key has 1024 bits", { hint: "Keys of fewer than 2048 bits are refused." }),
    });
    const form = await turnOnForm();
    await user.click(form.getByRole("button", { name: "Turn on git push" }));

    expect(await screen.findByText("Git push was not turned on for shop.example.com")).toBeInTheDocument();
    expect(within(card()).getByRole("button", { name: "Turn on git push" })).toBeEnabled();
  });
});

describe("git push, on", () => {
  it("shows the branch, the repository, the remote to add and who may push", { timeout: 40_000 }, async () => {
    screenWidth(1440);
    const { container } = await page(ON);
    await screen.findByRole("heading", { name: "Push from your computer" });
    const form = within(card());

    expect(await form.findByText("/var/lib/noust-git/shop.example.com.git")).toBeInTheDocument();
    expect(form.getByText("Never")).toBeInTheDocument();
    expect(form.getByText(`git remote add noust noust-git@${DOMAIN}:${DOMAIN}.git`)).toBeInTheDocument();
    expect(form.getByText("git push noust main")).toBeInTheDocument();
    const keys = form.getByRole("table", { name: "Keys that may push to this application" });
    expect(within(keys).getByText("laptop")).toBeInTheDocument();
    expect(within(keys).getByText("SHA256:abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")).toBeInTheDocument();
    expect(form.getByText(/A key serves one application/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says nobody may push yet when there is no key", { timeout: 40_000 }, async () => {
    await page({ ...ON, keys: [] });
    await screen.findByRole("heading", { name: "Push from your computer" });
    expect(await within(card()).findByText("No key may push yet. Add one below.")).toBeInTheDocument();
  });

  it("adds a key with the name it is given, and clears the form", { timeout: 40_000 }, async () => {
    let keys = ON.keys;
    const { user, backend } = await page(() => ({ ...ON, keys }), {
      [`POST /api/apps/${DOMAIN}/push/keys`]: () => {
        keys = [...ON.keys, { label: "ci", key_type: "ssh-ed25519", fingerprint: "SHA256:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz" }];
        return json(201, keys);
      },
    });
    await screen.findByRole("heading", { name: "Push from your computer" });
    const form = within(card());

    await form.findByRole("heading", { name: "Add a key" });
    const name = last(form.getAllByLabelText("Name of the key"));
    const text = last(form.getAllByLabelText("Public key"));
    await user.type(name, "ci");
    await user.type(text, KEY);
    await user.click(form.getByRole("button", { name: "Add the key" }));

    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${DOMAIN}/push/keys`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/push/keys`)[0]?.body).toEqual({ label: "ci", key: KEY });
    expect(await within(card()).findByText("ci")).toBeInTheDocument();
    expect(name).toHaveValue("");
  });

  it("does not offer to add a key that has no name or no text", { timeout: 40_000 }, async () => {
    await page(ON);
    await screen.findByRole("heading", { name: "Push from your computer" });
    expect(await within(card()).findByRole("button", { name: "Add the key" })).toBeDisabled();
  });

  it("removes a key after asking, and shows the keys that are left", { timeout: 40_000 }, async () => {
    let keys = ON.keys;
    const { user, backend } = await page(() => ({ ...ON, keys }), {
      [`DELETE /api/apps/${DOMAIN}/push/keys/laptop`]: () => {
        keys = [];
        return json(200, []);
      },
    });
    await screen.findByRole("heading", { name: "Push from your computer" });

    await user.click(await within(card()).findByRole("button", { name: "Remove the key laptop" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Stop laptop from pushing?" });
    await user.click(within(confirm).getByRole("button", { name: "Remove the key" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/push/keys/laptop`)).toHaveLength(1));
    expect(await within(card()).findByText("No key may push yet. Add one below.")).toBeInTheDocument();
  });

  it("says when the hook is not what Noust writes, verbatim, and writes it again", { timeout: 40_000 }, async () => {
    let broken = true;
    const { user, backend } = await page(
      () => ({ ...ON, hook_ok: !broken, problems: broken ? [`The hook is not the one Noust writes: noust push refresh ${DOMAIN}`] : [] }),
      {
        [`POST /api/apps/${DOMAIN}/push/refresh`]: () => {
          broken = false;
          return json(200, { ...ON, hook_ok: true, problems: [] });
        },
      },
    );
    await screen.findByRole("heading", { name: "Push from your computer" });
    const form = within(card());

    expect(await form.findByText("The hook needs to be written again")).toBeInTheDocument();
    expect(form.getByText(`The hook is not the one Noust writes: noust push refresh ${DOMAIN}`)).toBeInTheDocument();
    await user.click(form.getByRole("button", { name: "Write the hook again" }));

    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${DOMAIN}/push/refresh`)).toHaveLength(1));
    await waitFor(() => expect(within(card()).queryByText("The hook needs to be written again")).not.toBeInTheDocument());
  });

  it("turns it off after asking, and can delete the repository with it", { timeout: 40_000 }, async () => {
    let state: object = ON;
    const { user, backend } = await page(() => state, {
      [`DELETE /api/apps/${DOMAIN}/push`]: () => {
        state = PUSH_OFF;
        return json(200, PUSH_OFF);
      },
    });
    await screen.findByRole("heading", { name: "Push from your computer" });

    await user.click(await within(card()).findByRole("button", { name: "Turn off git push" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Turn off git push for ${DOMAIN}?` });
    await user.click(within(confirm).getByRole("checkbox", { name: "Also delete the repository, and the code pushed to it" }));
    await user.click(within(confirm).getByRole("button", { name: "Turn off git push" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/push`)).toHaveLength(1));
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/push`)[0]?.search.get("delete_repository")).toBe("true");
    expect(await within(card()).findByRole("button", { name: "Turn on git push" })).toBeInTheDocument();
  });
});

describe("an application a push cannot deploy", () => {
  it("says that it builds in place, how to move it, and asks the server nothing", { timeout: 40_000 }, async () => {
    const { backend } = await page(PUSH_OFF, {}, IN_PLACE_APP);
    await screen.findByRole("heading", { name: "Push from your computer" });
    const form = within(card());

    expect(form.getByText(/Git push needs the release layout, and this application builds in place/)).toBeInTheDocument();
    // The way forward is the page where instant rollback is turned on, not a command to type.
    expect(form.getByRole("link", { name: "Releases and rollback" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/deploys`);
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/push`)).toHaveLength(0);
  });

  it("says that a stack is built from its own files", { timeout: 40_000 }, async () => {
    const { backend } = await page(PUSH_OFF, {}, { ...RELEASE_APP, app_type: "docker-compose", layout: "inplace" });
    await screen.findByRole("heading", { name: "Push from your computer" });
    expect(within(card()).getByText(/A docker-compose application is built from its own files/)).toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/push`)).toHaveLength(0);
  });
});

describe("when the settings cannot be read", () => {
  it("shows the error and offers to try again", { timeout: 40_000 }, async () => {
    const { user, backend } = await page(PUSH_OFF, { [`GET /api/apps/${DOMAIN}/push`]: () => problem(500, "internal", "sshd could not be asked") });
    await screen.findByRole("heading", { name: "Push from your computer" });

    expect(await within(card()).findByText("The git push settings could not be read")).toBeInTheDocument();
    await user.click(within(card()).getByRole("button", { name: /Try again|Retry/ }));
    await waitFor(() => expect(backend.callsTo(`GET /api/apps/${DOMAIN}/push`).length).toBeGreaterThan(1));
  });
});
