/**
 * The second column of Settings, drawn by the real shell (docs/DESIGN.md, NV-10 and NV-11): on a fleet, "All the fleet" is
 * said once over a group in which every section is the fleet's, and on the row of the ones that are in a group that is not;
 * what a server shows before anyone signs in is its own and is never the fleet's; a name that does not say what it is for
 * carries a line under it; and on a server on its own nothing says "fleet" at all.
 */

import { act, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { NODES, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { setPageWidth, setRailFold } from "../preferences";

async function settingsAt(path: string, routes: Record<string, RouteHandler> = {}) {
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  fakeBackend({ ...signedInRoutes(), ...routes });
  const harness = renderConsole(path);
  const list = await screen.findByRole("navigation", { name: "Settings sections" });
  // The servers are asked for after the page is drawn: the group that is for a fleet is the sign that they have come.
  if ("GET /api/nodes" in routes) await within(list).findByRole("list", { name: /^Fleet/ });
  return { ...harness, list };
}

const FLEET = { "GET /api/nodes": () => json(200, { items: NODES }) };

/** A group of the list, by the start of its heading: the tag is a part of the heading, so "Fleet" is not "All the fleet". */
function group(list: HTMLElement, name: string): HTMLElement {
  return within(list).getByRole("list", { name: new RegExp(`^${name}`) });
}

/** The heading of a group, the paragraph the list is named by. */
function heading(list: HTMLElement, name: string): HTMLElement {
  const found = [...list.querySelectorAll("p")].find((paragraph) => paragraph.textContent.startsWith(name));
  if (found === undefined) throw new Error(`no heading for ${name}`);
  return found;
}

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

describe("Settings, on a fleet", () => {
  it("says All the fleet once over a group that is all of it, and not on its rows", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    for (const name of ["People and access", "Compliance and audit", "Fleet"]) {
      expect(within(heading(list, name)).getByText("All the fleet")).toBeInTheDocument();
      expect(within(group(list, name)).queryByText("All the fleet")).toBeNull();
    }
  });

  it("says it on the row of the sections that are the fleet's, in a group that mixes", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    // Connections holds the cloud providers, which are the fleet's, beside what is a server's.
    expect(within(heading(list, "Connections")).queryByText("All the fleet")).toBeNull();
    expect(within(within(group(list, "Connections")).getByRole("link", { name: /^Cloud providers/ })).getByText("All the fleet")).toBeInTheDocument();
    expect(within(within(group(list, "Connections")).getByRole("link", { name: /^Image registries/ })).queryByText("All the fleet")).toBeNull();
    // So does API: the tokens are the fleet's, the reference is not.
    expect(within(heading(list, "API")).queryByText("All the fleet")).toBeNull();
    expect(within(within(group(list, "API")).getByRole("link", { name: /^API tokens/ })).getByText("All the fleet")).toBeInTheDocument();
    expect(within(within(group(list, "API")).getByRole("link", { name: /^API reference/ })).queryByText("All the fleet")).toBeNull();
  });

  it("never says it of what a server shows before anyone signs in, which is its own, nor of Noust's own", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    const noust = group(list, "Noust");
    expect(within(heading(list, "Noust")).queryByText("All the fleet")).toBeNull();
    expect(within(noust).queryByText("All the fleet")).toBeNull();
    // It is the central's page whichever server is chosen, all the same: the section is where it was.
    expect(within(noust).getByRole("link", { name: /^Sign-in image/ })).toHaveAttribute("href", "/settings/sign-in-image");
  });

  it("puts the line under a name that does not say what it is for, and the people's group under it in order", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    const people = within(group(list, "People and access")).getAllByRole("link");
    expect(people.map((link) => link.textContent.replace(/All the fleet$/, ""))).toEqual([
      "Accounts",
      "Roles",
      "Single sign-onWith the company account",
      "Access security",
      "Approvals",
    ]);
  });

  it("says on the page's header, on the title's line, that the page holds for every server, and for how many", async () => {
    await settingsAt("/settings/accounts", FLEET);
    const title = screen.getByRole("heading", { level: 1, name: "Accounts" });
    const header = title.closest("header");
    if (header === null) throw new Error("no page header");
    expect(within(header).getByText("All the fleet")).toBeInTheDocument();
    expect(title.parentElement).toContainElement(within(header).getByText("All the fleet"));
    expect(within(header).getByText(/^Holds for the \d+ servers; it does not depend on the server chosen above$/)).toBeInTheDocument();
  });

  it("does not say it on the page of what is a server's own, or of the sign-in picture", async () => {
    await settingsAt("/settings/sign-in-image", FLEET);
    await screen.findByRole("heading", { level: 1, name: "Sign-in image" });
    expect(screen.queryByText(/it does not depend on the server chosen above/)).toBeNull();
    const header = screen.getByRole("heading", { level: 1 }).closest("header");
    if (header === null) throw new Error("no page header");
    expect(within(header).queryByText("All the fleet")).toBeNull();
  });
});

describe("Settings, on a server on its own", () => {
  it("says nothing of a fleet where there is none, and keeps the groups and the line under the name", async () => {
    const { list } = await settingsAt("/settings/accounts");
    expect(screen.queryByText("All the fleet")).toBeNull();
    expect(screen.queryByText(/it does not depend on the server chosen above/)).toBeNull();
    expect(within(list).getByText("With the company account")).toBeInTheDocument();
    // Fleet is for a console that manages others.
    expect([...list.querySelectorAll("p")].some((paragraph) => paragraph.textContent.startsWith("Fleet"))).toBe(false);
  });
});
