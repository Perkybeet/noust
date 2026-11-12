/**
 * The second column of Settings, drawn by the real shell (docs/DESIGN.md, NV-10 and NV-11): since 4.3 the sections are the
 * central's and nothing in the column or in the title says "All the fleet" any more; the group Fleet is gone (its sections
 * are Noust's, or are shown in the section each kind of setting belongs to); a name that does not say what it is for
 * carries a line under it; the four sections that are each server's own open the table of servers, so their link carries
 * no server; and on a server on its own the same sections appear with nothing of a fleet.
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
  // The servers are asked for after the page is drawn: the central's own sections are the sign that they have come.
  if ("GET /api/nodes" in routes) await within(list).findByRole("link", { name: /^Central and sealing/ });
  return { ...harness, list };
}

const FLEET = { "GET /api/nodes": () => json(200, { items: NODES }) };

/** A group of the list, by the start of its heading. */
function group(list: HTMLElement, name: string): HTMLElement {
  return within(list).getByRole("list", { name: new RegExp(`^${name}`) });
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
  it("says nothing of whose a section is: no tag in the column and no line under the title", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    expect(screen.queryByText("All the fleet")).toBeNull();
    expect(screen.queryByText(/it does not depend on the server chosen above/)).toBeNull();
    expect(within(list).queryByText("Fleet")).toBeNull();
  });

  it("has six groups, and the central's sections are with Noust", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    const headings = [...list.querySelectorAll("p")].map((paragraph) => paragraph.textContent);
    expect(headings).toEqual(["People and access", "Notices", "Connections", "Compliance and audit", "API", "Noust"]);
    const noust = within(group(list, "Noust")).getAllByRole("link");
    expect(noust.map((link) => link.textContent)).toEqual(["General", "Console address", "Sign-in image", "Variables", "Central and sealing", "About Noust"]);
  });

  it("leaves out of Settings what is the machine's: the manifest is in Server", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    expect(within(list).queryByRole("link", { name: /^Manifest/ })).toBeNull();
    expect(within(group(list, "API")).getAllByRole("link").map((link) => link.textContent)).toEqual(["API tokens", "API reference", "MCP"]);
  });

  it("opens the table of servers from the sections that are each server's own, never a server's page", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    // A link that carried a server would open that server's own page and not the table.
    for (const name of [/^Integrations/, /^Logs and metrics export/, /^Secret managers/, /^Deploy keys/]) {
      expect(within(list).getByRole("link", { name })).not.toHaveAttribute("href", expect.stringContaining("/n/"));
    }
    expect(within(list).getByRole("link", { name: /^Integrations/ })).toHaveAttribute("href", "/settings/integrations");
  });

  it("puts the line under a name that does not say what it is for, and the people's group under it in order", async () => {
    const { list } = await settingsAt("/settings/accounts", FLEET);
    const people = within(group(list, "People and access")).getAllByRole("link");
    expect(people.map((link) => link.textContent)).toEqual(["Accounts", "Roles", "Single sign-onWith the company account", "Access security", "Approvals"]);
  });
});

describe("Settings, on a server on its own", () => {
  it("says nothing of a fleet where there is none, and keeps the groups and the line under the name", async () => {
    const { list } = await settingsAt("/settings/accounts");
    expect(screen.queryByText("All the fleet")).toBeNull();
    expect(within(list).getByText("With the company account")).toBeInTheDocument();
    // The central's sections are for a console that manages others.
    expect(within(list).queryByRole("link", { name: /^Central and sealing/ })).toBeNull();
  });
});
