import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DESTS, INTERIM, REDIRECTS } from "../app/nav";
import { teamsRoutes } from "../features/teams/testFixtures";
import { renderConsole } from "../test/console";
import { fakeBackend, signedInRoutes } from "../test/fakes";

/**
 * No URL loses its screen (docs/DESIGN.md, NV-18). The table of app/nav.ts is walked through the
 * real router: an old address that is `live` lands on its new place with what it carried, one
 * that is `pending` still opens its own page because the screen at its destination does not hold
 * everything yet, and a new route whose page does not exist opens the screen that holds the
 * same thing today. `e2e/redirects.spec.ts` walks the same tables in a browser.
 */

const SAMPLE: Readonly<Record<string, string>> = { $domain: "shop.example.com", $name: "wasm-shop" };

/** An address that opens a route: its parameters filled in. */
const address = (pattern: string): string => pattern.replace(/\$\w+/g, (param) => SAMPLE[param] ?? "sample");

function open(path: string) {
  fakeBackend({ ...signedInRoutes(), ...teamsRoutes() });
  return renderConsole(path);
}

const LIVE = REDIRECTS.filter((row) => row.state === "live");
const PENDING = REDIRECTS.filter((row) => row.state === "pending");

describe("the table of redirects", () => {
  it("sends every address to a screen of the register", () => {
    const screens = new Set<string>(DESTS.map((entry) => entry.to));
    for (const row of [...REDIRECTS, ...INTERIM]) expect(screens.has(row.to), `${row.from} goes to ${row.to}, which is no screen`).toBe(true);
  });

  it("does not send an address to one that redirects again, nor round in a circle", () => {
    const steps = new Map<string, string>([...LIVE, ...INTERIM].map((row) => [row.from, row.to]));
    for (const start of steps.keys()) {
      const seen = new Set<string>([start]);
      for (let at = steps.get(start); at !== undefined; at = steps.get(at)) {
        expect(seen.has(at), `${start} goes round in a circle through ${at}`).toBe(false);
        seen.add(at);
      }
      expect(seen.size, `${start} lands on a page that redirects again`).toBeLessThanOrEqual(2);
    }
  });

  it("says what a redirect waits for, and gives a reason only to those that wait", () => {
    for (const row of PENDING) expect(row.after?.trim() ?? "", `${row.from} waits for nothing`).not.toBe("");
    for (const row of LIVE) expect(row.after, `${row.from} is live and still says it waits`).toBeUndefined();
  });

  it("starts from a route the register or the router knows", () => {
    const screens = new Set<string>(DESTS.map((entry) => entry.to));
    for (const row of PENDING) expect(screens.has(row.from), `${row.from} is pending, so it is still a screen of its own`).toBe(true);
  });
});

describe("an old address that is live", () => {
  it.each(LIVE.map((row) => [row.from, row] as const))("%s opens its new place", async (_from, row) => {
    const { location } = open(`${address(row.from)}?q=acme`);
    await waitFor(() => {
      expect(location().pathname).toBe(address(row.to));
    });
    // A list keeps its filters on the way; the page of one thing (a service, the account) has none to keep.
    if (!row.from.includes("$") && !("plain" in row)) expect(location().search).toMatchObject({ q: "acme" });
  });

  it("keeps the old address out of the history, so Back does not return to a redirect", async () => {
    const { location, history } = open("/teams");
    await waitFor(() => {
      expect(location().pathname).toBe("/projects/teams");
    });
    expect(history.length).toBe(1);
  });

  it("shows the page of the new place", async () => {
    open("/teams");
    // Teams are the second tab of Projects: the same title, the tab marked.
    expect(await screen.findByRole("heading", { level: 1, name: "Projects" })).toBeInTheDocument();
  });
});

describe("an old address that is pending", () => {
  it.each(PENDING.map((row) => [row.from, row] as const))("%s still opens its own page", async (_from, row) => {
    const { location } = open(address(row.from));
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(location().pathname).toBe(address(row.from));
  });
});

describe("a route whose page is yet to come", () => {
  if (INTERIM.length === 0) {
    it("is none now: every new route has its page", () => {
      expect(INTERIM).toEqual([]);
    });
    return;
  }
  it.each(INTERIM.map((row) => [row.from, row] as const))("%s opens the screen that holds the same thing today", async (_from, row) => {
    const { location } = open(address(row.from));
    await waitFor(() => {
      expect(location().pathname).toBe(address(row.to));
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });
});
