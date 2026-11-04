/**
 * The context column of the frame, drawn by the real shell (docs/DESIGN.md, NV-22 to NV-24): every area has one,
 * a resource's replaces the area's with the way back and the switch to its siblings, the fleet's faces have theirs,
 * and a phone draws the same list behind a selector at the top of the page, never as tabs. The derivations are
 * columns.test.ts's; what is here is what the shell makes of them.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { NODES, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { setPageWidth, setRailFold } from "../preferences";

async function shellAt(path: string, width = 1440, routes: Record<string, RouteHandler> = {}) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  fakeBackend({ ...signedInRoutes(), ...routes });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return harness;
}

/** The column a list of its sections is drawn in. */
function columnOf(nav: HTMLElement): HTMLElement {
  const column = nav.closest<HTMLElement>('[data-frame="column"]');
  if (column === null) {
    const chain: string[] = [];
    for (let at: HTMLElement | null = nav; at !== null; at = at.parentElement) chain.push(`${at.tagName.toLowerCase()}[${at.getAttribute("data-frame") ?? at.getAttribute("data-layer") ?? at.getAttribute("aria-label") ?? ""}]`);
    throw new Error(`the sections are not in a column: ${chain.join(" < ")}`);
  }
  return column;
}

/** The names of a column's links, without the count that follows a name (its own test is the counts'). */
const links = (nav: HTMLElement) => within(nav).getAllByRole("link").map((link) => link.textContent.replace(/\d+$/, ""));

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

