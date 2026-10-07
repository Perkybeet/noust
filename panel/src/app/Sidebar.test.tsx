/**
 * The mark of the page the operator is on (checklist M-59). The item's fill is within 1.1 to 1.3
 * of the ground (tokens.test.ts holds the 3:1 of the bar against it, in both themes), so the
 * current item also has a bar, and only the current item does. `e2e/ux-parity.spec.ts` reads
 * the rendered colours.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { teamsRoutes } from "../features/teams/testFixtures";
import { fakeBackend, problem, signedInRoutes } from "../test/fakes";

async function shellAt(path: string) {
  fakeBackend(signedInRoutes());
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  // The destinations are held as a skeleton until the server's teams are read.
  await waitFor(() => {
    expect(screen.getByRole("navigation", { name: "Main" })).not.toHaveAttribute("aria-busy");
  });
  return harness;
}

describe("the sidebar's current item", () => {
  it("has a bar of its own, and it is the only item that has one", async () => {
    await shellAt("/apps");
    const nav = screen.getByRole("navigation", { name: "Main" });
    const current = within(nav).getByRole("link", { name: /^Applications/ });
    expect(current).toHaveAttribute("aria-current", "page");
    expect(current.querySelector("[data-nav-indicator]")).not.toBeNull();
    for (const link of within(nav).getAllByRole("link")) {
      if (link !== current) expect(link.querySelector("[data-nav-indicator]"), link.textContent).toBeNull();
    }
  });

  it("moves the bar with the page", async () => {
    const { user } = await shellAt("/apps");
    await user.click(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Databases" }));
    await screen.findByRole("heading", { level: 1, name: "Databases" });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Databases" }).querySelector("[data-nav-indicator]")).not.toBeNull();
    // A failed count joins the name once the machine is read: "Applications 1 failed".
    expect(within(nav).getByRole("link", { name: /^Applications/ }).querySelector("[data-nav-indicator]")).toBeNull();
  });

  it("is decoration for a screen reader: aria-current already says where it is", async () => {
    await shellAt("/apps");
    const bar = document.querySelector("[data-nav-indicator]");
    expect(bar).toHaveAttribute("aria-hidden", "true");
    await expectNoAxeViolations(screen.getByRole("navigation", { name: "Main" }));
  });
});

describe("the sidebar while the server's teams are read", () => {
  it("holds its destinations as a skeleton, so the Projects link that follows pushes nothing down", async () => {
    const routes = teamsRoutes();
    const mine = routes["GET /api/teams/mine"];
    if (mine === undefined) throw new Error("the fixture has no teams route");
    let answer: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      answer = resolve;
    });
    fakeBackend({
      ...routes,
      "GET /api/teams/mine": async (call) => {
        await gate;
        return mine(call);
      },
    });
    renderConsole("/apps");
    await screen.findByRole("heading", { level: 1 });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav).toHaveAttribute("aria-busy", "true");
    expect(within(nav).queryByRole("link")).not.toBeInTheDocument();
    expect(nav.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);

    answer();
    expect(await within(nav).findByRole("link", { name: "Projects" })).toBeInTheDocument();
    expect(nav).not.toHaveAttribute("aria-busy");
    expect(nav.querySelector('[data-slot="skeleton"]')).toBeNull();
  });

  it("draws the links at once on a server that does not answer for teams, and without Projects", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/teams/mine": () => problem(404, "notfound", "Not found", {}) });
    renderConsole("/apps");
    await screen.findByRole("heading", { level: 1 });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(await within(nav).findByRole("link", { name: /^Applications/ })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Projects" })).not.toBeInTheDocument();
    expect(nav).not.toHaveAttribute("aria-busy");
  });
});
