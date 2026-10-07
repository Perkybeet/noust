import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import type { AppBuild } from "../../build/model";
import { DOMAIN, ELEVATED, RELEASE_APP, STATIC_APP, first, renderSettings, saveBar } from "./testKit";

const REPO: AppBuild = {
  domain: DOMAIN,
  source: "repo",
  base_directory: "apps/web",
  install: "pnpm install --frozen-lockfile",
  build: "pnpm --filter web build",
  start: "node dist/server.js --port ${PORT}",
  publish_directory: null,
  runtime: "node@22",
  document: null,
  repository_error: null,
  applied: null,
};

const NONE: AppBuild = { ...REPO, source: "none", base_directory: null, install: null, build: null, start: null, runtime: null };

const OPERATOR: AppBuild = { ...REPO, source: "operator", document: "build:\n  start: node dist/server.js\n" };

/**
 * A field by its label: the whole of it, with the "Optional" every field of this form carries.
 * (The section is named "Build commands" and the runtimes' group "Runtimes to start from", so a
 * prefix would match them too, and typing into a region does nothing.)
 */
function field(name: string): RegExp {
  return new RegExp(`^${name}(Optional)?$`);
}

function buildOf(build: AppBuild, app: object = RELEASE_APP, extra: Record<string, RouteHandler> = {}) {
  return renderSettings("/build", "Build commands", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/build`]: () => json(200, build),
    ...extra,
  });
}

describe("the Build commands subsection", () => {
  it("is listed for a type that has a block, and says where the commands come from", { timeout: 30_000 }, async () => {
    const { container } = await buildOf(REPO);
    const nav = first(screen.getAllByRole("navigation", { name: "Application settings" }));
    expect(within(nav).getByRole("link", { name: "Build commands" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/build`);
    const main = screen.getByRole("main");
    expect(await within(main).findByText("The repository's noust.yaml", { selector: "span" })).toBeInTheDocument();
    // The precedence rule, said where it matters.
    expect(within(main).getByText("This application's own settings win, whole")).toBeInTheDocument();
    expect(within(main).getByText(/Saving them here makes them this application's own/)).toBeInTheDocument();
    expect(within(main).getByLabelText(field("Base directory"))).toHaveValue("apps/web");
    expect(within(main).getByLabelText(field("Install command"))).toHaveValue("pnpm install --frozen-lockfile");
    expect(within(main).getByLabelText(field("Build command"))).toHaveValue("pnpm --filter web build");
    expect(within(main).getByLabelText(field("Start command"))).toHaveValue("node dist/server.js --port ${PORT}");
    expect(within(main).getByLabelText(field("Runtime"))).toHaveValue("node@22");
    // A Next.js application serves no static output: no publish directory to offer.
    expect(within(main).queryByLabelText(field("Publish directory"))).not.toBeInTheDocument();
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says that nothing is set when neither the application nor its repository say anything", { timeout: 30_000 }, async () => {
    await buildOf(NONE);
    expect(await screen.findByText("The commands of the application's type")).toBeInTheDocument();
    expect(screen.getByLabelText(field("Install command"))).toHaveValue("");
    expect(screen.queryByText(/Saving them here makes them/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove its own settings" })).not.toBeInTheDocument();
  });

  it("saves only the keys that are set, as the application's own block, which then wins whole", { timeout: 40_000 }, async () => {
    let stored: AppBuild = NONE;
    const { user, backend } = await buildOf(NONE, RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/build`]: () => json(200, stored),
      [`PUT /api/apps/${DOMAIN}/build`]: () => {
        stored = { ...NONE, source: "operator", start: "node dist/server.js", runtime: "node@22", document: "build:\n  start: node dist/server.js\n  runtime: node@22\n" };
        return json(200, stored);
      },
    });
    await user.type(await screen.findByLabelText(field("Start command")), "node dist/server.js");
    await user.click(screen.getByRole("button", { name: "node@22" }));
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/build`)).toHaveLength(1));
    // Only what was said: a key left out is not set.
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/build`)[0]?.body).toEqual({ start: "node dist/server.js", runtime: "node@22" });
    expect(await screen.findByText("Saved. It takes effect with the next update.")).toBeInTheDocument();
    expect(await screen.findByText("This application's own settings", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove its own settings" })).toBeInTheDocument();
  });

  it("asks to confirm it is you before it saves, because these commands run on the server", { timeout: 40_000 }, async () => {
    const { user, backend } = await buildOf(NONE, RELEASE_APP, {
      "GET /api/auth/session": () => json(200, { ...ELEVATED, elevated_until: null }),
      "POST /api/auth/elevate": () => json(200, { elevated_until: "2999-01-01T00:00:00+00:00" }),
      [`PUT /api/apps/${DOMAIN}/build`]: () => json(200, { ...NONE, source: "operator", build: "make" }),
    });
    await user.type(await screen.findByLabelText(field("Build command")), "make");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    const elevate = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(elevate).getByRole("textbox"), "123456");
    await user.click(within(elevate).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/build`)).toHaveLength(1));
  });

  it("warns that a pipe or && is no shell here, and does not stop the save", { timeout: 30_000 }, async () => {
    const { user } = await buildOf(NONE);
    await user.type(await screen.findByLabelText(field("Build command")), "npm ci && npm run build");
    expect(screen.getByText(/pipe, &&, a redirection or a substitution in it: they are ordinary arguments here/)).toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("says what is wrong before it is sent, beside the field, and sends nothing", { timeout: 30_000 }, async () => {
    const { user, backend } = await buildOf(NONE);
    await user.type(await screen.findByLabelText(field("Base directory")), "../outside");
    await user.type(screen.getByLabelText(field("Runtime")), "ruby@3");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A path inside the repository: relative, without .., ~, spaces or quotes.")).toBeInTheDocument();
    expect(screen.getByText("A runtime is a language, @ and a version: node@22, python@3.12, php@8.3, or lts or latest.")).toBeInTheDocument();
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/build`)).toHaveLength(0);
  });

  it("puts the server's refusal, which names the field of the block, beside that field", { timeout: 30_000 }, async () => {
    const { user } = await buildOf(NONE, RELEASE_APP, {
      [`PUT /api/apps/${DOMAIN}/build`]: () =>
        problem(400, "validationerror", "build.base_directory: a link anywhere on the way is refused", {
          fields: { "build.base_directory": "A link anywhere on the way to the base directory is refused." },
        }),
    });
    await user.type(await screen.findByLabelText(field("Base directory")), "apps/web");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("A link anywhere on the way to the base directory is refused.")).toBeInTheDocument();
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("shows the repository's invalid noust.yaml verbatim, and what the unit was last written from", { timeout: 30_000 }, async () => {
    await buildOf({
      ...NONE,
      repository_error: "build.strat: unknown key; did you mean build.start?",
      applied: { start: "/var/www/apps/shop/current/bin/api", working_directory: "/var/www/apps/shop/current/apps/api", path: "/opt/noust/runtimes/node/22/bin", runtime: { language: "node", version: "22.11.0" } },
    });
    expect(await screen.findByText("The repository's noust.yaml is not valid")).toBeInTheDocument();
    expect(screen.getByText("build.strat: unknown key; did you mean build.start?")).toBeInTheDocument();
    expect(screen.getByText("/var/www/apps/shop/current/bin/api")).toBeInTheDocument();
    expect(screen.getByText("/var/www/apps/shop/current/apps/api")).toBeInTheDocument();
    expect(screen.getByText("node 22.11.0")).toBeInTheDocument();
  });

  it("removes the application's own settings so the repository's apply again, after asking", { timeout: 40_000 }, async () => {
    let stored: AppBuild = OPERATOR;
    const { user, backend } = await buildOf(OPERATOR, RELEASE_APP, {
      [`GET /api/apps/${DOMAIN}/build`]: () => json(200, stored),
      [`DELETE /api/apps/${DOMAIN}/build`]: () => {
        stored = REPO;
        return json(200, stored);
      },
    });
    await user.click(await screen.findByRole("button", { name: "Remove its own settings" }));
    const confirm = await screen.findByRole("alertdialog", { name: `Remove the build commands of ${DOMAIN}?` });
    await user.click(within(confirm).getByRole("button", { name: "Remove its own settings" }));
    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/build`)).toHaveLength(1));
    expect(await screen.findByText("The repository's noust.yaml", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove its own settings" })).not.toBeInTheDocument();
  });

  it("offers a static site its publish directory and no start command, and a PHP application only what PHP does", { timeout: 40_000 }, async () => {
    await buildOf({ ...NONE, source: "none" }, STATIC_APP);
    expect(await screen.findByLabelText(field("Publish directory"))).toBeInTheDocument();
    expect(screen.queryByLabelText(field("Start command"))).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "python@3.12" })).toBeInTheDocument();
  });

  it("offers a PHP application only its install, its build and its runtime", { timeout: 40_000 }, async () => {
    await buildOf(NONE, { ...RELEASE_APP, app_type: "php-fpm" });
    expect(await screen.findByLabelText(field("Install command"))).toBeInTheDocument();
    expect(screen.queryByLabelText(field("Base directory"))).not.toBeInTheDocument();
    expect(screen.queryByLabelText(field("Start command"))).not.toBeInTheDocument();
    expect(screen.queryByLabelText(field("Publish directory"))).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "php@8.3" })).toBeInTheDocument();
  });

  it("has no block for what is built by its own configuration, and does not ask for one", { timeout: 30_000 }, async () => {
    const { backend } = await renderSettings("/build", "Build commands", { ...RELEASE_APP, app_type: "docker-compose" });
    expect(screen.getByText("A docker-compose application is built by its own configuration, so it has no build commands here.")).toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/build`)).toHaveLength(0);
    const nav = first(screen.getAllByRole("navigation", { name: "Application settings" }));
    expect(within(nav).queryByRole("link", { name: "Build commands" })).not.toBeInTheDocument();
  });

  it("points at the command that lists the runtimes a server has, as no endpoint says it", { timeout: 30_000 }, async () => {
    await buildOf(REPO);
    expect(await screen.findByText("noust runtime list")).toBeInTheDocument();
  });
});
