import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { canChangeSource } from "./ChangeSource";
import { DOMAIN, ELEVATED, IN_PLACE_APP, RELEASE_APP, jobOf, accepted, part, renderSettings } from "./testKit";

const NEW = "https://git.example.org/team/shop.git";
const HEAD = "4f1c2b9d8e7a6f5c4b3a29181716151413121110";

const KEY = {
  domain: DOMAIN,
  repository: "github.com/shop/storefront",
  source_is_ssh: false,
  ssh_url: null,
  key: null,
  shared_with: [],
  server_key_fingerprint: "SHA256:ServerWideKey0000000000000000000000000000000",
  warnings: [],
};

const PLAN = {
  domain: DOMAIN,
  current: RELEASE_APP.source,
  target: NEW,
  current_branch: "main",
  target_branch: "release",
  current_kind: "git",
  target_kind: "git",
  same_repository: false,
  head: HEAD,
  checked: true,
  pinned: false,
  previews: [],
  warnings: ["This is another repository, not the same one at a new address."],
  changed: true,
};

/** Renders the Source subsection and waits for the card this file is about. */
async function source(app: object = RELEASE_APP, routes: Record<string, RouteHandler> = {}) {
  const rendered = await renderSettings("/source", "Source", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/deploy-key`]: () => json(200, KEY),
    [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, { domain: DOMAIN, repository: KEY.repository, host: "github.com", configured: false, username: null, source_is_https: true }),
    [`GET /api/apps/${DOMAIN}/source-options`]: () => json(200, { domain: DOMAIN, submodules: true, lfs: "auto", shallow: true, watch_paths: [], skip_markers: true, customised: false }),
    [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, { domain: DOMAIN, enabled: false, state: "disabled", layout: "releases", inplace_warning: false, hooks: { exposed: false, base_url: null, hook_url: null, hook_url_public: false, content_type: "application/json", events: ["push"] }, branch: { tracked: "main", pinned: true, any_push_deploys: false }, forge: { forge: null, host: null, repository: null, settings_url: null }, github_app: { configured: false, hooks_active: false, covers_repository: false, account: null, repository_selection: null, settings_url: null }, deliveries: { total: 0, refused_since_last_verified: 0, last: null, last_verified_at: null, last_push_at: null } }),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    ...routes,
  });
  await screen.findByRole("heading", { name: "Where the code comes from" });
  return rendered;
}

const card = () => part("Where the code comes from");

describe("which applications can change their source", () => {
  it("are the ones on releases that are not a stack, a container, a preview or a push repository", () => {
    expect(canChangeSource({ layout: "releases", app_type: "nodejs", source: NEW })).toBe("yes");
    expect(canChangeSource({ layout: "inplace", app_type: "nodejs" })).toBe("inPlace");
    expect(canChangeSource({ layout: "releases", app_type: "docker-compose" })).toBe("type");
    expect(canChangeSource({ layout: "releases", app_type: "container" })).toBe("type");
    expect(canChangeSource({ layout: "releases", app_type: "nodejs", preview_parent: "shop.example.com" })).toBe("preview");
    expect(canChangeSource({ layout: "releases", app_type: "nodejs", source: "push:shop.example.com" })).toBe("push");
  });
});

describe("changing the source", () => {
  it("shows where the code comes from and offers to change it", { timeout: 40_000 }, async () => {
    const { container } = await source();
    const section = within(card());

    expect(section.getByText(RELEASE_APP.source)).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Change the source" })).toBeEnabled();
    await expectNoAxeViolations(container);
  });

  it("checks the new source first, shows what it does not carry over, and writes only after the check", { timeout: 60_000 }, async () => {
    const { user, backend, container } = await source(RELEASE_APP, {
      [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, PLAN),
      [`PUT /api/apps/${DOMAIN}/source`]: () => json(200, { ...PLAN, applied: true }),
    });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog", { name: `Change the source of ${DOMAIN}` }));

    await user.type(dialog.getByLabelText(/^New source/), NEW);
    await user.type(dialog.getByLabelText(/^Branch/), "release");
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)[0]?.body).toEqual({ source: NEW, branch: "release", check: true });
    expect(await dialog.findByText("This is another repository, not the same one at a new address.")).toBeInTheDocument();
    expect(dialog.getByText("What this does not carry over")).toBeInTheDocument();
    expect(dialog.getByText(HEAD.slice(0, 12))).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)).toHaveLength(0);
    await expectNoAxeViolations(container);

    await user.click(dialog.getByRole("button", { name: "Change the source" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)[0]?.body).toEqual({ source: NEW, branch: "release", check: true });
    expect(await within(card()).findByText(`${DOMAIN} deploys from the new source`)).toBeInTheDocument();
    expect(within(card()).getByText("What serves now keeps serving. The next update builds the new source.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("sends no branch when none was typed, and lets the remote check be turned off", { timeout: 60_000 }, async () => {
    const { user, backend } = await source(RELEASE_APP, { [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, { ...PLAN, checked: false, head: null }) });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText(/^New source/), NEW);
    await user.click(dialog.getByRole("checkbox", { name: /Ask the remote first/ }));
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    await waitFor(() => expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)).toHaveLength(1));
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)[0]?.body).toEqual({ source: NEW, check: false });
  });

  it("throws the check away when a field is edited, so what is confirmed is what was shown", { timeout: 60_000 }, async () => {
    const { user } = await source(RELEASE_APP, { [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, PLAN) });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText(/^New source/), NEW);
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));
    expect(await dialog.findByRole("button", { name: "Change the source" })).toBeInTheDocument();

    await user.type(dialog.getByLabelText(/^New source/), "x");

    expect(dialog.getByRole("button", { name: "Check the new source" })).toBeInTheDocument();
    expect(dialog.queryByText("What this does not carry over")).not.toBeInTheDocument();
  });

  it("asks for a source before asking the server anything", { timeout: 40_000 }, async () => {
    const { user, backend } = await source();
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    expect(await dialog.findByText("Name the new source.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/source/plan`)).toHaveLength(0);
  });

  it("shows what git answered when the remote cannot be read, and changes nothing", { timeout: 60_000 }, async () => {
    const { user, backend } = await source(RELEASE_APP, {
      [`POST /api/apps/${DOMAIN}/source/plan`]: () => problem(400, "sourceerror", "Cannot read the branches of https://git.example.org/team/shop.git", { output: "fatal: repository 'https://git.example.org/team/shop.git/' not found", hint: "Check the address, or give the server a deploy key." }),
    });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText(/^New source/), NEW);
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    expect(await dialog.findByText("The new source could not be checked")).toBeInTheDocument();
    expect(dialog.getByText(/fatal: repository .* not found/)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Check the new source" })).toBeEnabled();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source`)).toHaveLength(0);
  });

  it("puts a refusal that names the source field under that field", { timeout: 60_000 }, async () => {
    const { user } = await source(RELEASE_APP, {
      [`POST /api/apps/${DOMAIN}/source/plan`]: () => problem(400, "validationerror", "The new source carries a credential", { fields: { source: "The new source carries a credential" } }),
    });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText(/^New source/), "https://user:secret@git.example.org/team/shop.git");
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    expect(await dialog.findByText("The new source carries a credential")).toBeInTheDocument();
  });

  it("says there is nothing to change when the plan finds the same source", { timeout: 60_000 }, async () => {
    const { user } = await source(RELEASE_APP, { [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, { ...PLAN, target: RELEASE_APP.source, target_branch: "main", changed: false, warnings: [] }) });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));

    await user.type(dialog.getByLabelText(/^New source/), RELEASE_APP.source);
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));

    expect(await dialog.findByText("This application already deploys from there.")).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "Change the source" })).toBeDisabled();
  });

  it("queues an update of the new source when asked, forcing the rebuild", { timeout: 60_000 }, async () => {
    const { user, backend } = await source(RELEASE_APP, {
      [`POST /api/apps/${DOMAIN}/source/plan`]: () => json(200, PLAN),
      [`PUT /api/apps/${DOMAIN}/source`]: () => json(200, { ...PLAN, applied: true }),
      "POST /api/jobs/update": () => json(202, accepted(jobOf("job-1", "update"))),
    });
    await user.click(within(card()).getByRole("button", { name: "Change the source" }));
    const dialog = within(await screen.findByRole("dialog"));
    await user.type(dialog.getByLabelText(/^New source/), NEW);
    await user.click(dialog.getByRole("button", { name: "Check the new source" }));
    await user.click(await dialog.findByRole("button", { name: "Change the source" }));

    await user.click(await within(card()).findByRole("button", { name: "Update" }));

    await waitFor(() => expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1));
    expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: DOMAIN, force: true });
  });
});

describe("an application whose source is not this to change", () => {
  it("says an in-place application is moved onto releases first", { timeout: 40_000 }, async () => {
    await source({ ...IN_PLACE_APP, source: NEW });

    expect(within(card()).getByText(/builds in place\. Move it onto releases first/)).toBeInTheDocument();
    expect(within(card()).queryByRole("button", { name: "Change the source" })).not.toBeInTheDocument();
  });

  it("says a push repository is turned off in Deploy on push first", { timeout: 40_000 }, async () => {
    await source({ ...RELEASE_APP, source: `push:${DOMAIN}` });

    expect(within(card()).getByText(/Turn that off in Deploy on push first/)).toBeInTheDocument();
    expect(within(card()).queryByRole("button", { name: "Change the source" })).not.toBeInTheDocument();
  });
});

describe("in Spanish", () => {
  it("speaks Spanish", { timeout: 40_000 }, async () => {
    await source();
    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { name: "De dónde viene el código" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cambiar el origen" })).toBeInTheDocument();
  });
});
