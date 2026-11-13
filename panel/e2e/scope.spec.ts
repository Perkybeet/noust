/**
 * "All servers" and what a server offers, walked from the outside with a real browser on the real backend (items 196 and 187). The
 * owner's words are the criteria: with All servers chosen the selector cannot change by itself, every section has to be seen and no click
 * can be a dead end or bounce to Home; and "if something cannot be done, do not give the option".
 *
 * It runs twice, once with a central that is a hub and once with a central that is a server, each with one node. Every entry of the main
 * bar, of the second column and of the palette is pressed, and the test checks the address, the page and the selector, and fails on any
 * 4xx or 5xx the page causes (the console error of a failed request) and on any text of a refusal.
 */

import type { Locator, Page } from "@playwright/test";

import { areaOf, paletteEntries } from "../src/app/nav";
import { isCentralOnlyPath } from "../src/app/nodeRoute";
import type { PlainKey } from "../src/i18n";
import { english } from "./catalog";
import { expect, settle, signIn, startConsoleServer, test as base } from "./fixtures";
import type { RunningServer } from "./fixtures";

interface Fleet {
  central: RunningServer;
  node: RunningServer;
}

type Role = "hub" | "server";

/**
 * A test with a central of this role and one node, booted once for the worker that needs it: the hub's and the server's are two
 * fixtures of two tests, so a worker that runs one never boots the other.
 */
function withFleet(role: Role, machine: readonly string[] = []) {
  return base.extend<object, { fleet: Fleet }>({
    fleet: [
      // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
      async ({}, use) => {
        const node = await startConsoleServer([]);
        // A central that manages a server refuses to while its own sign-in has no second factor.
        const central = await startConsoleServer(["--totp", "--backup-codes", "2500", ...machine, "--central-role", role, "--fleet-node", `web-2=127.0.0.1:${new URL(node.url).port}`]);
        try {
          await use({ central, node });
        } finally {
          await Promise.all([central.stop(), node.stop()]);
        }
      },
      { scope: "worker", timeout: 180_000 },
    ],
    // The pages of the test are this central's, not the worker's own server.
    baseURL: async ({ fleet }, use) => {
      await use(fleet.central.url);
    },
  });
}

/** The hub as the owner runs it, `nas`: the container of a NAS, with no systemd, no journal, no nginx, no Docker and no root. */
const tests = { hub: withFleet("hub"), server: withFleet("server"), nas: withFleet("hub", ["--host-profile", "container"]) };

for (const each of Object.values(tests)) each.use({ viewport: { width: 1440, height: 900 } });

/** What a refusal of the server says, wherever it is drawn: nothing the console offers may end in one. */
const REFUSAL = /not available on (?:this central|a hub)|hub_role/i;

const selector = (page: Page): Locator => page.getByRole("button", { name: /^Server: / });

const bar = (page: Page): Locator => page.getByRole("navigation", { name: "Main" });

/** The second column of the frame: the only list that keeps the focus on its links. */
const column = (page: Page): Locator => page.getByRole("complementary").locator("nav[data-keep-focus]").first();

/** The links of a list of the frame, as name and address: read before pressing any, because pressing one redraws the list. */
async function linksOf(list: Locator): Promise<{ name: string; href: string }[]> {
  return list.getByRole("link").evaluateAll((all) =>
    all.map((link) => ({ name: link.textContent.replace(/\s+/g, " ").replace(/\d+$/, "").trim(), href: link.getAttribute("href") ?? "" })),
  );
}

/** Presses a link and waits until the page it opens is drawn: its heading is there, and no refusal is. */
async function open(page: Page, link: { name: string; href: string }, within: Locator): Promise<void> {
  await within.locator(`a[href="${link.href}"]`).first().click();
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  await settle(page);
  await expect(page.getByText(REFUSAL), `${link.name} (${link.href}) ends in a refusal`).toHaveCount(0);
}

