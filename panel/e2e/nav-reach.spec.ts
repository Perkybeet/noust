/// <reference types="vite/client" />
/**
 * Where everything is, walked from the outside (docs/DESIGN.md, NV-2 and NV-3; section 7 of the 4.0
 * navigation design): the register of screens (app/nav.ts) says what exists, and a browser, on the
 * real backend, finds each one the way a person does, by following what the page offers.
 *
 * - **Depth.** Starting at the Overview and following only links (the sidebar, the tab strips, the
 *   lists of Settings and of an application's settings, and any link on a page to a screen of the
 *   register), every screen is at most three clicks away, and four when an application or another
 *   resource has to be opened first. A screen nothing links to is a screen nobody finds.
 * - **The palette.** Every screen is found by its name with the keyboard alone: Ctrl+K, the name,
 *   Enter. No click.
 * - **Address length.** No page of the walk has more than four segments after the node's (NV-2).
 * - **A fleet.** The same two, from the fleet's own sidebar, for what is the fleet's.
 *
 * A screen that exists only under a condition (a fleet, a Compose stack, a unit, a database engine's
 * capability), one that is only a route so far, and one reached from a state and not browsed (a
 * `hidden` entry: the terminal, a deployment, a recipe) is not in the walk of depth; the register's own
 * test holds each of those to a way in. The palette finds the hidden ones too.
 */

import type { Page, Response } from "@playwright/test";

import { DESTS, REDIRECTS, isStaged, paletteEntries } from "../src/app/nav";
import type { Dest } from "../src/app/nav";
import type { PlainKey } from "../src/i18n";
import { english } from "./catalog";
import { depthOf } from "./clicks";
import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

/** Where an address ends up: an old address that redirects is its screen's new place (NV-18), and that is where the palette lands. */
function placeOf(to: string): string {
  return REDIRECTS.find((row) => row.from === to && row.state === "live")?.to ?? to;
}

/** A text of the English catalog by its dotted key. */
function textOf(key: PlainKey): string {
  let node: unknown = english;
  for (const part of key.split(".")) node = (node as Record<string, unknown> | undefined)?.[part];
  if (typeof node !== "string") throw new Error(`the catalog has no text for "${key}"`);
  return node;
}

/** A path with parameters (`/apps/$domain/settings`) as the pattern of the addresses it stands for. */
function patternOf(to: string): RegExp {
  const escaped = to.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(`^${escaped.replace(/\\\$\w+/g, "[^/]+")}/?$`);
}

/** Whether the register has this entry on every server: no condition, no capability, not a route only so far. */
function everywhere(entry: Dest): boolean {
  return entry.when === undefined && entry.needs === undefined && !isStaged(entry);
}

/** A screen of the register as the addresses it stands for, and whether those need a resource (a parameter). */
interface Target {
  source: string;
  dynamic: boolean;
}

function targets(): Target[] {
  return DESTS.filter((entry) => !isStaged(entry)).map((entry) => ({ source: patternOf(entry.to).source, dynamic: entry.to.includes("$") }));
}

/**
 * What a page offers to go on from, as a person sees it: the links of its navigation landmarks (the
 * sidebar, the strips, the lists of Settings), and any other visible link that goes to a screen of
 * the register (the Overview's link to the Map, a list's row that opens a resource). Of the links to
 * the same kind of resource, such as the applications, the first: they are one click each, and the
 * walk does not need every row.
 */
async function offered(page: Page, known: readonly Target[]): Promise<string[]> {
  return page.evaluate((register) => {
    const visible = (element: Element): boolean => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";
    const found = new Set<string>();
    const pathOf = (anchor: HTMLAnchorElement): string | null => {
      const url = new URL(anchor.href, location.href);
      if (url.origin !== location.origin || /^\/(api|assets|events|ws|hooks)\b/.test(url.pathname)) return null;
      return url.pathname.replace(/\/$/, "") || "/";
    };
    const inBreadcrumbs = (anchor: Element): boolean => (anchor.closest("nav")?.getAttribute("aria-label") ?? "").toLowerCase().startsWith("breadcrumb");
    const taken = new Set<string>();
    for (const anchor of document.querySelectorAll<HTMLAnchorElement>("a[href]")) {
      // Breadcrumbs go up, to somewhere shallower: they add no way to get anywhere.
      if (!visible(anchor) || inBreadcrumbs(anchor)) continue;
      const path = pathOf(anchor);
      if (path === null) continue;
      const inNavigation = anchor.closest("nav") !== null;
      const match = register.find((target) => new RegExp(target.source).test(path));
      if (match === undefined) {
        if (inNavigation) found.add(path);
        continue;
      }
      if (match.dynamic && !inNavigation) {
        if (taken.has(match.source)) continue;
        taken.add(match.source);
      }
      found.add(path);
    }
    return [...found];
  }, known);
}

