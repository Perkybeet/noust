/**
 * The frame of the console at each of its widths, drawn by the real shell (docs/DESIGN.md, NV-1, NV-20):
 * the main bar and the context column from 1024 px (folded to icons below 1440), and a phone's bar at the
 * foot below it; jsdom's own width is 1024 px, and the others are given by resizing the window. There is
 * no other presentation to choose. What a browser alone can say (the slide, the pixels) is
 * e2e/frame.spec.ts's.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { overviewFixture } from "../../features/overview/testFixtures";
import { NODES, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { setPageWidth, setRailFold } from "../preferences";

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

/** The areas of the bar, in the order it draws them, and Settings under them. */
const AREAS = ["Home", "Applications", "Catalog", "Databases", "Scheduled tasks", "Domains", "Network", "Logs", "Alerts", "Activity", "Backups", "Security", "Server", "Settings"];
const TITLES = ["Applications and data", "Network", "Observe", "Protect", "Infrastructure"];

beforeEach(() => {
  window.localStorage.clear();
  act(() => {
    setRailFold("expanded");
    setPageWidth("comfortable");
  });
  window.localStorage.clear();
});

afterEach(() => {
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
});

describe("the main bar", () => {
  it("lists the areas, each group under its title, and Settings under the last", async () => {
    await shellAt("/apps", 1440);
    const nav = main();
    const names = within(nav).getAllByRole("link").map((link) => link.textContent);
    expect(names).toEqual(AREAS);
    // "Network" is a title and an area, so the title is the one drawn as a paragraph.
    for (const title of TITLES) expect(within(nav).getAllByText(title).find((text) => text.tagName === "P")).toBeVisible();
    // A group is a list named by its title, so a screen reader can jump to it.
    expect(within(nav).getByRole("list", { name: "Applications and data" })).toBeInTheDocument();
    expect(within(within(nav).getByRole("list", { name: "Applications and data" })).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Applications",
      "Catalog",
      "Databases",
      "Scheduled tasks",
    ]);
    // Home is alone and Settings is pinned: neither is a group, so neither has a title.
    expect(within(nav).getAllByRole("list").length).toBe(TITLES.length + 2);
  });

  it("keeps the titles for a screen reader when it is folded, and draws only the joints", async () => {
    await shellAt("/apps", 1366);
    const nav = main();
    for (const title of TITLES) {
      const text = within(nav).getAllByText(title).find((candidate) => candidate.tagName === "P");
      expect(text).toHaveClass("sr-only");
    }
    expect(within(nav).getByRole("list", { name: "Network" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Applications" })).toBeInTheDocument();
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

  it("is drawn at every desktop width, with no choice of bar to make", async () => {
    for (const width of [1024, 1279, 1366, 1440, 1920]) {
      Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
      fakeBackend(signedInRoutes());
      const { unmount } = renderConsole("/apps");
      await screen.findByRole("heading", { level: 1 });
      expect(document.querySelector('[data-frame="rail"]'), String(width)).not.toBeNull();
      expect(screen.queryByRole("radiogroup", { name: "Navigation" }), String(width)).toBeNull();
      unmount();
    }
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
    expect(within(nav).getByRole("link", { name: "Server 1 failed" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Databases" })).toBeInTheDocument();
  });

  it("is the same areas on the fleet's pages, and opens an area at its fleet face where it has one", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: NODES }) });
    renderConsole("/fleet/apps");
    await screen.findByRole("heading", { level: 1 });
    const nav = main();
    expect(within(nav).getAllByRole("link")).toHaveLength(AREAS.length);
    expect(within(nav).getByRole("link", { name: "Applications" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Applications" })).toHaveAttribute("href", "/fleet/apps");
    // An area with no page of the fleet of its own is its address under /fleet, not a server's page.
    expect(within(nav).getByRole("link", { name: "Domains" })).toHaveAttribute("href", "/fleet/domains");
    expect(within(nav).getByRole("link", { name: "Databases" })).toHaveAttribute("href", "/fleet/databases");
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

describe("from 1440 px, the main bar and the column both open", () => {
  it("is a bar of areas with their names", async () => {
    await shellAt("/", 1440);
    expect(within(main()).getByRole("link", { name: "Applications" })).toBeVisible();
    expect(document.querySelector('[data-frame="rail"]')).not.toBeNull();
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
    expect(within(settings).getByRole("link", { name: "Notification channels" })).toBeInTheDocument();
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

  it("offers no choice of bar: the account menu has the theme, the language and the width, and nothing about bars", async () => {
    const { user } = await shellAt("/", 1440);
    await user.click(screen.getByRole("button", { name: /Session and preferences/ }));
    await screen.findByText("Page width");
    expect(screen.queryByRole("radiogroup", { name: "Navigation" })).toBeNull();
    expect(screen.queryByText("One bar")).toBeNull();
    expect(screen.queryByText("Double bar")).toBeNull();
  });

  it("has no accessibility violations, the bar and the column included", async () => {
    await shellAt("/apps/shop.example.com/logs", 1440);
    await screen.findByRole("navigation", { name: "Application sections" });
    await expectNoAxeViolations(document.body);
  });
});

describe("from 1024 to 1439 px, the main bar is folded and the column is beside it", () => {
  it("has the column of a resource beside the folded bar at 1024 px, where 4.1 drew one bar", async () => {
    await shellAt("/apps/shop.example.com");
    expect(document.querySelector('[data-frame="rail"]')).not.toBeNull();
    const column = await screen.findByRole("navigation", { name: "Application sections" });
    expect(column.closest('[data-frame="column"]')).not.toBeNull();
    expect(screen.getAllByRole("navigation", { name: "Application sections" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
  });

  it("opens over the page by its button, which then says Close; Escape closes it", async () => {
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

  it("does the same at 1024 px, the narrowest desktop", async () => {
    const { user } = await shellAt("/", 1024);
    await user.click(screen.getByRole("button", { name: "Unfold the bar" }));
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(within(main()).getAllByText("Network").find((text) => text.tagName === "P")).not.toHaveClass("sr-only");
    await user.keyboard("{Escape}");
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
    // Below 1440 it is folded whatever was saved, and its button opens it over the page.
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
    act(() => {
      setRailFold("expanded");
    });
    // A saved "unfolded" has no room here: it stays folded.
    expect(screen.getByRole("button", { name: "Unfold the bar" })).toBeInTheDocument();
    resizeTo(1024);
    expect(document.querySelector('[data-frame="rail"]')).not.toBeNull();
    resizeTo(1023);
    await waitFor(() => {
      expect(document.querySelector('[data-frame="rail"]')).toBeNull();
    });
  });
});

describe("a phone", () => {
  it("has four areas and More at the foot, and every area, in its group, in the sheet More opens", async () => {
    const { user } = await shellAt("/", 390);
    const foot = screen.getByRole("navigation", { name: "Areas" });
    expect(within(foot).getAllByRole("link").map((link) => link.textContent)).toEqual(["Home", "Applications", "Databases", "Activity"]);
    expect(document.querySelector('[data-frame="rail"]')).toBeNull();
    await user.click(within(foot).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "All areas" });
    for (const title of TITLES) expect(within(sheet).getAllByText(title).find((text) => text.tagName === "P")).toBeVisible();
    expect(within(sheet).getByRole("link", { name: "Backups" })).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Catalog" })).toBeInTheDocument();
    expect(within(sheet).getByRole("link", { name: "Settings" })).toBeInTheDocument();
    // The account moved here from the top bar.
    expect(within(sheet).getByRole("button", { name: "Your account" })).toBeInTheDocument();
  });

  it("draws the server selector and no account button in the top bar", async () => {
    await shellAt("/", 390);
    const bar = screen.getAllByRole("banner")[0] ?? document.body;
    expect(within(bar).queryByRole("button", { name: /Session and preferences/ })).toBeNull();
    expect(within(bar).getAllByRole("button", { name: "Search" }).length).toBeGreaterThan(0);
  });

  it("opens the session from the sheet, with the same body as the top bar's popover", async () => {
    const { user } = await shellAt("/", 390);
    await user.click(within(screen.getByRole("navigation", { name: "Areas" })).getByRole("button", { name: "More" }));
    const sheet = await screen.findByRole("dialog", { name: "All areas" });
    await user.click(within(sheet).getByRole("button", { name: "Your account" }));
    const session = await screen.findByRole("dialog", { name: "Session" });
    expect(within(session).getByRole("link", { name: "Account" })).toBeInTheDocument();
    expect(within(session).getByRole("button", { name: "Sign out" })).toBeInTheDocument();
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
