/**
 * Arriving, getting in and finding (items 157, 158, 160 and 197), in a browser: what a person sees who reaches a new
 * console without the installer's link, the first account and the offer that does not come back at every sign-in, the
 * hover of an option that must not look like the chosen one, and the bar read in words on a laptop. Runs once per theme.
 *
 * The pictures for review are tagged @screens (left out of the default run):
 *
 *     NOUST_SCREENS=1 NOUST_ARRIVAL_SCREENS=<dir> npx playwright test e2e/arrival.spec.ts
 */

import type { Locator, Page } from "@playwright/test";
import path from "node:path";

import { startServerWithAccounts } from "./accounts-server";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, stillness, test } from "./fixtures";
import type { RunningServer } from "./fixtures";

const OUT = process.env.NOUST_ARRIVAL_SCREENS ?? path.join(import.meta.dirname, "__screens__", "arrival");
const APP = "/apps/shop.example.net";
const SEARCH = "Search apps, databases, domains, servers, settings and actions";

/** What an option under the pointer looks like, as the browser computes it. */
async function looksOf(option: Locator): Promise<{ shadow: string; transform: string; background: string; color: string }> {
  return option.evaluate((element) => {
    const style = getComputedStyle(element);
    return { shadow: style.boxShadow, transform: style.transform, background: style.backgroundColor, color: style.color };
  });
}

/** The raised piece of a list or a row: what the chosen option is. */
async function pieceOf(container: Locator): Promise<{ shadow: string; background: string }> {
  return container.locator('[data-slot="sliding-indicator"]').first().evaluate((element) => {
    const style = getComputedStyle(element);
    return { shadow: style.boxShadow, background: style.backgroundColor };
  });
}

test.describe("an option under the pointer is not the chosen one (item 197)", () => {
  test("in the main bar, the column and a row of segments it takes a flat tone, and only the chosen one is a piece", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `${APP}/logs`);
    await settle(page);

    const bar = page.getByRole("navigation", { name: "Main" });
    const column = page.getByRole("navigation", { name: "Application sections" });
    for (const [where, option] of [
      ["the main bar", bar.getByRole("link", { name: "Domains" })],
      ["the column", column.getByRole("link", { name: "Deployments" })],
    ] as const) {
      const container = where === "the main bar" ? bar : column;
      const before = await looksOf(option);
      await option.hover();
      await stillness(page);
      const hovered = await looksOf(option);
      const chosen = await pieceOf(container);
      // A tone and a darker text, no height: nothing that rises, no edge, no joint.
      expect(hovered.shadow, `${where}: no edge under the pointer`).toBe("none");
      expect(hovered.transform, `${where}: it does not rise`).toBe("none");
      expect(hovered.background, `${where}: it has a ground of its own`).not.toBe("rgba(0, 0, 0, 0)");
      expect(hovered.background, `${where}: it is not the chosen piece's ground`).not.toBe(chosen.background);
      expect(hovered.color, `${where}: the text goes to the full colour`).not.toBe(before.color);
      // The chosen one stays the one piece: raised, with its edge.
      expect(chosen.shadow, `${where}: the chosen one keeps its joint`).not.toBe("none");
      // Under the pointer nothing moves the piece: the chosen option is still the current one.
      await expect(container.locator('[aria-current="page"]')).toHaveCount(1);
    }

    // The chosen option itself does not change under the pointer.
    const current = column.locator('[aria-current="page"]');
    const rest = await looksOf(current);
    await current.hover();
    await stillness(page);
    expect(await looksOf(current)).toEqual(rest);
  });

  test("a row of segments does the same, and the chosen segment is the one that is raised", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `${APP}/metrics`);
    await settle(page);
    const range = page.getByRole("radiogroup").first();
    const options = range.getByRole("radio");
    const count = await options.count();
    expect(count).toBeGreaterThan(1);
    // Which one is chosen, and one that is not.
    const states = await options.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-checked") === "true" || node.hasAttribute("data-checked")));
    const other = options.nth(states.findIndex((chosen) => !chosen));
    const chosen = await pieceOf(range);
    await other.hover();
    await stillness(page);
    const hovered = await looksOf(other);
    expect(hovered.shadow).toBe("none");
    expect(hovered.transform).toBe("none");
    expect(hovered.background).not.toBe("rgba(0, 0, 0, 0)");
    expect(hovered.background).not.toBe(chosen.background);
    expect(chosen.shadow).not.toBe("none");
  });
});

