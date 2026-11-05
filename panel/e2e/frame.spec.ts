/**
 * The frame of the console in a browser (docs/DESIGN.md, NV-20 to NV-22): the one frame of 4.2, a main bar and
 * the context column, at the widths that matter (1920, 1440, 1280 and 390 px), in both themes (the light and dark
 * projects), with the keyboard and with reduced motion, with axe and the Content Security Policy watching, and the
 * movement of the raised piece of the current item in the main bar and in the second column, and of the column
 * itself when a resource is entered. The register's rules are nav.register.test.ts's and columns.test.ts's, and
 * the layout's are app/navLayout.test.ts's; what only a browser can say is here: where each bar is, how wide,
 * that nothing scrolls sideways, that the piece really slides, for how long, and where it ends.
 *
 * Run on a production build (NOUST_E2E_STATIC=<dir>): the development build repeats requests and meets the
 * rate limit. With NOUST_FRAME_FRAMES=<directory> it also writes the pictures of the frame and three frames
 * of the slide (its start, its middle and its end), with the transition held at a time by the Web Animations
 * API.
 */

import path from "node:path";

import type { Locator, Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const FRAMES = process.env.NOUST_FRAME_FRAMES;
const APP = "/apps/shop.example.net";
const TITLES = ["Applications and data", "Network", "Observe", "Protect", "Infrastructure"];

/** Saves the fold before any script of the console runs, the way the bar's own button does. */
async function prefer(page: Page, rail?: "folded"): Promise<void> {
  await page.addInitScript((fold: string | null) => {
    if (fold !== null) window.localStorage.setItem("noust.rail", fold);
  }, rail ?? null);
}

async function openAt(page: Page, server: Parameters<typeof signIn>[1], width: number, url: string, rail?: "folded"): Promise<void> {
  await prefer(page, rail);
  await page.setViewportSize({ width, height: 900 });
  await signIn(page, server, url);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await settle(page);
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("not visible");
  return box;
}

function expectSameBox(actual: Box, expected: Box): void {
  for (const key of ["x", "y", "width", "height"] as const) expect(Math.abs(actual[key] - expected[key]), key).toBeLessThanOrEqual(1);
}

const mainBar = (page: Page) => page.getByRole("navigation", { name: "Main" });
/** The title of a group of areas: a paragraph, since "Network" is the name of a group and of an area. */
const groupTitle = (scope: Locator, title: string) => scope.locator("p").getByText(title, { exact: true });
const column = (page: Page, name: string) => page.getByRole("navigation", { name });

/** Whether the page scrolls sideways: it never may, at 390px or at any other (L-4). */
async function overflowsSideways(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
}

/** The transitions the raised piece of a list runs, by property, with the duration and the curve they were given. */
async function transitionsOf(piece: Locator): Promise<{ property: string; duration: number; easing: string }[]> {
  return piece.evaluate((element) =>
    element
      .getAnimations()
      .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
      .map((animation) => ({
        property: animation.transitionProperty,
        duration: Number(animation.effect?.getTiming().duration ?? 0),
        easing: animation.effect?.getTiming().easing ?? "",
      })),
  );
}

/**
 * Presses `target` and reads the transitions of the piece of its list in the same task of the page, one
 * frame later: a round trip to the test takes longer than the 200ms of the slide on a busy machine.
 */
async function pressAndRead(target: Locator, listSelector: string): Promise<{ property: string; duration: number; easing: string }[]> {
  return target.evaluate(async (element, selector) => {
    (element as HTMLElement).click();
    const piece = element.closest(selector)?.querySelector('[data-slot="sliding-indicator"]');
    const running = (): CSSTransition[] => (piece?.getAnimations() ?? []).filter((animation): animation is CSSTransition => animation instanceof CSSTransition);
    // The route changes when its page has loaded, a few frames later at most: the piece moves in the frame after that.
    for (let frame = 0; frame < 90 && running().length === 0; frame += 1) await new Promise((resolve) => requestAnimationFrame(resolve));
    return running().map((animation) => ({
      property: animation.transitionProperty,
      duration: Number(animation.effect?.getTiming().duration ?? 0),
      easing: animation.effect?.getTiming().easing ?? "",
    }));
  }, listSelector);
}

/** What the two layers of the column say about their movement, read in the frames after `target` is pressed. */
async function slideOf(target: Locator): Promise<{ enter: string; exit: string; enterName: string; exitName: string } | null> {
  return target.evaluate(async (element) => {
    (element as HTMLElement).click();
    for (let frame = 0; frame < 120; frame += 1) {
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const leaving = document.querySelector('[data-frame="column"] [data-layer="leaving"]');
      const current = document.querySelector('[data-frame="column"] [data-layer="current"]');
      if (leaving !== null && current !== null && getComputedStyle(current).animationName !== "none") {
        const entering = getComputedStyle(current);
        const exiting = getComputedStyle(leaving);
        return { enter: entering.animationDuration, exit: exiting.animationDuration, enterName: entering.animationName, exitName: exiting.animationName };
      }
    }
    return null;
  });
}

/** The folded bar of a window under 1440 px: 68px, opened over the page by its button without moving it. */
async function foldedBarOpensOverThePage(page: Page): Promise<void> {
  const rail = page.locator('[data-frame="rail"]');
  expect(Math.round((await boxOf(rail)).width)).toBe(68);
  const before = await boxOf(page.getByRole("main"));
  await page.getByRole("button", { name: "Unfold the bar" }).click();
  const close = page.getByRole("button", { name: "Close" });
  await expect(close).toHaveAttribute("aria-expanded", "true");
  // Over the page: the bar is 208px wide and the page did not move.
  expect(Math.round((await boxOf(rail.locator("nav"))).width)).toBeGreaterThanOrEqual(180);
  expectSameBox(await boxOf(page.getByRole("main")), before);
  await expect(page.locator('[data-frame="veil"]')).toBeVisible();
  await expect(groupTitle(mainBar(page), "Network")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the bar opened over the page");
  await page.keyboard.press("Escape");
  await expect(page.locator('[data-frame="veil"]')).toHaveCount(0);
  await page.getByRole("button", { name: "Unfold the bar" }).click();
  await page.mouse.click(1000, 500);
  await expect(page.locator('[data-frame="veil"]')).toHaveCount(0);
  // The column is there beside the folded bar: both fit.
  await expect(column(page, "Application sections")).toBeVisible();
  expect(await overflowsSideways(page)).toBe(false);
}

test.describe("the main bar and the column", () => {
  test("at 1440 px has the bar unfolded with its names in titled groups, and a column on Home and on an application", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    const bar = mainBar(page);
    await expect(bar.getByRole("link", { name: "Applications" })).toBeVisible();
    // Unfolded: 208px, the names are text, and each group has its title and its joint.
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(208);
    await expect(bar.getByText("Applications", { exact: true })).toBeVisible();
    for (const title of TITLES) await expect(groupTitle(bar, title)).toBeVisible();
    // Home has a column too: the content does not jump from one side to the other between two areas.
    await expect(page.locator('[data-frame="column"]')).toHaveCount(1);
    expect(Math.round((await boxOf(page.locator('[data-frame="column"]'))).width)).toBe(256);
    // Home's own list is in it: the panels, the one that comes with the console open.
    await expect(column(page, "Panels").getByRole("link", { name: /This server/ })).toHaveAttribute("aria-current", "page");
    expect(await overflowsSideways(page)).toBe(false);
    await expectNoA11yViolations(page, "Home, the frame at 1440");

    await page.goto(`${APP}/deployments`);
    await expect(page.getByRole("heading", { level: 1, name: "shop.example.net" })).toBeVisible();
    const sections = column(page, "Application sections");
    await expect(sections).toBeVisible();
    expect(Math.round((await boxOf(page.locator('[data-frame="column"]'))).width)).toBe(256);
    for (const group of ["Use", "Configure", "Fix", "Move elsewhere", "Danger"]) await expect(sections.getByText(group, { exact: true })).toBeVisible();
    await expect(sections.getByRole("link", { name: "Deployments" })).toHaveAttribute("aria-current", "page");
    // The sections are in the column, so the page does not draw them again.
    await expect(page.getByRole("navigation", { name: "Application sections" })).toHaveCount(1);
    // The breadcrumbs are the top bar's, named by the application.
    const crumbs = page.getByRole("banner").getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumbs.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText("Deployments");
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application, the frame at 1440");
  });

  test("at 1920 px keeps both bars open and the page in its own column, with nothing scrolling sideways", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1920, "/apps");
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(208);
    expect(Math.round((await boxOf(page.locator('[data-frame="column"]'))).width)).toBe(256);
    await expect(column(page, "Applications sections")).toBeVisible();
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "Applications, the frame at 1920");
  });

  test("gives every area a column with its own sections, and none leaves it", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/network");
    const network = column(page, "Network sections");
    await expect(network.getByRole("link", { name: "Published ports" })).toHaveAttribute("aria-current", "page");
    for (const name of ["Tunnels", "Firewall", "Listening ports", "Interfaces"]) await expect(network.getByRole("link", { name })).toBeVisible();
    await network.getByRole("link", { name: "Firewall" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Firewall" })).toBeVisible();
    await expect(network.getByRole("link", { name: "Firewall" })).toHaveAttribute("aria-current", "page");
    // The area is still Network in the bar, and the column is the same one.
    await expect(mainBar(page).getByRole("link", { name: "Network" })).toHaveAttribute("aria-current", "page");
    await expect(page.locator('[data-frame="column"]')).toHaveCount(1);
    await page.goto("/security");
    const security = column(page, "Security sections");
    for (const name of ["Checks", "SSH", "Brute force"]) await expect(security.getByRole("link", { name })).toBeVisible();
    await page.goto("/logs");
    await column(page, "Logs sections").getByRole("link", { name: "System" }).click();
    await expect(page).toHaveURL(/source=system/);
    await expect(column(page, "Logs sections").getByRole("link", { name: "System" })).toHaveAttribute("aria-current", "page");
  });

  test("folds to icons by a button that says what it does, keeps the choice, and names each icon on focus", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    const fold = page.getByRole("button", { name: "Fold the bar" });
    await expect(fold).toHaveAttribute("aria-expanded", "true");
    await fold.click();
    await expect(page.getByRole("button", { name: "Unfold the bar" })).toHaveAttribute("aria-expanded", "false");
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(68);
    // Folded, a group is its joint alone; its title stays for a screen reader.
    await expect(groupTitle(mainBar(page), "Network")).toHaveClass(/sr-only/);
    await expect(mainBar(page).getByRole("list", { name: "Network" })).toBeVisible();
    // The icon is a link with its name, and says it in a tooltip when the keyboard reaches it.
    await mainBar(page).getByRole("link", { name: "Home" }).focus();
    await page.keyboard.press("Tab");
    await expect(mainBar(page).getByRole("link", { name: /^Applications/ })).toBeFocused();
    // The tooltip is the popup of the overlay layer, apart from the link whose name it says.
    await expect(page.locator(".z-overlay", { hasText: "Applications" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the bar folded to icons");
    await page.reload();
    await expect(page.getByRole("button", { name: "Unfold the bar" })).toBeVisible();
  });

  for (const width of [1366, 1280, 1024]) {
    test(`at ${String(width)} px is folded, opens over the page without pushing it, and closes by Escape or a press outside`, async ({ page, consoleServer }) => {
      await openAt(page, consoleServer, width, `${APP}/logs`);
      await foldedBarOpensOverThePage(page);
    });
  }

  test("at 1280 px keeps the column beside the folded bar and the page in what is left, with axe watching", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1280, `${APP}/logs`);
    await expect(column(page, "Application sections")).toBeVisible();
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(68);
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application, the frame at 1280");
  });

  test("offers no choice of bar: the account menu has the theme, the language and the width, and nothing about bars", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    await page.getByRole("button", { name: /Session and preferences/ }).click();
    await expect(page.getByText("Page width", { exact: true })).toBeVisible();
    await expect(page.getByRole("radiogroup", { name: "Navigation" })).toHaveCount(0);
    await expect(page.getByText("One bar", { exact: true })).toHaveCount(0);
    await expect(mainBar(page).getByText("What runs")).toHaveCount(0);
  });

  test("follows the keyboard: tab reaches the fold button after the areas, with the focus ring on it", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    await page.getByRole("link", { name: "Skip to content" }).focus();
    const fold = page.getByRole("button", { name: "Fold the bar" });
    await mainBar(page).getByRole("link", { name: "Settings" }).focus();
    await page.keyboard.press("Tab");
    await expect(fold).toBeFocused();
    await expect(fold).toHaveAttribute("aria-controls", /.+/);
  });

  test("keeps the focus on a section of the column when it is chosen, and the column is reached by Tab after the bar", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, `${APP}/deployments`);
    const logs = column(page, "Application sections").getByRole("link", { name: "Logs" });
    await logs.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 1, name: "shop.example.net" })).toBeVisible();
    await expect(logs).toBeFocused();
    await expect(logs).toHaveAttribute("aria-current", "page");
  });
});

