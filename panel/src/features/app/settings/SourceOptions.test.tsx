import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { outcomeView } from "./webhook";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings } from "./testKit";

const REPOSITORY = "github.com/shop/storefront";

const DEFAULTS = {
  domain: DOMAIN,
  submodules: false,
  lfs: "auto",
  shallow: true,
  watch_paths: [] as string[],
  skip_markers: true,
  customised: false,
};

const NO_KEY = {
  domain: DOMAIN,
  repository: REPOSITORY,
  source_is_ssh: false,
  ssh_url: null,
  key: null,
  shared_with: [],
  server_key_fingerprint: null,
  warnings: [],
};

async function source(options: object | (() => object), routes: Record<string, RouteHandler> = {}) {
  const rendered = await renderSettings("/source", "Source", RELEASE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/deploy-key`]: () => json(200, NO_KEY),
    [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, { domain: DOMAIN, repository: REPOSITORY, host: "github.com", configured: false, username: null, source_is_https: true }),
    [`GET /api/apps/${DOMAIN}/webhook`]: () => json(404, {}),
    "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/source-options`]: () => json(200, typeof options === "function" ? options() : options),
    ...routes,
  });
  await screen.findByRole("heading", { name: "Fetching and deploying" });
  return rendered;
}

const card = () => part("Fetching and deploying");

describe("the source options of an application", () => {
  it("shows what applies, with the defaults when nothing was changed", { timeout: 30_000 }, async () => {
    const { container } = await source(DEFAULTS);
    const form = within(card());

    expect(await form.findByRole("switch", { name: /Fetch submodules/ })).not.toBeChecked();
    expect(form.getByRole("switch", { name: /Shallow clone/ })).toBeChecked();
    expect(form.getByRole("switch", { name: /Skip pushes marked/ })).toBeChecked();
    expect(form.getByLabelText(/Watch paths/)).toHaveValue("");
    expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
    // Nothing was changed, so there is nothing to go back to.
    expect(form.queryByRole("button", { name: "Back to the defaults" })).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("saves what was changed, one glob per line, and shows the stored result", { timeout: 40_000 }, async () => {
    let stored: object = DEFAULTS;
    const { user, backend } = await source(() => stored, {
      [`PUT /api/apps/${DOMAIN}/source-options`]: (call) => {
        const body = call.body as Record<string, unknown>;
        stored = { ...DEFAULTS, ...body, customised: true };
        return json(200, stored);
      },
    });
    const form = within(card());
    await user.click(await form.findByRole("switch", { name: /Fetch submodules/ }));
    await user.type(form.getByLabelText(/Watch paths/), "apps/web/**{Enter}{Enter}!**/*.md");
    await user.click(form.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source-options`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/source-options`)[0]?.body).toEqual({
      submodules: true,
      lfs: "auto",
      shallow: true,
      skip_markers: true,
      watch_paths: ["apps/web/**", "!**/*.md"],
    });
    expect(await form.findByRole("button", { name: "Back to the defaults" })).toBeInTheDocument();
    expect(form.getByLabelText(/Watch paths/)).toHaveValue("apps/web/**\n!**/*.md");
    expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("puts the server's refusal of a pattern beside the field it is about", { timeout: 40_000 }, async () => {
    const { user } = await source(DEFAULTS, {
      [`PUT /api/apps/${DOMAIN}/source-options`]: () => json(400, { error: "validation_error", detail: "watch_paths: '..' climbs out of the repository", fields: { watch_paths: "'..' climbs out of the repository" } }),
    });
    const form = within(card());
    await user.type(await form.findByLabelText(/Watch paths/), "../x");
    await user.click(form.getByRole("button", { name: "Save" }));

    expect(await form.findByText(/climbs out of the repository/)).toBeInTheDocument();
    // What was typed is kept, to be corrected.
    expect(form.getByLabelText(/Watch paths/)).toHaveValue("../x");
  });

  it("goes back to the defaults after asking", { timeout: 40_000 }, async () => {
    let stored: object = { ...DEFAULTS, submodules: true, customised: true };
    const { user, backend } = await source(() => stored, {
      [`DELETE /api/apps/${DOMAIN}/source-options`]: () => {
        stored = DEFAULTS;
        return json(200, DEFAULTS);
      },
    });
    const form = within(card());
    await user.click(await form.findByRole("button", { name: "Back to the defaults" }));
    await user.click(await screen.findByRole("button", { name: "Use the defaults" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/source-options`)).toHaveLength(1));
    await waitFor(() => expect(form.getByRole("switch", { name: /Fetch submodules/ })).not.toBeChecked());
  });

  it("says when it could not be read", { timeout: 30_000 }, async () => {
    await renderSettings("/source", "Source", RELEASE_APP, {
      "GET /api/auth/session": () => json(200, ELEVATED),
      [`GET /api/apps/${DOMAIN}/deploy-key`]: () => json(200, NO_KEY),
      [`GET /api/apps/${DOMAIN}/git-credential`]: () => json(200, { domain: DOMAIN, repository: REPOSITORY, host: "github.com", configured: false, username: null, source_is_https: true }),
      [`GET /api/apps/${DOMAIN}/webhook`]: () => json(404, {}),
      "GET /api/deploy-keys": () => json(200, { keys: [], total: 0 }),
      [`GET /api/apps/${DOMAIN}/source-options`]: () => problem(500, "server_error", "boom"),
    });

    expect(await screen.findByText("Could not read how this app is fetched")).toBeInTheDocument();
  });
});

describe("the deliveries the rules ignore", () => {
  it("are named for what they came to", () => {
    expect(outcomeView("ignored_skip", "en")).toEqual({ state: "stopped", label: "Marked to skip, ignored" });
    expect(outcomeView("ignored_paths", "en")).toEqual({ state: "stopped", label: "No watched path touched, ignored" });
  });
});
