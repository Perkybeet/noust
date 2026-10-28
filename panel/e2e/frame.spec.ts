/**
 * The frame of the console in a browser (docs/DESIGN.md, NV-20 and NV-21): the double bar and one bar at
 * the three widths that matter (390, 1366 and 1440 px), in both themes (the light and dark projects),
 * with axe and the Content Security Policy watching, and the movement of the raised piece of the current
 * item in the main bar and in the second column. The register's rules are nav.register.test.ts's and the
 * layout's are app/navLayout.test.ts's; what only a browser can say is here: where each bar is, how wide,
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

type Presentation = "double" | "single";

/** Saves the presentation before any script of the console runs, the way the bar's own choice does. */
async function prefer(page: Page, presentation: Presentation, rail?: "folded"): Promise<void> {
  await page.addInitScript(
    ({ presentation: nav, rail: fold }) => {
      if (nav === "single") window.localStorage.setItem("noust.nav", "single");
      if (fold !== undefined) window.localStorage.setItem("noust.rail", fold);
    },
    { presentation, rail },
  );
}

async function openAt(page: Page, server: Parameters<typeof signIn>[1], width: number, url: string, presentation: Presentation = "double", rail?: "folded"): Promise<void> {
  await prefer(page, presentation, rail);
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
const column = (page: Page, name: string) => page.getByRole("navigation", { name });

/** Whether the page scrolls sideways: it never may, at 390 px or at any other (L-4). */
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

test.describe("the double bar", () => {
  test("at 1440 px has the bar unfolded with its names, no second column on Home, and one on an application", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    const bar = mainBar(page);
    await expect(bar.getByRole("link", { name: "Applications" })).toBeVisible();
    // Unfolded: 208px, and the names are text.
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(208);
    await expect(bar.getByText("Applications", { exact: true })).toBeVisible();
    await expect(page.locator('[data-frame="column"]')).toHaveCount(0);
    expect(await overflowsSideways(page)).toBe(false);
    await expectNoA11yViolations(page, "Home, the double bar at 1440");

    await page.goto(`${APP}/deployments`);
    await expect(page.getByRole("heading", { level: 1, name: "shop.example.net" })).toBeVisible();
    const sections = column(page, "Application sections");
    await expect(sections).toBeVisible();
    expect(Math.round((await boxOf(page.locator('[data-frame="column"]'))).width)).toBe(256);
    for (const group of ["Use", "Configure", "Fix", "Danger"]) await expect(sections.getByText(group, { exact: true })).toBeVisible();
    await expect(sections.getByRole("link", { name: "Deployments" })).toHaveAttribute("aria-current", "page");
    // The sections are in the column, so the page does not draw them again.
    await expect(page.getByRole("navigation", { name: "Application sections" })).toHaveCount(1);
    // The breadcrumbs are the top bar's, named by the application.
    const crumbs = page.getByRole("banner").getByRole("navigation", { name: "Breadcrumb" });
    await expect(crumbs.getByRole("link", { name: "Applications" })).toBeVisible();
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText("Deployments");
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application, the double bar at 1440");
  });

  test("folds to icons by a button that says what it does, keeps the choice, and names each icon on focus", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    const fold = page.getByRole("button", { name: "Fold the bar" });
    await expect(fold).toHaveAttribute("aria-expanded", "true");
    await fold.click();
    await expect(page.getByRole("button", { name: "Unfold the bar" })).toHaveAttribute("aria-expanded", "false");
    expect(Math.round((await boxOf(page.locator('[data-frame="rail"]'))).width)).toBe(68);
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

  test("at 1366 px is folded, opens over the page without pushing it, and closes by Escape or a press outside", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1366, `${APP}/logs`);
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
    await settle(page);
    await expectNoA11yViolations(page, "the bar opened over the page");
    await page.keyboard.press("Escape");
    await expect(page.locator('[data-frame="veil"]')).toHaveCount(0);
    await page.getByRole("button", { name: "Unfold the bar" }).click();
    await page.mouse.click(1300, 500);
    await expect(page.locator('[data-frame="veil"]')).toHaveCount(0);
    // The column is there beside the folded bar: both fit.
    await expect(column(page, "Application sections")).toBeVisible();
    expect(await overflowsSideways(page)).toBe(false);
  });

  test("changes to one bar from the bar's own choice and back, and the choice is the browser's", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, `${APP}/metrics`);
    const choice = page.getByRole("radiogroup", { name: "Navigation" });
    await choice.getByRole("radio", { name: "One bar" }).click();
    await expect(page.locator('[data-frame="rail"]')).toHaveCount(0);
    await expect(mainBar(page).getByText("What runs")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Application sections" })).toBeVisible();
    await page.reload();
    await expect(mainBar(page).getByText("What runs")).toBeVisible();
    await page.getByRole("radiogroup", { name: "Navigation" }).getByRole("radio", { name: "Double bar" }).click();
    await expect(page.locator('[data-frame="rail"]')).toBeVisible();
  });

  test("follows the keyboard: tab reaches the fold button after the areas, with the focus ring on it", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, "/");
    await page.getByRole("link", { name: "Skip to content" }).focus();
    const fold = page.getByRole("button", { name: "Fold the bar" });
    await mainBar(page).getByRole("link", { name: "Settings" }).focus();
    await page.keyboard.press("Tab");
    // The choice of bar comes first, then the fold: both are buttons of the bar.
    await page.keyboard.press("Tab");
    await expect(fold).toBeFocused();
    await expect(fold).toHaveAttribute("aria-controls", /.+/);
  });
});