test.describe("the bar on a laptop (item 157)", () => {
  for (const width of [1200, 1280, 1366, 1440]) {
    test(`at ${String(width)} px has its words, the column beside it, and the page of a settings section whole`, async ({ page, consoleServer }) => {
      await page.setViewportSize({ width, height: 800 });
      await signIn(page, consoleServer, "/settings/tokens");
      await settle(page);
      await expect(page.getByRole("navigation", { name: "Main" }).getByText("Applications", { exact: true })).toBeVisible();
      await expect(page.locator('[data-frame="column"]')).toBeVisible();
      const main = await page.getByRole("main").boundingBox();
      expect(main?.width ?? 0).toBeGreaterThan(560);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      await expectNoA11yViolations(page, `Settings, the frame at ${String(width)}`);
    });
  }
});

test.describe("the bar on a short laptop screen (item 157)", () => {
  // The bar in words is taller than the bar of icons was: on 768 and 720px its rows and the titles of its groups take less
  // height, so Settings (the last of it) and the way to fold it are on screen without scrolling the bar.
  for (const [width, height] of [[1366, 768], [1280, 720], [1200, 720]] as const) {
    test(`at ${String(width)}x${String(height)} the whole bar, Settings included, is on screen without scrolling it`, async ({ page, consoleServer }) => {
      await page.setViewportSize({ width, height });
      await signIn(page, consoleServer, `${APP}/deployments`);
      await settle(page);
      const bar = page.getByRole("navigation", { name: "Main" });
      const heights = await bar.evaluate((nav) => ({ scrollHeight: nav.scrollHeight, clientHeight: nav.clientHeight }));
      expect(heights.scrollHeight, `the bar needs no scrolling (it holds ${String(heights.scrollHeight)}px in ${String(heights.clientHeight)}px)`).toBeLessThanOrEqual(heights.clientHeight + 1);
      const settings = await bar.getByRole("link", { name: "Settings" }).boundingBox();
      expect((settings?.y ?? Infinity) + (settings?.height ?? 0), "Settings ends inside the window").toBeLessThanOrEqual(height);
      const fold = await page.getByRole("button", { name: "Fold the bar" }).boundingBox();
      expect((fold?.y ?? Infinity) + (fold?.height ?? 0), "the fold button ends inside the window").toBeLessThanOrEqual(height);
      await expectNoA11yViolations(page, `the bar at ${String(width)}x${String(height)}`);
    });
  }
});

