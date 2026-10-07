import { configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";
import { DEFAULT_TEAM, ENVIRONMENTS, PROJECTS, teamsRoutes, viewerSession } from "./testFixtures";

// A test's own budget as well, from its first line: a hook that sets it applies from the next test.
vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

async function shellAt(options: Parameters<typeof teamsRoutes>[0] = {}, session?: Parameters<typeof teamsRoutes>[1], extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({ ...teamsRoutes(options, session), ...extra });
  const harness = renderConsole("/");
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

async function search(user: ReturnType<typeof renderConsole>["user"], text: string) {
  await user.keyboard("{Control>}k{/Control}");
  const input = await screen.findByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
  await user.type(input, text);
}

const ALONE = { teams: [DEFAULT_TEAM], projects: PROJECTS.slice(0, 1), environments: ENVIRONMENTS.slice(0, 1) };

describe("the command palette and teams", () => {
  it("finds a project by its name, its short name or its team, and opens it", async () => {
    const { user, location } = await shellAt();
    // Teams and projects come from the console's own organisation, read once the shell is up.
    await screen.findByRole("button", { name: "Team: All teams" });
    await search(user, "acme web");
    await user.click(await screen.findByRole("option", { name: /^Web/ }));
    await waitFor(() => {
      expect(location().pathname).toBe("/projects/acme/web");
    });
  });

  it("finds a team and opens its page", async () => {
    const { user, location } = await shellAt();
    await screen.findByRole("button", { name: "Team: All teams" });
    await search(user, "acme corp");
    await user.click(await screen.findByRole("option", { name: /^Acme Corp/ }));
    await waitFor(() => {
      expect(location().pathname).toBe("/teams/acme");
    });
  });

  it("offers to make a project and a team to an administrator, and opens the dialog by the link", async () => {
    const { user, location } = await shellAt();
    await screen.findByRole("button", { name: "Team: All teams" });
    await search(user, "new project");
    await user.click(await screen.findByRole("option", { name: /New project/ }));
    await waitFor(() => {
      expect(location().pathname).toBe("/projects");
    });
    expect(await screen.findByRole("dialog", { name: "New project" })).toBeInTheDocument();
  });

  it("offers neither to someone who only reads", async () => {
    const { user } = await shellAt({}, viewerSession());
    await screen.findByRole("button", { name: "Team: All teams" });
    await search(user, "new ");
    await screen.findByRole("option", { name: /^New application$/ });
    expect(screen.queryByRole("option", { name: /New project/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /New team/ })).not.toBeInTheDocument();
  });
});

describe("the sidebar's Projects", () => {
  it("is offered where there is something to organise", async () => {
    await shellAt({}, viewerSession());
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByRole("link", { name: "Projects" })).toHaveAttribute("href", "/projects");
  });

  it("is offered to whoever may start organising, on a server nobody organised", async () => {
    await shellAt(ALONE);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(await within(nav).findByRole("link", { name: "Projects" })).toBeInTheDocument();
  });

  it("is not drawn for someone who only reads, on a server nobody organised: the sidebar is what it always was", async () => {
    await shellAt(ALONE, viewerSession());
    const nav = screen.getByRole("navigation", { name: "Main" });
    await within(nav).findByRole("link", { name: /^Applications/ });
    expect(within(nav).queryByRole("link", { name: "Projects" })).not.toBeInTheDocument();
  });
});
