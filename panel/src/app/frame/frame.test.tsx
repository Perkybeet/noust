/**
 * The frame of the console in each of its presentations, drawn by the real shell (docs/DESIGN.md, NV-1,
 * NV-20): one bar at the window's own width in jsdom (1024 px), and the double bar and the phone's bar by
 * giving the window another width. What a browser alone can say (the slide, the pixels) is
 * e2e/frame.spec.ts's.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { overviewFixture } from "../../features/overview/testFixtures";
import { NODES, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { setNavPresentation, setPageWidth, setRailFold } from "../preferences";

function resizeTo(width: number): void {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  act(() => {
    window.dispatchEvent(new Event("resize"));
  });
}

async function shellAt(path: string, width = 1024) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  fakeBackend(signedInRoutes());
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return harness;
}

const main = () => screen.getByRole("navigation", { name: "Main" });

beforeEach(() => {
  window.localStorage.clear();
  act(() => {
    setNavPresentation("double");
    setRailFold("expanded");
    setPageWidth("comfortable");
  });
  window.localStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
});

describe("one bar (below 1280 px)", () => {
  it("lists the twelve areas, each group under its title, and Settings under the last", async () => {
    await shellAt("/apps");
    const nav = main();
    const names = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(names).toEqual(["Home", "Applications", "Databases", "Scheduled tasks", "Domains", "Logs", "Alerts", "Activity", "Backups", "Servers", "Catalog", "Settings"]);
    for (const title of ["What runs", "How it is reached", "What is happening", "Kept safe", "Where it runs", "How it is managed"]) expect(within(nav).getByText(title)).toBeInTheDocument();
    // A group is a list named by its title.
    expect(within(nav).getByRole("list", { name: "What runs" })).toBeInTheDocument();
  });

  it("marks the current area by aria-current alone: no bar of colour, and only one item", async () => {
    await shellAt("/apps");
    const current = within(main()).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual(["Applications"]);
    expect(document.querySelector("[data-nav-indicator]")).toBeNull();
  });

  it("slides one raised piece, which is decoration for a screen reader", async () => {
    await shellAt("/apps");
    const pieces = main().querySelectorAll('[data-slot="sliding-indicator"]');
    expect(pieces).toHaveLength(1);
    expect(pieces[0]).toHaveAttribute("aria-hidden", "true");
    await expectNoAxeViolations(main());
  });

  it("keeps an area current beneath its own pages, and moves with the page", async () => {
    const { user } = await shellAt("/catalog");
    expect(within(main()).getByRole("link", { name: "Catalog" })).toHaveAttribute("aria-current", "page");
    expect(within(main()).getByRole("link", { name: "Applications" })).not.toHaveAttribute("aria-current");
    await user.click(within(main()).getByRole("link", { name: "Databases" }));
    await screen.findByRole("heading", { level: 1, name: "Databases" });
    expect(within(main()).getByRole("link", { name: "Databases" })).toHaveAttribute("aria-current", "page");
    expect(within(main()).getByRole("link", { name: "Catalog" })).not.toHaveAttribute("aria-current");
  });

  it("keeps Home lit on the Map, which is its second view, and nothing else", async () => {
    await shellAt("/map");
    const current = within(main()).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map((link) => link.textContent)).toEqual(["Home"]);
  });

  it("has no second column, and says in the page where a resource's sections are", async () => {
    await shellAt("/apps/shop.example.com");
    expect(document.querySelector('[data-frame="column"]')).toBeNull();
    expect(await screen.findByRole("navigation", { name: "Application sections" })).toBeInTheDocument();
  });

  it("draws no choice of bar below 1280 px: two columns do not fit", async () => {
    await shellAt("/apps");
    expect(screen.queryByRole("radiogroup", { name: "Navigation" })).toBeNull();
  });

  it("counts what failed beside the area it is in, red for what failed and amber for what is about to", async () => {
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
    expect(within(nav).getByRole("link", { name: "Servers 1 failed" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Databases" })).toBeInTheDocument();
  });

  it("is the same twelve areas on the fleet's pages, and opens an area at its fleet face where it has one", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/fleet/apps");
    await screen.findByRole("heading", { level: 1 });
    const nav = main();
    expect(within(nav).getAllByRole("link")).toHaveLength(12);
    expect(within(nav).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Applications" })).toHaveAttribute("href", "/fleet/apps");
    expect(within(nav).getByRole("link", { name: "Domains" })).toHaveAttribute("href", "/fleet/certificates");
    expect(within(nav).getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
    expect(within(nav).getByRole("link", { name: "Home" })).toHaveAttribute("href", "/fleet");
  });

  it("lights Applications of the fleet on the applications that run on several servers", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/fleet/placements");
    await screen.findByRole("heading", { level: 1 });
    expect(within(main()).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
  });

  it("keeps a lone server's bar on its account, which is the central's page: no area is current", async () => {
    await shellAt("/account");
    expect(within(main()).getByRole("link", { name: "Databases" })).toBeInTheDocument();
    expect(within(main()).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page")).toEqual([]);
  });
});

describe("the double bar (1440 px and wider)", () => {
  it("is a bar of areas with their names, and Home's panels in the second column", async () => {
    await shellAt("/", 1440);
    expect(within(main()).getByRole("link", { name: "Applications" })).toBeVisible();
    expect(document.querySelector('[data-frame="rail"]')).not.toBeNull();
    const column = await screen.findByRole("navigation", { name: "Panels" });
    expect(column.closest('[data-frame="column"]')).not.toBeNull();
    expect(within(column).getByRole("link", { name: /This server/ })).toHaveAttribute("aria-current", "page");
    expect(within(column).getByRole("link", { name: "New panel" })).toBeVisible();
    // The names are text, not tooltips: the bar is unfolded.
    expect(within(main()).getByText("Applications")).not.toHaveClass("sr-only");
  });

  it("has the second column of an application, grouped by what its sections are for, and no strip in the page", async () => {
    await shellAt("/apps/shop.example.com/deployments", 1440);
    const column = await screen.findByRole("navigation", { name: "Application sections" });
    expect(column.closest('[data-frame="column"]')).not.toBeNull();
    for (const group of ["Use", "Configure", "Fix", "Danger"]) expect(within(column).getByText(group)).toBeInTheDocument();
    expect(within(column).getByRole("link", { name: "Deployments" })).toHaveAttribute("aria-current", "page");
    expect(within(column).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page")).toHaveLength(1);
    // The sections are in the column: the page does not draw them a second time.
    expect(screen.getAllByRole("navigation", { name: "Application sections" })).toHaveLength(1);
    // The area stays current in the bar beside it.
    expect(within(main()).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
  });

  it("says where the page is in the top bar: the steps above it are links and only the page's own is current", async () => {
    await shellAt("/apps/shop.example.com/deployments", 1440);
    const crumbs = within(screen.getAllByRole("banner")[0] ?? document.body).getByRole("navigation", { name: "Breadcrumb" });
    const steps = within(crumbs).getAllByRole("listitem").map((item) => item.textContent);
    expect(steps).toEqual(["Applications", "shop.example.com", "Deployments"]);
    // A step above the page is where the page is under, never the page: the prefix match of a link marks nothing.
    const current = crumbs.querySelectorAll('[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveTextContent("Deployments");
    expect(within(crumbs).getByRole("link", { name: "shop.example.com" })).toHaveAttribute("translate", "no");
  });

  it("gives the column and the bar a raised piece each", async () => {
    await shellAt("/apps/shop.example.com", 1440);
    const column = await screen.findByRole("navigation", { name: "Application sections" });
    expect(main().querySelectorAll('[data-slot="sliding-indicator"]')).toHaveLength(1);
    expect(column.querySelectorAll('[data-slot="sliding-indicator"]')).toHaveLength(1);
  });

  it("has the server's sections in a column of its own, and Settings' by whose they are", async () => {
    await shellAt("/server", 1440);
    const server = await screen.findByRole("navigation", { name: "Server sections" });
    expect(within(server).getByRole("link", { name: /^Status/ })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("navigation", { name: "Servers sections" })).toBeNull();
  });

  it("has Settings' groups as the column, and not again in the page", async () => {
    await shellAt("/settings/tokens", 1440);
    const settings = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(settings.closest('[data-frame="column"]')).not.toBeNull();
    expect(within(settings).getByRole("link", { name: "API tokens" })).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("navigation", { name: "Settings sections" })).toHaveLength(1);
  });

  it("narrows Settings' column by a section's name and by the name it had before, and says when none answers", async () => {
    const { user } = await shellAt("/settings/tokens", 1440);
    const settings = await screen.findByRole("navigation", { name: "Settings sections" });
    const filter = screen.getByRole("searchbox", { name: "Filter sections" });
    // Only the column has the box: the page beside it does not draw the list again.
    expect(screen.getAllByRole("searchbox", { name: "Filter sections" })).toHaveLength(1);
    await user.type(filter, "notifications");
    expect(within(settings).getAllByRole("link")).toHaveLength(1);
    expect(within(settings).getByRole("link", { name: "Channels" })).toBeInTheDocument();
    await user.clear(filter);
    await user.type(filter, "zzzz");
    expect(within(settings).queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("No section matches this filter.")).toBeInTheDocument();
    await user.clear(filter);
    expect(within(settings).getByRole("link", { name: "API tokens" })).toHaveAttribute("aria-current", "page");
  });

  it("folds to icons by a button that says what it does and what it is, and keeps the choice", async () => {
    const { user } = await shellAt("/", 1440);
    const fold = screen.getByRole("button", { name: "Fold the bar" });
    expect(fold).toHaveAttribute("aria-expanded", "true");
    await user.click(fold);
    const unfold = screen.getByRole("button", { name: "Unfold the bar" });
    expect(unfold).toHaveAttribute("aria-expanded", "false");
    // Folded, an icon is a link that still has its name.
    expect(within(main()).getByRole("link", { name: "Applications" })).toBeInTheDocument();
    expect(window.localStorage.getItem("noust.rail")).toBe("folded");
    await user.click(unfold);
    expect(screen.getByRole("button", { name: "Fold the bar" })).toBeInTheDocument();
    expect(window.localStorage.getItem("noust.rail")).toBeNull();
  });

  it("offers the choice of bar, drawn and named, and one bar is what it says", async () => {
    const { user } = await shellAt("/", 1440);
    const choice = screen.getByRole("radiogroup", { name: "Navigation" });
    expect(within(choice).getByRole("radio", { name: "Double bar" })).toBeChecked();
    await user.click(within(choice).getByRole("radio", { name: "One bar" }));
    await waitFor(() => {
      expect(document.querySelector('[data-frame="rail"]')).toBeNull();
    });
    expect(within(main()).getByText("What runs")).toBeInTheDocument();
    expect(window.localStorage.getItem("noust.nav")).toBe("single");
  });

  it("has no accessibility violations, the bar and the column included", async () => {
    await shellAt("/apps/shop.example.com/logs", 1440);
    await screen.findByRole("navigation", { name: "Application sections" });
    await expectNoAxeViolations(document.body);
  });
});

describe("the double bar between 1280 and 1439 px", () => {
  it("is folded, and unfolding opens it over the page: the button says Close, Escape closes it", async () => {
    const { user } = await shellAt("/", 1366);
    const open = screen.getByRole("button", { name: "Unfold the bar" });
    expect(open).toHaveAttribute("aria-expanded", "false");
    await user.click(open);
    const close = screen.getByRole("button", { name: "Close" });
    expect(close).toHaveAttribute("aria-expanded", "true");
    expect(document.querySelector('[data-frame="veil"]')).not.toBeNull();
    // Nothing is saved by opening it over the page: it was never unfolded.
    expect(window.localStorage.getItem("noust.rail")).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
    expect(document.querySelector('[data-frame="veil"]')).toBeNull();
  });

  it("closes over a choice: going to an area puts the bar back", async () => {
    const { user } = await shellAt("/", 1366);
    await user.click(screen.getByRole("button", { name: "Unfold the bar" }));
    await user.click(within(main()).getByRole("link", { name: "Databases" }));
    await screen.findByRole("heading", { level: 1, name: "Databases" });
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
  });

  it("closes by pressing the veil", async () => {
    const { user } = await shellAt("/", 1366);
    await user.click(screen.getByRole("button", { name: "Unfold the bar" }));
    const veil = document.querySelector<HTMLElement>('[data-frame="veil"]');
    if (veil === null) throw new Error("no veil");
    await user.click(veil);
    expect(document.querySelector('[data-frame="veil"]')).toBeNull();
  });

  it("follows the window across 1440: the choice saved there is what is drawn above it", async () => {
    await shellAt("/", 1440);
    act(() => {
      setRailFold("folded");
    });
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
    resizeTo(1366);
    // Between 1280 and 1439 px it is folded whatever was saved, and its button opens it over the page.
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
    resizeTo(1279);
    await waitFor(() => {
      expect(document.querySelector('[data-frame="rail"]')).toBeNull();
    });
    expect(within(main()).getByText("What runs")).toBeInTheDocument();
  });
});

describe("a phone", () => {
  it("has four areas and More at the foot, and the rest of the areas in the menu", async () => {
    const { user } = await shellAt("/", 390);
    const foot = screen.getByRole("navigation", { name: "Areas" });
    expect(within(foot).getAllByRole("link").map((link) => link.textContent)).toEqual(["Home", "Applications", "Databases", "Activity"]);
    expect(document.querySelector('[data-frame="rail"]')).toBeNull();
    await user.click(within(foot).getByRole("button", { name: "More" }));
    const dialog = await screen.findByRole("dialog", { name: "Menu" });
    expect(within(dialog).getByRole("link", { name: "Backups" })).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("marks the current area at the foot too", async () => {
    await shellAt("/apps", 390);
    const foot = screen.getByRole("navigation", { name: "Areas" });
    expect(within(foot).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
  });
});

describe("the width of the page", () => {
  it("is capped unless the person chose the whole width", async () => {
    await shellAt("/apps", 1440);
    const page = () => screen.getByRole("main").firstElementChild;
    expect(page()).toHaveClass("max-w-page");
    act(() => {
      setPageWidth("full");
    });
    expect(page()).toHaveClass("max-w-none");
    expect(page()).not.toHaveClass("max-w-page");
  });
});