/**
 * The links a menu of the top bar offers (the account, its sign-in security, the language): they are
 * behind a click, so they cost one more than a link in the page.
 */
async function menuLinks(page: Page): Promise<string[]> {
  const trigger = page.getByRole("button", { name: "Session and preferences" });
  if ((await trigger.count()) === 0) return [];
  await trigger.click();
  // Only the console's own addresses: the menu also links to the documentation, which is another site.
  const hrefs = await page.locator('[role="menu"] a[href], [role="dialog"] a[href]').evaluateAll((anchors) =>
    anchors.flatMap((anchor) => {
      const url = new URL((anchor as HTMLAnchorElement).href, location.href);
      return url.origin === location.origin ? [url.pathname.replace(/\/$/, "") || "/"] : [];
    }),
  );
  await page.keyboard.press("Escape");
  return hrefs;
}

/**
 * Keeps the walk under the rate the server allows an authenticated session (1200 requests a minute): a cold page
 * makes dozens, and a hundred and fifty of them in three minutes would be refused, with the refusals read as a page
 * that links to nothing. It counts the API requests of the last minute and waits before the next page while there
 * are too many.
 */
function pacer(page: Page): () => Promise<void> {
  const sent: number[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) sent.push(Date.now());
  });
  return async () => {
    for (;;) {
      const since = Date.now() - 60_000;
      while (sent.length > 0 && (sent[0] ?? 0) < since) sent.shift();
      if (sent.length < 800) return;
      await page.waitForTimeout(2_000);
    }
  };
}

/** Opens a page of the walk, and when the server has refused part of it all the same, waits for the window to reopen and looks again. */
async function visit(page: Page, path: string, pace: () => Promise<void>): Promise<void> {
  await pace();
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const refused: string[] = [];
    const watch = (response: Response): void => {
      if (response.status() === 429) refused.push(response.url());
    };
    page.on("response", watch);
    try {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await settle(page);
    } finally {
      page.off("response", watch);
    }
    if (refused.length === 0) return;
    await page.waitForTimeout(30_000);
  }
}

/**
 * Follows what the console offers from `start`, a click at a time, and says how many clicks each page
 * it reaches takes. Every page is loaded once, at the least number of clicks it was offered at.
 */
async function crawl(page: Page, start: string, within: (path: string) => boolean): Promise<Map<string, number>> {
  const known = targets();
  const depths = new Map<string, number>([[start, 0]]);
  const queue: string[] = [start];
  const pace = pacer(page);
  let first = true;
  while (queue.length > 0) {
    const path = queue.shift() ?? "";
    await visit(page, path, pace);
    const here = depths.get(path) ?? 0;
    const offers = (await offered(page, known)).map((next) => ({ next, cost: 1 }));
    // The menu of the top bar is the same on every page: its links are asked for once, on the first.
    if (first) offers.push(...(await menuLinks(page)).map((next) => ({ next, cost: 2 })));
    first = false;
    for (const { next, cost } of offers) {
      if (!within(next)) continue;
      const seen = depths.get(next);
      if (seen === undefined) queue.push(next);
      if (seen === undefined || seen > here + cost) depths.set(next, here + cost);
    }
  }
  return depths;
}

/** The least clicks at which any page the walk reached is this entry. */
function reachedAt(depths: ReadonlyMap<string, number>, entry: Dest): number | undefined {
  const pattern = patternOf(entry.to);
  const found = [...depths].filter(([path]) => pattern.test(path)).map(([, depth]) => depth);
  return found.length === 0 ? undefined : Math.min(...found);
}

/**
 * Screens that are in the register and that no link of the console leads to yet, each with the lane that draws
 * the way in. The walk fails when a screen is lost that is not here, and when one is here and found: the list only
 * gets shorter, and it is empty when the 4.0 work is whole.
 */
const NOT_LINKED_YET: Readonly<Record<string, string>> = {};

/** Screens the seeded machine has no way to show, and why; the register's own test holds each to its way in. */
const NOT_ON_THIS_MACHINE: Readonly<Record<string, string>> = {
  "app.settings.container": "the section of a container application, and the seeded machine has none",
};

/** The most clicks a screen may take from the Overview: three, and four when a resource is opened first. */
const budgetOf = (entry: Dest): number => (entry.to.includes("$") ? 4 : 3);