/**
 * Opens an area from the bar and waits until the second column is that area's: the column slides from the previous list to the new one, and a
 * list read in between is the previous area's (its links would not be there to press).
 */
async function openArea(page: Page, area: { name: string; href: string }): Promise<void> {
  await open(page, area, bar(page));
  const kind = areaOf(area.href.split("?")[0] ?? "")?.id;
  await expect(page.locator('aside[data-frame="column"]'), `the column of ${area.name} did not arrive`).toHaveAttribute("data-column", kind ?? "");
}

/** A text of the English catalog by its dotted key, as the palette says it. */
function textOf(key: PlainKey): string {
  let node: unknown = english;
  for (const part of key.split(".")) node = (node as Record<string, unknown> | undefined)?.[part];
  if (typeof node !== "string") throw new Error(`the catalog has no text for "${key}"`);
  return node;
}

/** Where the address is, without what is after the path. */
const pathOf = (page: Page): string => new URL(page.url()).pathname;

for (const role of ["hub", "server"] as const) {
  const test = tests[role];
  test.describe(`a central that is a ${role}`, () => {
    test(`with "Todos los servidores" elegido, el selector no puede cambiar solo: nada de la barra lo mueve (${role})`, async ({ page, fleet }) => {
      await signIn(page, fleet.central, "/fleet");
      await expect(selector(page)).toHaveAccessibleName("Server: all servers");

      const entries = await linksOf(bar(page));
      expect(entries.length, "the bar has its areas").toBeGreaterThanOrEqual(13);
      for (const entry of entries) {
        // Settings is the central's: the selector is a label there, and the choice comes back after it.
        if (entry.href === "/settings") continue;
        await open(page, entry, bar(page));
        expect(pathOf(page), `${entry.name} leaves All servers`).toMatch(/^\/fleet(\/|$)/);
        await expect(selector(page), `${entry.name} moved the selector`).toHaveAccessibleName("Server: all servers");
      }
    });

    test(`con "Todos los servidores" elegido tienen que verse todas las secciones: Copias enseña las cinco de un servidor (${role})`, async ({ page, fleet }) => {
      await signIn(page, fleet.central, "/fleet/backups");
      await expect(selector(page)).toHaveAccessibleName("Server: all servers");
      const has = async (path: string, sections: readonly string[]): Promise<void> => {
        await page.goto(`${fleet.central.url}${path}`);
        for (const section of sections) {
          await expect(column(page).getByRole("link", { name: new RegExp(`^${section}\\b`) }), `${path} lacks the section ${section}`).toBeVisible();
        }
      };
      await has("/fleet/backups", ["Backups", "Schedules", "Volumes", "Noust", "Destinations"]);
      // The areas that had no page of the fleet show the same sections as a server: Databases has its engines, Network its tunnels and firewall.
      await has("/fleet/databases", ["Engines"]);
      await has("/fleet/network", ["Tunnels", "Firewall"]);
    });

    test(`ningún clic puede ser un callejón o rebotar a Inicio: cada sección de la columna abre su página y deja el selector (${role})`, async ({ page, fleet }) => {
      test.setTimeout(300_000);
      await signIn(page, fleet.central, "/fleet");
      for (const area of await linksOf(bar(page))) {
        if (area.href === "/settings" || area.href === "/fleet") continue;
        await openArea(page, area);
        for (const section of await linksOf(column(page))) {
          if (!section.href.startsWith("/fleet")) continue;
          await open(page, section, column(page));
          const where = pathOf(page);
          expect(where, `${area.name} > ${section.name} opened ${where}, not ${section.href}`).toBe(section.href.split("?")[0]);
          expect(where, `${area.name} > ${section.name} bounced to Home`).not.toBe("/fleet");
          await expect(selector(page), `${area.name} > ${section.name} moved the selector`).toHaveAccessibleName("Server: all servers");
        }
      }
    });

    test(`the palette opens every page of the scope it is in, and never moves the selector (${role})`, async ({ page, fleet }, testInfo) => {
      test.skip(testInfo.project.name !== "light", "the palette is the same in both themes");
      test.setTimeout(600_000);
      await signIn(page, fleet.central, "/fleet");
      const entries = paletteEntries({ t: (key: PlainKey) => textOf(key), can: () => true, scope: "fleet", when: { fleet: true } });
      expect(entries.length).toBeGreaterThan(40);
      const search = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
      const missed: string[] = [];
      for (const entry of entries) {
        // Settings and the account are the central's: the selector is a label there, and they are not what this walk is about.
        if ((!entry.to.startsWith("/fleet") && isCentralOnlyPath(entry.to)) || entry.to === "/apps/new") continue;
        await page.goto(`${fleet.central.url}/fleet`);
        await expect(selector(page)).toHaveAccessibleName("Server: all servers");
        await page.keyboard.press("Control+k");
        await search.fill(entry.text);
        await page.keyboard.press("Enter");
        await expect(page.getByRole("dialog", { name: "Search the console" })).toBeHidden();
        await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
        const landed = pathOf(page);
        if (landed !== entry.to.split("?")[0]) missed.push(`"${entry.text}" went to ${landed}, not ${entry.to} (${entry.id})`);
        if ((await selector(page).getAttribute("aria-label")) !== "Server: all servers") missed.push(`"${entry.text}" moved the selector`);
        if ((await page.getByText(REFUSAL).count()) > 0) missed.push(`"${entry.text}" ends in a refusal`);
      }
      expect(missed, missed.join("\n")).toEqual([]);
    });
  });
}