test.describe("a new console, from the door to the first account (item 158)", () => {
  let server: RunningServer;

  test.beforeEach(async () => {
    server = await startConsoleServer(["--empty"]);
  });

  test.afterEach(async ({ page }) => {
    await page.goto("about:blank");
    await server.stop();
  });

  test("tells whoever arrives without the installer's link where the token is, and does not frighten them with recovery", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    // A server with no account has nobody to ask for a name: the page opens on the token itself, and says who printed it.
    await page.goto(`${server.url}/login`);
    await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token", exact: true })).toBeVisible();
    await expect(page.getByText("The installer printed it once, in a box. Paste it here.")).toBeVisible();
    await expect(page.getByText("noust web token --new")).toBeVisible();
    // Nothing of recovery: this is the first sign-in, not the way back in. And no doors to an account form that has no accounts.
    await expect(page.getByText(/Just installed Noust\?/)).toHaveCount(0);
    await expect(page.getByText(/Once there are accounts/)).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Other ways to sign in" })).toHaveCount(0);
    await settle(page);
    await expectNoA11yViolations(page, "the sign-in of a new server, without the installer's link");
  });

  test("a server that has accounts says its token is the server's, and what the token is for only once it is chosen", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    const withAccounts = await startServerWithAccounts();
    try {
      await page.goto(`${withAccounts.url}/login`);
      await expect(page.getByRole("heading", { level: 1, name: "Sign in", exact: true })).toBeVisible();
      // A server that has accounts does not say where its token was shown: there may have been no installer (a container, a store's app).
      await expect(page.getByText(/First time on this server\? Sign in with this server's access token, under/)).toBeVisible();
      await expect(page.getByText(/installer printed/i)).toHaveCount(0);
      await expect(page.getByText("noust web token --new")).toBeVisible();
      await settle(page);
      await expectNoA11yViolations(page, "the sign-in of a server with accounts, without the installer's link");

      await page.getByRole("button", { name: "Access token" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeVisible();
      await expect(page.getByText("Paste this server's access token.")).toBeVisible();
      await expect(page.getByText(/Just installed Noust\? Sign in with it and create your own account/)).toBeVisible();
      await expect(page.getByText(/Once there are accounts/)).toHaveCount(0);
      await settle(page);
      await expectNoA11yViolations(page, "the access token, from the account form");
    } finally {
      await page.goto("about:blank");
      await withAccounts.stop();
    }
  });

  test("says the account is the person's own, and does not bring the page back at the next sign-in with the token", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    await page.goto(`${server.url}/login?with=token`);
    await page.getByLabel("Access token").fill(server.token);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Create your administrator account" })).toBeVisible();
    await expect(page.getByText(/The one you create now is yours, the administrator's/)).toBeVisible();
    await expect(page.getByLabel(/^Password/)).toHaveAccessibleDescription(/^Long and unique\. You can change it later from your account/);
    await expect(page.getByText(/If you do not manage this server, choose Not now/)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the first account");

    await page.getByRole("button", { name: "Not now" }).click();
    await expect(page).not.toHaveURL(/\/setup/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // Another sign-in with the token, in the same browser: the first accounts are not offered again.
    await page.context().clearCookies();
    await page.goto(`${server.url}/login?with=token`);
    await page.getByLabel("Access token").fill(server.token);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).not.toHaveURL(/\/login/);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page).not.toHaveURL(/\/setup/);
    // And the value the browser kept is what said so: put off, a week to run.
    const kept = await page.evaluate(() => window.localStorage.getItem("noust.later"));
    expect(JSON.parse(kept ?? "{}")).toMatchObject({ account: { returned: false } });
  });
});

test.describe("finding what the first round did not find (item 160)", () => {
  test("a lone server shows how to add a second one, with what a fleet is", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/server");
    await settle(page);
    const card = page.getByRole("region", { name: "More than one server?" });
    await expect(card).toBeVisible();
    await expect(card.getByText(/A fleet is several servers you manage from one console/)).toBeVisible();
    await card.getByRole("link", { name: "Add a server" }).click();
    await expect(page).toHaveURL(/\/fleet\/servers/);
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("the application's Domains tab leads to where the upload limit is set", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `${APP}/domains`);
    await settle(page);
    const section = page.getByRole("region", { name: "Upload size" });
    await expect(section).toBeVisible();
    await section.getByRole("link", { name: "Open the site's Structure" }).click();
    await expect(page).toHaveURL(/\/domains\/sites\/.+view=structure/);
    await expect(page.getByLabel("Max upload size").first()).toBeVisible();
  });

  test("the palette finds the shell for \"migrate\" and the sites for \"upload\"", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `${APP}/logs`);
    await settle(page);
    const search = async (query: string): Promise<Locator> => {
      await page.keyboard.press("Control+k");
      await page.getByRole("combobox", { name: SEARCH }).fill(query);
      return page.getByRole("dialog", { name: "Search the console" });
    };
    // Inside an application the shell of the application is among what is found, by what a person calls it.
    let results = await search("migrate");
    await expect(results.getByRole("option", { name: new RegExp(`Terminal.*${APP.slice(6)}`) }).first()).toBeVisible();
    await page.keyboard.press("Escape");
    results = await search("upload");
    await expect(results.getByRole("option", { name: /Sites|Domains/ }).first()).toBeVisible();
  });

  test("Activity and the audit log each say how they differ from the other", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/activity");
    await expect(page.getByText(/to follow the work\. To know who used which privilege, there is the audit log\./)).toBeVisible();
    await page.goto("/settings/audit");
    await expect(page.getByText(/in a record that shows if anybody alters it/)).toBeVisible();
    await page.getByRole("main").getByRole("link", { name: "Activity" }).click();
    await expect(page).toHaveURL(/\/activity/);
  });
});

