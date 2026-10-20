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
import { overviewFixture } from "../features/overview/testFixtures";
import { teamsRoutes } from "../features/teams/testFixtures";
import { NODES, fakeBackend, json, problem, signedInRoutes } from "../test/fakes";

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

describe("the sidebar's counters", () => {
  it("count what the inbox lists, red for what failed and amber for what is about to", async () => {
    const failedApp = { kind: "state", severity: "fail", code: "service_failed", params: {}, detail: null, when: null, deployment_id: null, actions: [] };
    const expired = { kind: "certificate", severity: "fail", code: "certificate_expired", params: {}, detail: null, when: null, deployment_id: null, actions: [] };
    const soon = { kind: "certificate", severity: "warn", code: "certificate_expires_in", params: { days: 9 }, detail: null, when: null, deployment_id: null, actions: [] };
    const unit = { kind: "unit", severity: "fail", code: "unit_failed", params: {}, detail: null, when: null, deployment_id: null, actions: [] };
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/overview": () =>
        json(
          200,
          overviewFixture({
            attention_total: 4,
            attention: [
              { id: "app:shop.example.com", subject: { kind: "app", domain: "shop.example.com" }, title: "shop.example.com", severity: "fail", reasons: [failedApp] },
              { id: "certificate:old.example.com", subject: { kind: "certificate", domain: "old.example.com" }, title: "old.example.com", severity: "fail", reasons: [expired] },
              { id: "certificate:new.example.com", subject: { kind: "certificate", domain: "new.example.com" }, title: "new.example.com", severity: "warn", reasons: [soon] },
              { id: "unit:noust-web", subject: { kind: "unit", name: "noust-web" }, title: "noust-web", severity: "fail", reasons: [unit] },
            ],
          }),
        ),
    });
    renderConsole("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(await within(nav).findByRole("link", { name: "Applications 1 failed" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Domains 1 failed 1 certificate needs renewing" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Server 1 failed" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Databases" })).toBeInTheDocument();
  });
});

describe("the sidebar follows the scope", () => {
  it("keeps Applications lit on the recipes, which are its second and third view", async () => {
    await shellAt("/catalog");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: /^Applications/ })).toHaveAttribute("aria-current", "page");
    expect(within(nav).queryByRole("link", { name: /^Catalog/ })).toBeNull();
  });

  it("keeps Overview lit on the Map, which is its second view, and nothing else", async () => {
    await shellAt("/map");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page")).toHaveLength(1);
  });

  it("is the fleet's seven destinations on the fleet's pages, with the fleet's Overview lit only on /fleet", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/fleet/apps");
    await screen.findByRole("heading", { level: 1 });
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getAllByRole("link").map((link) => link.textContent)).toEqual(["Overview", "Servers", "Applications", "Certificates", "Backups", "Updates", "Activity"]);
    expect(within(nav).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("keeps Applications of the fleet lit on the applications that run on several servers", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/fleet/placements");
    await screen.findByRole("heading", { level: 1 });
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
  });
});