describe("every area has a column", () => {
  it("is there on Home, where the panels are, and the gallery of widgets takes its place while one is customised", async () => {
    await shellAt("/");
    const column = document.querySelector('[data-frame="column"]');
    expect(column).not.toBeNull();
    expect(column).toHaveAttribute("data-column", "overview");
    expect(column).toHaveAccessibleName("Home");
    // Home draws its own list (features/overview/HomeColumn): the panels, the one that comes with the console open.
    const panels = await screen.findByRole("navigation", { name: "Panels" });
    expect(columnOf(panels)).toBe(column);
    expect(within(panels).getByRole("link", { name: /This server/ })).toHaveAttribute("aria-current", "page");
    expect(within(panels).getByRole("link", { name: "New panel" })).toBeVisible();
  });

  it("has Home's column on the fleet's face of Home too, and no tab strip in the page of either", async () => {
    await shellAt("/fleet", 1440, { "GET /api/nodes": () => json(200, { items: NODES }) });
    const column = document.querySelector('[data-frame="column"]');
    expect(column).toHaveAttribute("data-column", "overview");
    await screen.findByRole("navigation", { name: "Panels" });
    expect(within(screen.getByRole("main")).queryByRole("navigation", { name: /sections/ })).toBeNull();
  });

  it("slides to the gallery while a panel is customised, and back to the panels when it is not", async () => {
    await shellAt("/?customise=true");
    await waitFor(() => {
      expect(document.querySelector('[data-frame="column"]')?.textContent).toContain("Add widgets");
    });
    expect(screen.queryByRole("navigation", { name: "Panels" })).toBeNull();
  });

  it("lists Network's sections in the groups the register gives, the first of them being the area's own page", async () => {
    await shellAt("/network");
    const sections = await screen.findByRole("navigation", { name: "Network sections" });
    expect(links(sections)).toEqual(["Published ports", "Tunnels", "Firewall", "Listening ports", "Interfaces"]);
    expect(within(sections).getByText("Publish")).toBeInTheDocument();
    expect(within(sections).getByText("This machine")).toBeInTheDocument();
    expect(within(sections).getByRole("link", { name: /^Published ports/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getAllByRole("navigation", { name: "Network sections" })).toHaveLength(1);
  });

  it("keeps the column when a section is chosen, and the area stays current in the bar", async () => {
    const { user } = await shellAt("/network");
    const sections = await screen.findByRole("navigation", { name: "Network sections" });
    await user.click(within(sections).getByRole("link", { name: "Firewall" }));
    await screen.findByRole("heading", { level: 1, name: "Firewall" });
    expect(within(screen.getByRole("navigation", { name: "Network sections" })).getByRole("link", { name: "Firewall" })).toHaveAttribute("aria-current", "page");
    expect(within(screen.getByRole("navigation", { name: "Main" })).getByRole("link", { name: "Network" })).toHaveAttribute("aria-current", "page");
    expect(document.querySelectorAll('[data-frame="column"]')).toHaveLength(1);
  });

  it("lists the fixed views of an area that is one page, and marks the one the address chose", async () => {
    await shellAt("/logs?source=system");
    const sections = await screen.findByRole("navigation", { name: "Logs sections" });
    expect(links(sections)).toEqual(["Applications", "System"]);
    expect(within(sections).getByRole("link", { name: "System" })).toHaveAttribute("aria-current", "page");
    expect(within(sections).getByRole("link", { name: "Applications" })).not.toHaveAttribute("aria-current");
    expect(within(sections).getByRole("link", { name: "System" })).toHaveAttribute("href", expect.stringContaining("source=system"));
  });

  it("lists the machine's sections, Security's in its own column and the Server's in its own", async () => {
    await shellAt("/security");
    expect(links(await screen.findByRole("navigation", { name: "Security sections" }))).toEqual(["Checks", "SSH", "Brute force"]);
  });

  it("has Databases' own column with the engines, and Settings' with its groups, as before", async () => {
    await shellAt("/databases");
    expect(await screen.findByRole("navigation", { name: "Databases" })).toBeInTheDocument();
  });
});

describe("entering a resource", () => {
  it("replaces the area's list with the application's sections, the way back to the area and the switch to the others", async () => {
    await shellAt("/apps/shop.example.com");
    const sections = await screen.findByRole("navigation", { name: "Application sections" });
    const column = sections.closest('[data-frame="column"]');
    if (column === null) throw new Error("no column");
    expect(within(column as HTMLElement).getByRole("link", { name: "Applications" })).toHaveAttribute("href", "/apps");
    expect(within(column as HTMLElement).getByRole("button", { name: "Switch application" })).toBeInTheDocument();
    expect(column).toHaveAttribute("data-column", "app");
    expect(screen.queryByRole("navigation", { name: "Applications sections" })).toBeNull();
  });

  it("jumps to another application in the same section from the switch, without going back to the list", async () => {
    const { user, location } = await shellAt("/apps/shop.example.com/logs");
    const column = columnOf(await screen.findByRole("navigation", { name: "Application sections" }));
    await user.click(within(column).getByRole("button", { name: "Switch application" }));
    await user.click(await screen.findByRole("menuitem", { name: "admin.example.com" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/apps/admin.example.com/logs");
    });
  });

  it("slides back to the area's list from the way back, and the column is the area's again", async () => {
    const { user, location } = await shellAt("/apps/shop.example.com");
    const column = columnOf(await screen.findByRole("navigation", { name: "Application sections" }));
    await user.click(within(column).getByRole("link", { name: "Applications" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/apps");
    });
    expect(await screen.findByRole("navigation", { name: "Applications sections" })).toBeInTheDocument();
  });

  it("has no strip of sections in the page, whichever resource it is", async () => {
    await shellAt("/apps/shop.example.com/deployments");
    await screen.findByRole("navigation", { name: "Application sections" });
    expect(screen.getAllByRole("navigation", { name: "Application sections" })).toHaveLength(1);
  });

  it("has a server's sections with the way back to the list on a fleet, and the area's own head on a lone server", async () => {
    await shellAt("/n/web-2/server", 1440, { "GET /api/nodes": () => json(200, { items: NODES }) });
    const sections = await screen.findByRole("navigation", { name: "Server sections" });
    const column = columnOf(sections);
    expect(within(column).getByRole("link", { name: "All servers" })).toHaveAttribute("href", "/fleet/servers");
    expect(within(column).getByRole("button", { name: "Switch server" })).toBeInTheDocument();
    expect(within(sections).getByText("This machine")).toBeInTheDocument();
    expect(within(sections).getByText("What runs")).toBeInTheDocument();
  });
});

describe("the fleet's pages", () => {
  it("have their area's column, with the faces of that area", async () => {
    await shellAt("/fleet/apps", 1440, { "GET /api/nodes": () => json(200, { items: NODES }) });
    const sections = await screen.findByRole("navigation", { name: "Applications sections" });
    expect(links(sections)).toEqual(["Applications", "On several servers"]);
    expect(within(sections).getByRole("link", { name: "Applications" })).toHaveAttribute("href", "/fleet/apps");
  });

  it("list the servers beside the faces of Server, to choose one", async () => {
    await shellAt("/fleet/servers", 1440, { "GET /api/nodes": () => json(200, { items: NODES }) });
    const sections = await screen.findByRole("navigation", { name: "Server sections" });
    expect(within(sections).getByRole("link", { name: "Servers" })).toHaveAttribute("href", "/fleet/servers");
    expect(within(sections).getByRole("link", { name: "web-2" })).toHaveAttribute("href", "/n/web-2/server");
  });
});

describe("a phone", () => {
  it("draws the column as a selector at the top of the page that says where the page is, and no strip of tabs", async () => {
    await shellAt("/apps/shop.example.com/deployments", 390);
    const selector = within(screen.getByRole("main")).getByRole("button", { name: /Deployments/ });
    expect(selector).toHaveTextContent("shop.example.com");
    expect(selector).toHaveAttribute("aria-haspopup", "dialog");
    expect(screen.queryByRole("navigation", { name: "Application sections" })).toBeNull();
  });

  it("opens the same list in a sheet, which closes when a section is chosen", async () => {
    const { user, location } = await shellAt("/apps/shop.example.com/deployments", 390);
    await user.click(within(screen.getByRole("main")).getByRole("button", { name: /Deployments/ }));
    const sheet = await screen.findByRole("dialog");
    const sections = within(sheet).getByRole("navigation", { name: "Application sections" });
    for (const group of ["Use", "Configure", "Fix", "Move elsewhere", "Danger"]) expect(within(sections).getByText(group)).toBeInTheDocument();
    expect(within(sections).getByRole("link", { name: "Deployments" })).toHaveAttribute("aria-current", "page");
    await expectNoAxeViolations(sheet);
    await user.click(within(sections).getByRole("link", { name: "Logs" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/apps/shop.example.com/logs");
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).toBeNull();
    });
  });

  it("is there on an area that is one page, naming the view the address chose", async () => {
    await shellAt("/logs?source=system", 390);
    expect(within(screen.getByRole("main")).getByRole("button", { name: /System/ })).toBeInTheDocument();
  });

  it("has no column at its side: the page has the whole width", async () => {
    await shellAt("/apps", 390);
    expect(document.querySelector('[data-frame="column"]')).toBeNull();
  });
});
