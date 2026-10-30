import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderConsole } from "../test/console";
import { fakeBackend, json } from "../test/fakes";
import { centralSession, fleetRoutes } from "../features/fleet/testFixtures";

const NOW = "2026-09-29T08:00:00Z";

function search(items: readonly unknown[]) {
  return {
    resource: "search",
    query: "api",
    generated_at: NOW,
    partial: false,
    fleet: true,
    servers: [],
    nodes: [],
    refusals: [],
    total: items.length,
    truncated: false,
    items,
  };
}

const HIT = { kind: "app", name: "ledger.example.com", node: "web-2", local: false, page: "/apps/ledger.example.com", href: "/n/web-2/apps/ledger.example.com", detail: "Python", labels: {} };

describe("the palette on a fleet", () => {
  it("asks the central for what the words match on every server, and opens the thing on its own server", async () => {
    const backend = fakeBackend({ ...fleetRoutes(centralSession()), "GET /api/views/search": () => json(200, search([HIT])) });
    const { user, location } = renderConsole("/fleet");
    await screen.findByRole("heading", { level: 1, name: "Home" });
    await user.keyboard("{Control>}k{/Control}");
    await user.type(await screen.findByRole("combobox"), "ledger");
    // Found by the central, on another server, named beside its name.
    const option = await screen.findByRole("option", { name: /ledger\.example\.com/ });
    expect(within(option).getByText("web-2, Python")).toBeInTheDocument();
    expect(backend.callsTo("GET /api/views/search")[0]?.search.get("q")).toBe("ledger");
    await user.click(option);
    await waitFor(() => {
      expect(location().pathname).toBe("/apps/ledger.example.com");
    });
    expect(location().search).toMatchObject({ node: "web-2" });
  });

  it("asks nothing for a single letter, or for a mode's prefix: not even a question is put off for later", async () => {
    const backend = fakeBackend({ ...fleetRoutes(centralSession()), "GET /api/views/search": () => json(200, search([])) });
    // The pause before a question is a timer: none of that length is ever set for these, which says nothing will be asked
    // without waiting for any time to pass.
    const pauses: unknown[] = [];
    const realSet = window.setTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation(((handler: TimerHandler, delay?: number, ...args: unknown[]) => {
      if (delay === 250) pauses.push(handler);
      return realSet(handler, delay, ...args);
    }) as typeof window.setTimeout);
    const { user } = renderConsole("/fleet");
    await screen.findByRole("heading", { level: 1, name: "Home" });
    await user.keyboard("{Control>}k{/Control}");
    const field = await screen.findByRole("combobox");
    await user.type(field, "a");
    await user.clear(field);
    await user.type(field, ">restart");
    expect(await screen.findByRole("combobox")).toHaveValue(">restart");
    expect(pauses).toHaveLength(0);
    expect(backend.callsTo("GET /api/views/search")).toHaveLength(0);
  });
});
