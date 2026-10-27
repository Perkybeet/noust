/**
 * The sliding indicator in a real browser (docs/DESIGN.md 2.7, M-5): the raised piece of the chosen tab,
 * segment or navigation item slides to the item the person picks, in `--duration-move` and by the standard
 * curve, and ends exactly on the item's box. The unit tests (components/ui/SlidingIndicator.test.tsx) hold
 * the rules; what only a browser can say is held here: that a transition really runs, how long it lasts,
 * where it ends, and that with reduced motion there is none.
 *
 * It reads the gallery (/__design), which only a development build includes (see design-contract.spec.ts):
 *
 *   NODE_ENV=development npx vite build --mode development --outDir /tmp/noust-dev-build
 *   NOUST_E2E_STATIC=/tmp/noust-dev-build npx playwright test indicator
 *
 * With NOUST_KIT_FRAMES=<directory> it also writes three frames of the movement (the start, the middle and
 * the end of the slide), with the transition held at a time by the Web Animations API.
 */

import path from "node:path";

import type { Locator, Page } from "@playwright/test";

import { expect, settle, signIn, test } from "./fixtures";

const FRAMES = process.env.NOUST_KIT_FRAMES;

async function openGallery(page: Page, server: Parameters<typeof signIn>[1]): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, server);
  await page.goto("/__design");
  const gallery = page.locator('nav[aria-label="Design system"]');
  const present = await gallery
    .waitFor({ state: "attached", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  test.skip(!present, "The gallery is only in a development build (see the header of this file).");
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

/** The transitions the indicator runs, by property, with the duration and the curve they were given. */
async function transitionsOf(indicator: Locator): Promise<{ property: string; duration: number; easing: string }[]> {
  return indicator.evaluate((element) =>
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
 * Presses `target` and reads the transitions of the indicator of its list in the same task of the page, one
 * frame later: a round trip to the test takes longer than the 200ms of the slide on a busy machine, and what
 * is read afterwards would be a transition that has already ended.
 */
async function pressAndRead(target: Locator, indicatorOf: string): Promise<{ property: string; duration: number; easing: string }[]> {
  return target.evaluate(async (element, selector) => {
    (element as HTMLElement).click();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const piece = element.closest(selector)?.querySelector('[data-slot="sliding-indicator"]');
    return (piece?.getAnimations() ?? [])
      .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
      .map((animation) => ({
        property: animation.transitionProperty,
        duration: Number(animation.effect?.getTiming().duration ?? 0),
        easing: animation.effect?.getTiming().easing ?? "",
      }));
  }, indicatorOf);
}

test.describe("the sliding indicator", () => {
  test("does not slide when the page arrives: the chosen tab already has its piece", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer);
    const rail = page.getByRole("tablist", { name: "Application sections" }).first();
    const indicator = rail.locator('[data-slot="sliding-indicator"]');
    await expect(indicator).toHaveAttribute("data-state", "visible");
    expect(await transitionsOf(indicator)).toEqual([]);
    expectSameBox(await boxOf(indicator), await boxOf(rail.getByRole("tab", { name: "Overview" })));
  });

  test("slides to the tab that is picked in the duration and by the curve of the tokens, and ends on its box", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer);
    const rail = page.getByRole("tablist", { name: "Application sections" }).first();
    const indicator = rail.locator('[data-slot="sliding-indicator"]');
    const target = rail.getByRole("tab", { name: "Environment variables" });
    const running = await pressAndRead(target, '[role="tablist"]');

    expect(running.map((entry) => entry.property).sort(), "a move and a change of width").toEqual(expect.arrayContaining(["translate", "width"]));
    for (const entry of running.filter((item) => ["translate", "width"].includes(item.property))) {
      expect(entry.duration).toBe(200);
      expect(entry.easing).toBe("cubic-bezier(0.4, 0, 0.2, 1)");
    }
    // It is only moving: no scale anywhere, which would stretch the rounded corners.
    expect(running.map((entry) => entry.property)).not.toContain("scale");
    expect(running.map((entry) => entry.property)).not.toContain("transform");

    await indicator.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    expectSameBox(await boxOf(indicator), await boxOf(target));
  });

  test("is instant with reduced motion: no transition runs and it is on the tab at once", async ({ page, consoleServer }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await openGallery(page, consoleServer);
    const rail = page.getByRole("tablist", { name: "Application sections" }).first();
    const indicator = rail.locator('[data-slot="sliding-indicator"]');
    const target = rail.getByRole("tab", { name: "Logs" });
    await target.click();
    expect(await transitionsOf(indicator)).toEqual([]);
    expectSameBox(await boxOf(indicator), await boxOf(target));
    const duration = await indicator.evaluate((element) => getComputedStyle(element).transitionDuration);
    expect(duration.split(",").every((part) => part.trim() === "0s")).toBe(true);
  });

  test("follows a vertical list between two groups with the same slide, and ends on the item", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer);
    const nav = page.getByTestId("nav-demo").first();
    const indicator = nav.locator('[data-slot="sliding-indicator"]');
    const target = nav.getByTestId("nav-domains");
    const running = await pressAndRead(target, "nav");
    const move = running.find((entry) => entry.property === "translate");
    expect(move?.duration).toBe(200);
    await indicator.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    expectSameBox(await boxOf(indicator), await boxOf(target));
  });

  test("fits the width of the icon when the bar is folded, without sliding, and is hidden when no item is chosen", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer);
    const nav = page.getByTestId("nav-demo").first();
    const indicator = nav.locator('[data-slot="sliding-indicator"]');
    await nav.getByTestId("nav-apps").click();
    await indicator.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    await page.getByTestId("nav-fold").first().click();
    await expect.poll(async () => (await boxOf(indicator)).width).toBeLessThan(60);
    expect(await transitionsOf(indicator)).toEqual([]);
    expectSameBox(await boxOf(indicator), await boxOf(nav.getByTestId("nav-apps")));
    await page.getByTestId("nav-none").first().click();
    await expect(indicator).toHaveAttribute("data-state", "hidden");
  });

  test("never queues: choosing again before it arrives goes from where it is to the last item", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer);
    const rail = page.getByRole("tablist", { name: "Application sections" }).first();
    const indicator = rail.locator('[data-slot="sliding-indicator"]');
    for (const name of ["Deployments 12", "Logs", "Environment variables", "Overview"]) await rail.getByRole("tab", { name }).click();
    const last = rail.getByRole("tab", { name: "Overview" });
    await indicator.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    expectSameBox(await boxOf(indicator), await boxOf(last));
    expect((await transitionsOf(indicator)).length).toBe(0);
  });

  test("three frames of the movement, for the report", async ({ page, consoleServer }) => {
    test.skip(FRAMES === undefined, "NOUST_KIT_FRAMES names the directory the frames are written to.");
    await openGallery(page, consoleServer);
    await page.locator("#stone-motion").scrollIntoViewIfNeeded();
    for (const theme of ["light", "dark"] as const) {
      // The gallery shows both themes side by side, each pinned: the frames are taken in each.
      const panel = page.locator(`#stone-motion [data-theme="${theme}"]`).first();
      const rail = panel.getByRole("tablist", { name: "Application sections" });
      const indicator = rail.locator('[data-slot="sliding-indicator"]');
      // The click and the pause are one task of the page, so the transition cannot run to its end in the time a
      // round trip takes: it is started by the click and held at once, and each frame is the time it is set to.
      await rail.getByRole("tab", { name: "Environment variables" }).evaluate(async (tab) => {
        (tab as HTMLElement).click();
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const piece = tab.closest('[role="tablist"]')?.querySelector('[data-slot="sliding-indicator"]');
        for (const animation of piece?.getAnimations() ?? []) animation.pause();
      });
      const hold = async (time: number): Promise<void> => {
        await indicator.evaluate((element, at) => {
          for (const animation of element.getAnimations()) animation.currentTime = at;
        }, time);
      };
      const box = await boxOf(rail);
      for (const [name, time] of [
        ["inicio", 0],
        ["mitad", 100],
        ["final", 200],
      ] as const) {
        await hold(time);
        await page.screenshot({
          path: path.join(FRAMES ?? ".", `movimiento-${theme}-${name}.png`),
          clip: { x: Math.max(0, box.x - 16), y: Math.max(0, box.y - 16), width: box.width + 32, height: box.height + 32 },
        });
      }
      // Back to the first tab for the next theme's specimen, with the transition left to finish.
      await indicator.evaluate((element) => {
        for (const animation of element.getAnimations()) animation.finish();
      });
    }
  });
});
