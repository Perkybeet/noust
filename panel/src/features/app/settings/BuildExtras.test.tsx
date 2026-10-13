import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import type { AppBuild } from "../../build/model";
import { DOMAIN, ELEVATED, RELEASE_APP, accepted, jobOf, part, renderSettings } from "./testKit";

const NONE: AppBuild = {
  domain: DOMAIN,
  source: "none",
  base_directory: null,
  install: null,
  build: null,
  start: null,
  publish_directory: null,
  runtime: null,
  document: null,
  repository_error: null,
  applied: null,
};

const CACHE = { domain: DOMAIN, path: "/var/cache/noust/build/shop-example-com", exists: true, size_bytes: 52_428_800 };

const NO_PROCESSES = { domain: DOMAIN, source: "none", problem: "", procfile_honoured: false, processes: [], notes: [] };

function build(routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/build", "Build commands", RELEASE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/build`]: () => json(200, NONE),
    [`GET /api/apps/${DOMAIN}/build-cache`]: () => json(200, CACHE),
    [`GET /api/apps/${DOMAIN}/processes`]: () => json(200, NO_PROCESSES),
    ...routes,
  });
}

describe("the build cache", () => {
  it("says how much it holds and where", { timeout: 30_000 }, async () => {
    const { container } = await build();
    await screen.findByRole("heading", { name: "Build cache" });
    const card = within(part("Build cache"));

    expect(await card.findByText("50 MB")).toBeInTheDocument();
    expect(card.getByText("/var/cache/noust/build/shop-example-com")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("is emptied after asking, and says it was", { timeout: 40_000 }, async () => {
    let cache = CACHE;
    const { user, backend } = await build({
      [`GET /api/apps/${DOMAIN}/build-cache`]: () => json(200, cache),
      [`DELETE /api/apps/${DOMAIN}/build-cache`]: () => {
        const freed = { ...CACHE };
        cache = { ...CACHE, size_bytes: 0 };
        return json(200, freed);
      },
    });
    await screen.findByRole("heading", { name: "Build cache" });
    const card = within(part("Build cache"));
    await user.click(await card.findByRole("button", { name: "Empty the cache" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Empty the cache" }));

    await waitFor(() => expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/build-cache`)).toHaveLength(1));
    // Read again: nothing is left to empty.
    await waitFor(() => expect(within(part("Build cache")).getByRole("button", { name: "Empty the cache" })).toBeDisabled());
  });

  it("builds the live version again from nothing", { timeout: 40_000 }, async () => {
    const { user, backend } = await build({
      "POST /api/jobs/update": () => json(202, accepted(jobOf("job-fresh", "update"))),
    });
    await screen.findByRole("heading", { name: "Build cache" });
    await user.click(await within(part("Build cache")).findByRole("button", { name: "Rebuild without cache" }));

    await waitFor(() => expect(backend.callsTo("POST /api/jobs/update")).toHaveLength(1));
    expect(backend.callsTo("POST /api/jobs/update")[0]?.body).toEqual({ domain: DOMAIN, force: true, no_cache: true });
  });

  it("has nothing to empty when it holds nothing", { timeout: 30_000 }, async () => {
    await build({ [`GET /api/apps/${DOMAIN}/build-cache`]: () => json(200, { ...CACHE, exists: false, size_bytes: 0 }) });
    await screen.findByRole("heading", { name: "Build cache" });
    const card = within(part("Build cache"));

    expect(await card.findByRole("button", { name: "Empty the cache" })).toBeDisabled();
    expect(card.getByRole("button", { name: "Rebuild without cache" })).toBeEnabled();
  });
});

describe("the process types", () => {
  const DECLARED = {
    domain: DOMAIN,
    source: "noust.yaml",
    problem: "",
    procfile_honoured: false,
    processes: [
      { name: "web", argv: ["./bin/server"], command: "./bin/server", source: "noust.yaml", started_by_noust: true, in_effect: true },
      { name: "worker", argv: ["./bin/jobs"], command: "./bin/jobs", source: "noust.yaml", started_by_noust: false, in_effect: false },
    ],
    notes: ["worker is declared here; a deployment starts only web and runs release."],
  };

  it("lists what is declared, which a deployment uses, and why the rest is not", { timeout: 30_000 }, async () => {
    const { container } = await build({ [`GET /api/apps/${DOMAIN}/processes`]: () => json(200, DECLARED) });
    await screen.findByRole("heading", { name: "Process types" });
    const card = within(part("Process types"));

    expect(await card.findByText("Declared in noust.yaml")).toBeInTheDocument();
    const [web, release] = card.getAllByRole("listitem");
    if (!web || !release) throw new Error("expected two process rows");
    expect(within(web).getByText("web")).toBeInTheDocument();
    expect(within(web).getByText("Used by deployments")).toBeInTheDocument();
    expect(within(release).getByText("Declared")).toBeInTheDocument();
    expect(card.getByText(/a deployment starts only web and runs release/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("says a Procfile cannot be used, with the server's words", { timeout: 30_000 }, async () => {
    await build({
      [`GET /api/apps/${DOMAIN}/processes`]: () => json(200, { ...NO_PROCESSES, source: "none", problem: "Procfile line 1 (web) needs a shell ('&&'). Put the commands in a script." }),
    });

    expect(await screen.findByText("A Procfile that cannot be used")).toBeInTheDocument();
    expect(screen.getByText(/needs a shell/)).toBeInTheDocument();
  });

  it("is not on the page when the repository declares nothing", { timeout: 30_000 }, async () => {
    const { backend } = await build();
    await waitFor(() => expect(backend.callsTo(`GET /api/apps/${DOMAIN}/processes`)).toHaveLength(1));

    await waitFor(() => expect(screen.queryByRole("heading", { name: "Process types" })).not.toBeInTheDocument());
  });
});