test.describe("entering a resource", () => {
  test("slides the column to the application's sections, decelerating in and accelerating out in less time, and back", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/apps");
    const forward = await slideOf(page.getByRole("link", { name: "shop.example.net" }).first());
    expect(forward).toEqual({ enter: "0.24s", exit: "0.14s", enterName: "column-in-forward", exitName: "column-out-forward" });
    await expect(column(page, "Application sections")).toBeVisible();
    // The leaving list is gone after the slide: the column never holds two at rest.
    await expect(page.locator('[data-frame="column"] [data-layer="leaving"]')).toHaveCount(0);
    // The head says where the way back leads and has the switch to the other applications.
    const column1 = page.locator('[data-frame="column"]');
    await expect(column1.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(column1.getByRole("button", { name: "Switch application" })).toBeVisible();

    const back = await slideOf(column1.getByRole("link", { name: "Applications" }));
    expect(back).toEqual({ enter: "0.24s", exit: "0.14s", enterName: "column-in-back", exitName: "column-out-back" });
    await expect(column(page, "Applications sections")).toBeVisible();
    await expect(page.locator('[data-frame="column"] [data-layer="leaving"]')).toHaveCount(0);
  });

  test("is instant with reduced motion: the new list is there at once and the old one never lingers", async ({ page, consoleServer }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openAt(page, consoleServer, 1440, "/apps");
    await page.getByRole("link", { name: "shop.example.net" }).first().click();
    await expect(column(page, "Application sections")).toBeVisible();
    await expect(page.locator('[data-frame="column"] [data-layer="leaving"]')).toHaveCount(0);
    expect(await page.locator('[data-frame="column"] [data-layer="current"]').evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  });

  test("jumps to another application without going back to the list, in the same section", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, `${APP}/logs`);
    await page.getByRole("button", { name: "Switch application" }).click();
    const other = page.getByRole("menuitem").filter({ hasNotText: "shop.example.net" }).first();
    const name = (await other.textContent())?.trim() ?? "";
    await other.click();
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/apps/${name.replace(/\./g, "\\.")}/logs`));
  });
});

test.describe("a phone", () => {
  test("has four areas at the foot and every area in its group in the sheet More opens, with the account, and nothing scrolls sideways", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 390, "/");
    const foot = page.getByRole("navigation", { name: "Areas" });
    await expect(foot).toBeVisible();
    await expect(foot.getByRole("link")).toHaveCount(4);
    await expect(page.locator('[data-frame="rail"]')).toHaveCount(0);
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "Home on a phone");
    await foot.getByRole("button", { name: "More" }).click();
    const sheet = page.getByRole("dialog", { name: "All areas" });
    for (const title of TITLES) await expect(groupTitle(sheet, title)).toBeVisible();
    await expect(sheet.getByRole("link", { name: "Catalog" })).toBeVisible();
    await expect(sheet.getByRole("button", { name: "Your account" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the sheet of all the areas");
    await sheet.getByRole("link", { name: "Backups" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(foot.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current", "page");
  });

  test("draws the column as a selector at the top of the page that opens a sheet with the same list, never tabs", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 390, `${APP}/deployments`);
    const selector = page.getByRole("main").getByRole("button", { name: /Deployments/ });
    await expect(selector).toBeVisible();
    // No strip of sections in the page: they are behind the selector.
    await expect(page.getByRole("navigation", { name: "Application sections" })).toHaveCount(0);
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application on a phone");
    await selector.click();
    const sheet = page.getByRole("dialog");
    const sections = sheet.getByRole("navigation", { name: "Application sections" });
    await expect(sections).toBeVisible();
    for (const group of ["Use", "Configure", "Fix", "Move elsewhere", "Danger"]) await expect(sections.getByText(group, { exact: true })).toBeVisible();
    await expect(sections.getByRole("link", { name: "Deployments" })).toHaveAttribute("aria-current", "page");
    await settle(page);
    await expectNoA11yViolations(page, "the sheet of an application's sections");
    await sections.getByRole("link", { name: "Logs" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("main").getByRole("button", { name: /Logs/ })).toBeVisible();
  });

  test("has the selector on an area that is one page too, and the account in the sheet More opens", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 390, "/logs");
    await expect(page.getByRole("main").getByRole("button", { name: /Applications/ })).toBeVisible();
    await page.getByRole("navigation", { name: "Areas" }).getByRole("button", { name: "More" }).click();
    await page.getByRole("dialog", { name: "All areas" }).getByRole("button", { name: "Your account" }).click();
    const session = page.getByRole("dialog", { name: "Session" });
    await expect(session.getByRole("button", { name: "Sign out" })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the session on a phone");
  });
});

test.describe("the raised piece of the current item", () => {
  test("does not slide when the page arrives: the current area already has its piece", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/apps");
    const bar = mainBar(page);
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    await expect(piece).toHaveAttribute("data-state", "visible");
    expect(await transitionsOf(piece)).toEqual([]);
    expectSameBox(await boxOf(piece), await boxOf(bar.getByRole("link", { name: "Applications" })));
  });

  test("slides to the area that is picked, in the duration and by the curve of the tokens, and ends on its box", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/apps");
    const bar = mainBar(page);
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    const target = bar.getByRole("link", { name: "Databases" });
    const running = await pressAndRead(target, "nav");
    const moving = running.filter((entry) => ["translate", "width", "height"].includes(entry.property));
    expect(moving.map((entry) => entry.property)).toContain("translate");
    for (const entry of moving) {
      expect(entry.duration).toBe(200);
      expect(entry.easing).toBe("cubic-bezier(0.4, 0, 0.2, 1)");
    }
    expect(running.map((entry) => entry.property)).not.toContain("scale");
    await piece.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
    expectSameBox(await boxOf(piece), await boxOf(target));
  });

  test("slides to the section that is picked in the second column, which has its own piece", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, `${APP}/deployments`);
    const sections = column(page, "Application sections");
    const piece = sections.locator('[data-slot="sliding-indicator"]');
    expect(await transitionsOf(piece)).toEqual([]);
    const target = sections.getByRole("link", { name: "Logs" });
    const running = await pressAndRead(target, "nav");
    expect(running.find((entry) => entry.property === "translate")?.duration).toBe(200);
    await piece.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    expectSameBox(await boxOf(piece), await boxOf(target));
    // The bar beside it did not move: the area is the same.
    expect(await transitionsOf(mainBar(page).locator('[data-slot="sliding-indicator"]'))).toEqual([]);
  });

  test("slides with the browser's back button too", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/apps");
    const bar = mainBar(page);
    await bar.getByRole("link", { name: "Databases" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    await piece.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    await page.evaluate(() => {
      window.history.back();
    });
    await expect.poll(async () => (await transitionsOf(piece)).some((entry) => entry.property === "translate")).toBe(true);
    await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
    await piece.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    expectSameBox(await boxOf(piece), await boxOf(bar.getByRole("link", { name: "Applications" })));
  });

  test("does not slide when the bar folds: it takes the width of the icon at once", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/apps");
    const bar = mainBar(page);
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    await page.getByRole("button", { name: "Fold the bar" }).click();
    await expect.poll(async () => (await boxOf(piece)).width).toBeLessThan(60);
    expect(await transitionsOf(piece)).toEqual([]);
    expectSameBox(await boxOf(piece), await boxOf(bar.getByRole("link", { name: "Applications" })));
  });

  test("is instant with reduced motion: no transition runs and it is on the area at once", async ({ page, consoleServer }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openAt(page, consoleServer, 1440, "/apps");
    const bar = mainBar(page);
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    const target = bar.getByRole("link", { name: "Databases" });
    await target.click();
    await expect(page.getByRole("heading", { level: 1, name: "Databases" })).toBeVisible();
    expect(await transitionsOf(piece)).toEqual([]);
    expectSameBox(await boxOf(piece), await boxOf(target));
  });

  test("the trail of a deployment's page is read whole at 1440 and at 1366, beside the machine's name and the search", async ({ page, consoleServer }) => {
    // The room of the bar is the page's column, with an application's second column taking 264px of it: the search box and
    // the theme give way to the trail, not the other way round. The domain of the video's recording, as long as the seed has.
    const domain = "clientes.example.com";
    await openAt(page, consoleServer, 1440, `/apps/${domain}`);
    const newest = ((await (await page.request.get(`/api/deployments?domain=${domain}&limit=1`)).json()) as { items: { id: number }[] }).items[0];
    if (newest === undefined) throw new Error(`the seed has no deployment of ${domain}`);
    for (const width of [1440, 1366]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/apps/${domain}/deployments/${String(newest.id)}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      const crumbs = page.getByRole("banner").getByRole("navigation", { name: "Breadcrumb" });
      await expect(crumbs.getByRole("link", { name: "Deployments" })).toBeVisible();
      // No step of the trail is cut: every one shows the whole of its text.
      const cut = await crumbs.locator("li a, li span").evaluateAll((nodes) =>
        nodes.filter((node) => node instanceof HTMLElement && node.scrollWidth > node.clientWidth + 1).map((node) => node.textContent),
      );
      expect(cut, `the trail is cut at ${String(width)}px`).toEqual([]);
      // And the machine's name is still read whole beside it.
      const name = page.getByRole("banner").locator('a[href="/server"]').first();
      await expect(name).toBeVisible();
      expect((await name.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(72);
    }
  });

  test("a long trail gives way before the machine's name does", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1280, "/apps/tienda.example.org/access");
    const name = page.getByRole("banner").locator('a[href="/server"]').first();
    await expect(name).toBeVisible();
    // The strip clips what does not fit from its right edge: the link must end inside it, and be wide enough to read.
    const { link, strip } = await name.evaluate((anchor) => {
      const clip = anchor.closest("div.overflow-hidden") ?? anchor.parentElement ?? anchor;
      const own = anchor.getBoundingClientRect();
      const around = clip.getBoundingClientRect();
      return { link: { right: own.right, width: own.width }, strip: { right: around.right } };
    });
    expect(link.width).toBeGreaterThanOrEqual(72);
    expect(link.right).toBeLessThanOrEqual(strip.right + 0.5);
  });

  test("the frame at its widths and three frames of the slide, for the report", async ({ page, consoleServer }) => {
    test.skip(FRAMES === undefined, "NOUST_FRAME_FRAMES names the directory the pictures are written to.");
    const theme = test.info().project.name;
    const out = (name: string): string => path.join(FRAMES ?? ".", `${name}-${theme}.png`);

    await openAt(page, consoleServer, 1440, "/");
    await page.screenshot({ path: out("inicio") });
    await page.goto(APP);
    await settle(page);
    await page.screenshot({ path: out("aplicacion") });
    await page.goto("/network");
    await settle(page);
    await page.screenshot({ path: out("red") });
    await page.goto("/settings/tokens");
    await settle(page);
    await page.screenshot({ path: out("ajustes") });
    await page.getByRole("button", { name: "Fold the bar" }).click();
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: out("inicio-plegada") });
    await page.getByRole("button", { name: "Unfold the bar" }).click();

    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto("/apps");
    await settle(page);
    await page.screenshot({ path: out("aplicaciones-1920") });

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(APP);
    await settle(page);
    await page.screenshot({ path: out("aplicacion-1280") });
    await page.getByRole("button", { name: "Unfold the bar" }).click();
    await page.screenshot({ path: out("aplicacion-1280-abierta") });
    await page.keyboard.press("Escape");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: out("inicio-390") });
    await page.goto(`${APP}/deployments`);
    await settle(page);
    await page.screenshot({ path: out("aplicacion-390") });
    await page.getByRole("main").getByRole("button", { name: /Deployments/ }).click();
    await settle(page);
    await page.screenshot({ path: out("aplicacion-390-hoja") });
    await page.keyboard.press("Escape");
    await page.getByRole("navigation", { name: "Areas" }).getByRole("button", { name: "More" }).click();
    await settle(page);
    await page.screenshot({ path: out("mas-areas-390") });
    await page.keyboard.press("Escape");

    // Three frames of the slide in the main bar: the click and the pause are one task of the page, so the
    // transition cannot run to its end in the time a round trip takes.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/apps");
    await settle(page);
    const bar = mainBar(page);
    const piece = bar.locator('[data-slot="sliding-indicator"]');
    await bar.getByRole("link", { name: "Databases" }).evaluate(async (link) => {
      (link as HTMLElement).click();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const moving = link.closest("nav")?.querySelector('[data-slot="sliding-indicator"]');
      for (const animation of moving?.getAnimations() ?? []) animation.pause();
    });
    const clip = await boxOf(page.locator('[data-frame="rail"]'));
    for (const [name, time] of [
      ["inicio", 0],
      ["mitad", 100],
      ["final", 200],
    ] as const) {
      await piece.evaluate((element, at) => {
        for (const animation of element.getAnimations()) animation.currentTime = at;
      }, time);
      await page.screenshot({ path: out(`deslizamiento-${name}`), clip: { x: clip.x, y: clip.y, width: clip.width, height: Math.min(clip.height, 620) } });
    }
  });
});

test.describe("a veil", () => {
  /** The colour a token paints, as the browser computes it (a probe, since the token's own text is a light-dark() of a mix). */
  async function paint(page: Page, token: string): Promise<string> {
    return page.evaluate((name) => {
      const probe = document.createElement("div");
      probe.style.background = `var(${name})`;
      document.body.append(probe);
      const colour = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return colour;
    }, token);
  }

  const ground = (page: Page) => page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);

  for (const width of [1440, 1920]) {
    test(`at ${String(width)} px covers the main bar and the column as it covers the page, and gives the ground its tone`, async ({ page, consoleServer }) => {
      await openAt(page, consoleServer, width, "/apps");
      const plain = await paint(page, "--bg");
      const veiled = await paint(page, "--bg-veiled");
      expect(veiled, "the veiled ground is another tone than the plain one").not.toBe(plain);
      expect(await ground(page)).toBe(plain);

      await page.keyboard.press("Control+k");
      const veil = page.locator(".bg-backdrop");
      await expect(page.getByRole("dialog")).toBeVisible();
      await expect(veil).toBeVisible();
      await settle(page);

      // The veil is the whole window but for the room the scrollbar keeps (scrollbar-gutter: stable), which it does
      // not reach and which the ground fills below: nothing of the frame is left out of it.
      const viewport = page.viewportSize();
      const box = await boxOf(veil);
      expect([box.x, box.y, Math.round(box.height)]).toEqual([0, 0, viewport?.height ?? 0]);
      expect((viewport?.width ?? 0) - Math.round(box.width), "all the veil leaves out is the scrollbar's room").toBeLessThanOrEqual(20);
      // And it is what is on top where the bars are: the main bar, the column and the top bar are under it, not over it.
      const rail = await boxOf(page.locator('[data-frame="rail"]'));
      const sections = await boxOf(page.locator('[data-frame="column"]'));
      // Not by role: with a dialog open the page behind it is hidden from the accessibility tree, which is its job.
      const top = await boxOf(page.locator("header").first());
      const points = {
        bar: { x: rail.x + rail.width / 2, y: 450, within: '[data-frame="rail"]' },
        column: { x: sections.x + sections.width / 2, y: 450, within: '[data-frame="column"]' },
        top: { x: top.x + top.width / 2, y: top.y + top.height / 2, within: "header" },
      };
      for (const [name, point] of Object.entries(points)) {
        // From the top down at that point: the veil comes before anything of the bar, which is under it. (The dialog's own
        // transparent layer is above both, so the first element is not the veil's.)
        const order = await page.evaluate((at) => {
          const stack = document.elementsFromPoint(at.x, at.y);
          const veilAt = stack.findIndex((element) => element.classList.contains("bg-backdrop"));
          const barAt = stack.findIndex((element) => element.closest(at.within) !== null);
          return { veilAt, barAt };
        }, point);
        expect(order.veilAt, `the veil is at the ${name}`).toBeGreaterThanOrEqual(0);
        expect(order.barAt, `the ${name} is at its point`).toBeGreaterThanOrEqual(0);
        expect(order.veilAt, `the veil is over the ${name}`).toBeLessThan(order.barAt);
      }
      // The ground takes the veil's tone, so the scrollbar's room is not a light strip beside the veiled window.
      await expect.poll(() => ground(page)).toBe(veiled);
      if (FRAMES !== undefined) await page.screenshot({ path: path.join(FRAMES, `velo-${String(width)}-${test.info().project.name}.png`) });

      await page.keyboard.press("Escape");
      await expect(veil).toHaveCount(0);
      await expect.poll(() => ground(page)).toBe(plain);
    });
  }
});