/** Every entry of the bar and of the column, with the hub chosen in the selector, opens a page that works. */
async function walkTheHub(page: Page, central: RunningServer): Promise<void> {
  await signIn(page, central, "/");
  // A hub starts on All servers: choosing it as a server is the operator's.
  await expect(selector(page)).toHaveAccessibleName("Server: all servers");
  await selector(page).click();
  await page.getByRole("menuitemradio", { name: /Hub/ }).click();
  await expect(selector(page)).not.toHaveAccessibleName("Server: all servers");

  const areas = await linksOf(bar(page));
  // The areas a hub does not have are not in the bar: no Applications, no Databases, no Scheduled tasks.
  const names = areas.map((area) => area.name);
  for (const gone of [/^Applications/, /^Databases/, /^Scheduled tasks/, /^Domains/]) {
    expect(names.some((name) => gone.test(name)), `a hub's bar has ${String(gone)}`).toBe(false);
  }
  for (const area of areas) {
    if (area.href === "/settings") continue;
    await openArea(page, area);
    for (const section of await linksOf(column(page))) {
      if (section.href.startsWith("/fleet")) continue;
      await open(page, section, column(page));
    }
  }
}

tests.hub.describe("a hub chosen in the selector offers what a hub can do, and nothing else", () => {
  tests.hub("every entry of the bar and of the column opens a page that works (no 4xx, no 5xx, no refusal)", async ({ page, fleet }) => {
    tests.hub.setTimeout(300_000);
    await walkTheHub(page, fleet.central);
  });
});

tests.nas.describe("the hub of a NAS, a container with no systemd, journal, nginx, Docker or root, offers none of what it lacks", () => {
  tests.nas("every entry of the bar and of the column opens a page that works (no 4xx, no 5xx, no refusal)", async ({ page, fleet }) => {
    tests.nas.setTimeout(300_000);
    await walkTheHub(page, fleet.central);
    // What the machine lacks is not in the bar either: no Security, no Logs of a journal that is not there.
    const names = (await linksOf(bar(page))).map((area) => area.name);
    for (const gone of [/^Security/, /^Logs/]) expect(names.some((name) => gone.test(name)), `the hub of a NAS has ${String(gone)}`).toBe(false);
  });
});