test.describe("the pictures for review @screens", () => {
  const theme = (): string => test.info().project.name;
  const out = (name: string): string => path.join(OUT, `${name}-${theme()}.png`);

  async function hoverPicture(page: Page, name: string, target: Locator, clip: { x: number; y: number; width: number; height: number }): Promise<void> {
    await target.hover();
    await stillness(page);
    await page.screenshot({ path: out(name), clip });
  }

  test("the bar at 1200, 1280, 1366 and 1440", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 800 });
    await signIn(page, consoleServer, `${APP}/deployments`);
    for (const width of [1200, 1280, 1366, 1440]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto(`${APP}/deployments`);
      await expect(page.getByRole("heading", { level: 1, name: "shop.example.net" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: out(`bar-${String(width)}`) });
    }
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/settings/tokens");
    await settle(page);
    await page.screenshot({ path: out("bar-1280-settings") });
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto("/apps");
    await settle(page);
    await page.screenshot({ path: out("bar-1366x768-apps") });
  });

  test("an option under the pointer, beside the chosen one", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, `${APP}/logs`);
    await settle(page);
    const bar = page.getByRole("navigation", { name: "Main" });
    const column = page.getByRole("navigation", { name: "Application sections" });
    await hoverPicture(page, "hover-bar", bar.getByRole("link", { name: "Domains" }), { x: 0, y: 0, width: 720, height: 560 });
    await hoverPicture(page, "hover-column", column.getByRole("link", { name: "Deployments" }), { x: 200, y: 0, width: 520, height: 560 });
    await page.goto(`${APP}/metrics`);
    await settle(page);
    const range = page.getByRole("radiogroup").first();
    const box = await range.boundingBox();
    if (box === null) throw new Error("no range");
    await hoverPicture(page, "hover-segments", range.getByRole("radio").nth(1), { x: Math.max(0, box.x - 24), y: Math.max(0, box.y - 24), width: 420, height: box.height + 48 });
  });

  test("the entry and the first account", async ({ page, problems }) => {
    problems.expect(/status of 401 .* \/api\/auth\/session$/);
    const server = await startConsoleServer(["--empty"]);
    try {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`${server.url}/login`);
      await expect(page.getByText(/First time on this server\?/)).toBeVisible();
      await settle(page);
      await page.screenshot({ path: out("entry-1440") });
      await page.getByRole("button", { name: "Access token" }).click();
      await expect(page.getByText(/Just installed Noust\?/)).toBeVisible();
      await settle(page);
      await page.screenshot({ path: out("entry-token-1440") });
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(`${server.url}/login`);
      await expect(page.getByText(/First time on this server\?/)).toBeVisible();
      await settle(page);
      await page.screenshot({ path: out("entry-390") });

      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`${server.url}/login?with=token`);
      await page.getByLabel("Access token").fill(server.token);
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Create your administrator account" })).toBeVisible();
      await settle(page);
      await page.screenshot({ path: out("first-account-1440") });
      await page.setViewportSize({ width: 390, height: 844 });
      await settle(page);
      await page.screenshot({ path: out("first-account-390") });
    } finally {
      await page.goto("about:blank");
      await server.stop();
    }
  });

  test("the way to a fleet, the upload limit and the two descriptions", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/server");
    await settle(page);
    // The card is under the machine's readings: the picture is of it, on the page.
    await page.getByRole("region", { name: "More than one server?" }).scrollIntoViewIfNeeded();
    await settle(page);
    await page.screenshot({ path: out("server-fleet-1440") });
    await page.goto(`${APP}/domains`);
    await expect(page.getByRole("region", { name: "Upload size" })).toBeVisible();
    await settle(page);
    await page.getByRole("region", { name: "Upload size" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: out("domains-upload-1440") });
    await page.goto("/activity");
    await settle(page);
    await page.screenshot({ path: out("activity-1440") });
    await page.goto("/settings/audit");
    await settle(page);
    await page.screenshot({ path: out("audit-1440") });
  });
});