test.describe("a server on its own", () => {
  test.setTimeout(900_000);

  test("every screen of the register is at most three clicks from the Overview, four through a resource", async ({ page, consoleServer }, testInfo) => {
    // The same links in both themes: the walk is run once.
    test.skip(testInfo.project.name !== "light", "the links are the same in both themes");
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/");
    // A screen of the fleet is not on a server on its own; the others are the register's.
    const expected = DESTS.filter((entry) => entry.scope !== "fleet" && entry.kind !== "hidden" && everywhere(entry) && entry.id !== "overview" && !(entry.id in NOT_ON_THIS_MACHINE));
    const depths = await crawl(page, "/", (path) => !path.startsWith("/n/") && !path.startsWith("/fleet") && !path.startsWith("/login"));

    const slow: string[] = [];
    const lost: string[] = [];
    for (const entry of expected) {
      const at = reachedAt(depths, entry);
      if (at === undefined) lost.push(`${entry.id} (${entry.to})`);
      else if (at > budgetOf(entry)) slow.push(`${entry.id} (${entry.to}) is ${String(at)} clicks away; the most is ${String(budgetOf(entry))}`);
    }
    const unexplained = lost.filter((name) => !((name.split(" ")[0] ?? "") in NOT_LINKED_YET));
    expect(unexplained, `nothing the console offers leads to:\n${unexplained.join("\n")}`).toEqual([]);
    const linked = Object.keys(NOT_LINKED_YET).filter((id) => !lost.some((name) => name.startsWith(`${id} `)));
    expect(linked, `these are linked now, so they leave NOT_LINKED_YET: ${linked.join(", ")}`).toEqual([]);
    expect(slow, slow.join("\n")).toEqual([]);

    // NV-2: four segments at most, and a screen of the walk does not exceed them.
    const deep = [...depths.keys()].filter((path) => depthOf(path) > 4);
    expect(deep, `these addresses have more than four segments: ${deep.join(", ")}`).toEqual([]);
  });

  test("every screen is found by its name with the keyboard alone, Ctrl+K, the name, Enter", async ({ page, consoleServer }, testInfo) => {
    test.skip(testInfo.project.name !== "light", "the palette is the same in both themes");
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/databases");
    const entries = paletteEntries({ t: (key: PlainKey) => textOf(key), can: () => true, when: {} }).filter((entry) => entry.scope !== "fleet");
    expect(entries.length).toBeGreaterThan(40);
    const search = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });

    const pace = pacer(page);
    const missed: string[] = [];
    for (const entry of entries) {
      // From a page that has nothing to do with it, so the palette is asked from the same place every time.
      await visit(page, "/databases", pace);
      await page.keyboard.press("Control+k");
      await search.fill(entry.text);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog", { name: "Search the console" })).toBeHidden();
      const landed = new URL(page.url()).pathname.replace(/\/$/, "") || "/";
      if (landed !== placeOf(entry.to)) missed.push(`"${entry.text}" went to ${landed}, not ${placeOf(entry.to)} (${entry.id})`);
    }
    expect(missed, missed.join("\n")).toEqual([]);
  });
});

/** A central with one node, as fleet.spec.ts makes them. */
const NODE_NAME = "fleet-node";

interface FleetWorkerFixtures {
  node: RunningServer;
  consoleServer: ConsoleServer;
}

const withFleet = test.extend<object, FleetWorkerFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([]);
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ node }, use) => {
      const central = await startConsoleServer(["--totp", "--backup-codes", "500", "--fleet-node", `${NODE_NAME}=127.0.0.1:${new URL(node.url).port}`]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
});

withFleet.describe("a fleet", () => {
  withFleet.setTimeout(300_000);

  withFleet("every screen of the fleet is one click from the fleet's own sidebar, and found by its name", async ({ page, consoleServer }, testInfo) => {
    withFleet.skip(testInfo.project.name !== "light", "the links are the same in both themes");
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/fleet");
    const expected = DESTS.filter((entry) => entry.scope === "fleet" && entry.kind !== "hidden" && everywhere(entry));
    const depths = await crawl(page, "/fleet", (path) => path.startsWith("/fleet"));

    const lost = expected.filter((entry) => reachedAt(depths, entry) === undefined).map((entry) => `${entry.id} (${entry.to})`);
    expect(lost, `nothing the fleet offers leads to:\n${lost.join("\n")}`).toEqual([]);
    // The fleet is two clicks from anywhere (the selector, All servers); its own screens are within three more.
    const slow = expected.filter((entry) => (reachedAt(depths, entry) ?? 0) > 3).map((entry) => entry.id);
    expect(slow, `these take more than three clicks from the fleet's page: ${slow.join(", ")}`).toEqual([]);

    const search = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
    const missed: string[] = [];
    for (const entry of paletteEntries({ t: (key: PlainKey) => textOf(key), can: () => true, when: { fleet: true } }).filter((candidate) => candidate.scope === "fleet")) {
      await page.goto("/fleet");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.keyboard.press("Control+k");
      await search.fill(entry.text);
      await page.keyboard.press("Enter");
      await expect(page.getByRole("dialog", { name: "Search the console" })).toBeHidden();
      const landed = new URL(page.url()).pathname.replace(/\/$/, "") || "/";
      if (landed !== placeOf(entry.to)) missed.push(`"${entry.text}" went to ${landed}, not ${placeOf(entry.to)} (${entry.id})`);
    }
    expect(missed, missed.join("\n")).toEqual([]);
  });
});
