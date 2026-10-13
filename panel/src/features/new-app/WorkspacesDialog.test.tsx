import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { workspaceKey } from "./WorkspacesDialog";

// The wizard is lazy-loaded and the dialog reads the repository first, which a busy machine
// stretches past the second a lookup waits by default.
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const REPORT = {
  tool: "turborepo",
  package_manager: "pnpm",
  workspaces: [
    { name: "web", directory: "apps/web", app_type: "nextjs", deployable: true, reason: "", depends_on: ["ui"], watch_paths: ["apps/web", "packages/ui"], build: ["pnpm", "turbo", "build", "--filter=web"] },
    { name: "api", directory: "apps/api", app_type: "nodejs", deployable: true, reason: "", depends_on: [], watch_paths: ["apps/api"], build: null },
    { name: "ui", directory: "packages/ui", app_type: null, deployable: false, reason: "It has no start script, so it is a library", depends_on: [], watch_paths: ["packages/ui"], build: null },
  ],
  problems: ["packages/old has a package.json that is not valid JSON"],
};

function wizard(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes({ ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nextjs", name: "Next.js", default_port: 3000 }] }),
    "POST /api/workspaces/inspect": () => json(200, REPORT),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

async function openIt(user: ReturnType<typeof renderConsole>["user"]) {
  await user.type(await screen.findByLabelText("Repository or directory"), "https://github.com/acme/monorepo.git");
  await user.click(screen.getByRole("button", { name: "Deploy the workspaces of a monorepo" }));
  const dialog = await screen.findByRole("dialog", { name: "Deploy the workspaces of a monorepo" });
  // The source the wizard had is where it starts.
  expect(within(dialog).getByLabelText(/^Repository/)).toHaveValue("https://github.com/acme/monorepo.git");
  await user.click(within(dialog).getByRole("button", { name: "Read the workspaces" }));
  return dialog;
}

describe("the key of a workspace", () => {
  it("is its directory, which is unique where its name may not be", () => {
    expect(workspaceKey({ directory: "apps/web" })).toBe("apps/web");
  });
});

describe("deploying the workspaces of a monorepo", () => {
  it("reads which workspaces are applications and says why the others are not", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard();
    const dialog = await openIt(harness.user);

    expect(await within(dialog).findByText("3 workspaces, managed with turborepo and pnpm.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/workspaces/inspect")[0]?.body).toEqual({ source: "https://github.com/acme/monorepo.git" });
    expect(within(dialog).getByRole("checkbox", { name: /^Deploy web/ })).not.toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: /^Deploy api/ })).not.toBeChecked();
    // A library is not offered, and the repository's own words say why.
    expect(within(dialog).queryByRole("checkbox", { name: /ui/ })).not.toBeInTheDocument();
    expect(within(dialog).getByText(/It has no start script, so it is a library/)).toBeInTheDocument();
    expect(within(dialog).getByText("packages/old has a package.json that is not valid JSON")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deploy 0 applications" })).toBeDisabled();
    await expectNoAxeViolations(document.body);
  });

  it("deploys each chosen workspace on its own domain, in one request", { timeout: 40_000 }, async () => {
    const { backend, harness } = wizard({
      "POST /api/workspaces/deploy": () =>
        json(202, {
          jobs: [
            { job_id: "j1", status: "pending", message: "Queued", job: { id: "j1" } },
            { job_id: "j2", status: "pending", message: "Queued", job: { id: "j2" } },
          ],
        }),
    });
    const { user } = harness;
    const dialog = await openIt(user);
    await user.click(await within(dialog).findByRole("checkbox", { name: /^Deploy web/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /^Deploy api/ }));
    // A chosen workspace shows what makes it deploy, and Deploy waits for every domain.
    expect(within(dialog).getByText("packages/ui")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deploy 2 applications" })).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/^Domain for web/), "www.example.com");
    await user.type(within(dialog).getByLabelText(/^Domain for api/), "api.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Deploy 2 applications" }));

    await waitFor(() => expect(backend.callsTo("POST /api/workspaces/deploy")).toHaveLength(1));
    expect(backend.callsTo("POST /api/workspaces/deploy")[0]?.body).toEqual({
      source: "https://github.com/acme/monorepo.git",
      apps: [
        { workspace: "apps/web", domain: "www.example.com" },
        { workspace: "apps/api", domain: "api.example.com" },
      ],
    });
    expect(await within(dialog).findByText("The applications are being created")).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Open www.example.com" })).toHaveAttribute("href", "/apps/www.example.com");
    expect(within(dialog).getByRole("link", { name: "Open api.example.com" })).toHaveAttribute("href", "/apps/api.example.com");
  });

  it("shows the server's refusal as it said it, and keeps the choices", { timeout: 40_000 }, async () => {
    const { harness } = wizard({
      "POST /api/workspaces/deploy": () => problem(409, "validationerror", "www.example.com is already used by shop-example-com"),
    });
    const { user } = harness;
    const dialog = await openIt(user);
    await user.click(await within(dialog).findByRole("checkbox", { name: /^Deploy web/ }));
    await user.type(within(dialog).getByLabelText(/^Domain for web/), "www.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Deploy 1 application" }));

    expect(await within(dialog).findByText(/www\.example\.com is already used by shop-example-com/)).toBeInTheDocument();
    expect(within(dialog).getByText("The applications could not be created")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/^Domain for web/)).toHaveValue("www.example.com");
  });

  it("says when the repository declares no workspaces", { timeout: 40_000 }, async () => {
    const { harness } = wizard({ "POST /api/workspaces/inspect": () => json(200, { tool: "none", package_manager: "npm", workspaces: [], problems: [] }) });
    const dialog = await openIt(harness.user);

    expect(await within(dialog).findByText("This repository declares no workspaces.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("shows why the repository could not be read", { timeout: 40_000 }, async () => {
    const { harness } = wizard({ "POST /api/workspaces/inspect": () => problem(400, "sourceerror", "Repository not found") });
    const dialog = await openIt(harness.user);

    expect(await within(dialog).findByText("Repository not found")).toBeInTheDocument();
    expect(within(dialog).getByText("The workspaces could not be read")).toBeInTheDocument();
  });
});