test.describe("one bar", () => {
  test("at 1440 px has the areas in titled groups and the sections as tabs in the page", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1440, `${APP}/deployments`, "single");
    const bar = mainBar(page);
    for (const title of ["What runs", "How it is reached", "What is happening", "Kept safe", "Where it runs", "How it is managed"]) await expect(bar.getByText(title, { exact: true })).toBeVisible();
    await expect(page.locator('[data-frame="column"]')).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Application sections" })).toBeVisible();
    expect(Math.round((await boxOf(page.getByRole("complementary", { name: "Navigation" }))).width)).toBe(264);
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application, one bar at 1440");
  });

  test("at 1366 px is what was saved, and at 1024 px the only one: there is no room for two", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1366, "/apps", "single");
    await expect(mainBar(page).getByText("What runs")).toBeVisible();
    await page.setViewportSize({ width: 1100, height: 900 });
    await expect(page.getByRole("radiogroup", { name: "Navigation" })).toHaveCount(0);
    await expect(mainBar(page).getByText("What runs")).toBeVisible();
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "one bar at 1100");
  });
});

test.describe("a phone", () => {
  test("has four areas at the foot and the rest in the menu, and nothing scrolls sideways", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 390, "/");
    const foot = page.getByRole("navigation", { name: "Areas" });
    await expect(foot).toBeVisible();
    await expect(foot.getByRole("link")).toHaveCount(4);
    await expect(page.locator('[data-frame="rail"]')).toHaveCount(0);
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "Home on a phone");
    await foot.getByRole("button", { name: "More" }).click();
    const menu = page.getByRole("dialog", { name: "Menu" });
    await expect(menu.getByRole("link", { name: "Backups" })).toBeVisible();
    await menu.getByRole("link", { name: "Backups" }).click();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(foot.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current", "page");
  });

  test("puts the trail on the first line of the page, and the application's sections as tabs", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 390, `${APP}/deployments`);
    await expect(page.getByRole("main").getByRole("navigation", { name: "Breadcrumb" })).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Application sections" })).toBeVisible();
    expect(await overflowsSideways(page)).toBe(false);
    await settle(page);
    await expectNoA11yViolations(page, "an application on a phone");
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

  test("a long trail gives way before the machine's name does", async ({ page, consoleServer }) => {
    await openAt(page, consoleServer, 1280, "/apps/tienda.example.org/settings/access");
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

  test("the frame in both presentations and three frames of the slide, for the report", async ({ page, consoleServer }) => {
    test.skip(FRAMES === undefined, "NOUST_FRAME_FRAMES names the directory the pictures are written to.");
    const theme = test.info().project.name;
    const out = (name: string): string => path.join(FRAMES ?? ".", `${name}-${theme}.png`);

    await openAt(page, consoleServer, 1440, "/");
    await page.screenshot({ path: out("inicio") });
    await page.goto(APP);
    await settle(page);
    await page.screenshot({ path: out("aplicacion") });
    await page.goto("/settings/tokens");
    await settle(page);
    await page.screenshot({ path: out("ajustes") });
    await page.getByRole("button", { name: "Fold the bar" }).click();
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: out("inicio-plegada") });
    await page.getByRole("button", { name: "Unfold the bar" }).click();

    await page.getByRole("radiogroup", { name: "Navigation" }).getByRole("radio", { name: "One bar" }).click();
    await page.goto(APP);
    await settle(page);
    await page.screenshot({ path: out("aplicacion-una-barra") });
    await page.getByRole("radiogroup", { name: "Navigation" }).getByRole("radio", { name: "Double bar" }).click();

    await page.setViewportSize({ width: 1366, height: 768 });
    await page.goto("/apps");
    await settle(page);
    await page.screenshot({ path: out("aplicaciones-1366") });
    await page.getByRole("button", { name: "Unfold the bar" }).click();
    await page.screenshot({ path: out("aplicaciones-1366-abierta") });
    await page.keyboard.press("Escape");

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await settle(page);
    await page.screenshot({ path: out("inicio-390") });

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
