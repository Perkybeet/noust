/**
 * The screens before the console are one slab (4.2, item 108): the logo, a noust drawn from the side and a
 * sentence in a hollow on the left, the form on the right; on a phone the drawing is a band across the top.
 * Against the real backend, at 1920, 1440 and 390 px, in both themes (the light and dark projects), with the
 * strict policy, axe, the keyboard and a real second factor (`console_server.py --totp`).
 *
 * What a person sees is what is measured: where the slab is, which part is beside which, that the drawing
 * is grey stone with a violet boat and nothing else, that no door is wider than its neighbours. A picture
 * of each screen is written to $NOUST_ACCESS_SHOTS when it is set, for a review by eye.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, expectNoA11yViolations, settle, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

const SIZES = [
  { name: "1920", width: 1920, height: 1080 },
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;

const SHOTS = process.env.NOUST_ACCESS_SHOTS;

/** Where the slab is, as a person sees it. */
interface Geometry {
  /** The page the slab is centred in: the screen less the scrollbar's room, which a desktop browser keeps and a phone does not. */
  page: { x: number; y: number; width: number; height: number };
  slab: { x: number; y: number; width: number; height: number };
  identity: { x: number; y: number; width: number; height: number };
  form: { x: number; y: number; width: number; height: number };
  drawing: { x: number; y: number; width: number; height: number };
  viewport: { width: number; height: number };
  overflow: number;
}

async function geometry(page: Page): Promise<Geometry> {
  return page.evaluate(() => {
    const box = (selector: string) => {
      const element = document.querySelector(selector);
      if (element === null) throw new Error(`nothing matches ${selector}`);
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    return {
      page: (() => {
        const parent = document.querySelector("[data-template='auth']")?.parentElement;
        if (parent === null || parent === undefined) throw new Error("the slab has no page");
        const { x, y, width, height } = parent.getBoundingClientRect();
        return { x, y, width, height };
      })(),
      slab: box("[data-template='auth']"),
      identity: box("[data-template='auth'] [data-slot='identity']"),
      form: box("[data-template='auth'] [data-slot='form']"),
      drawing: box("[data-template='auth'] [data-slot='identity'] svg[viewBox='0 0 400 230']"),
      viewport: { width: document.documentElement.clientWidth, height: window.innerHeight },
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
}

async function capture(page: Page, project: string, size: string, name: string): Promise<void> {
  if (SHOTS === undefined) return;
  await settle(page);
  await page.screenshot({ path: path.join(SHOTS, project, `${name}-${size}.png`) });
}

/** The slab says the same thing at every size: this is what wide and narrow each have to be. */
async function expectSlab(page: Page, width: number): Promise<Geometry> {
  const g = await geometry(page);
  expect(g.overflow, "the page scrolls sideways").toBeLessThanOrEqual(0);
  if (width >= 1024) {
    // One slab of 992px in the middle of the screen, the hollow beside the form.
    expect(g.slab.width).toBeGreaterThan(985);
    expect(g.slab.width).toBeLessThanOrEqual(993);
    expect(Math.abs(g.slab.x + g.slab.width / 2 - (g.page.x + g.page.width / 2)), "the slab is not centred").toBeLessThanOrEqual(1);
    expect(g.identity.x + g.identity.width).toBeLessThanOrEqual(g.form.x + 1);
    expect(Math.abs(g.identity.y + g.identity.height / 2 - (g.form.y + g.form.height / 2))).toBeLessThanOrEqual(2);
    // The hollow is the wider half, as drawn (11 parts to 10), and the drawing is read at a size a person can see.
    expect(g.identity.width).toBeGreaterThan(g.form.width);
    expect(g.drawing.width).toBeGreaterThan(300);
    expect(g.slab.y).toBeGreaterThanOrEqual(0);
    expect(g.slab.y + g.slab.height).toBeLessThanOrEqual(g.viewport.height);
  } else {
    // A phone: the slab takes the width, the drawing is a band across its top and the form follows it.
    expect(g.slab.width).toBeGreaterThan(g.page.width - 28);
    expect(g.identity.y + g.identity.height).toBeLessThanOrEqual(g.form.y + 1);
    // The drawing takes the whole width of its hollow: the band is as wide as the hollow less its own padding.
    expect(Math.abs(g.drawing.width - (g.identity.width - 32))).toBeLessThanOrEqual(1);
  }
  return g;
}

/** The number of channels apart the most and least of a colour are: 0 is grey, a violet is far from it. */
function chroma(colour: string): number {
  const channels = colour.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? [];
  return Math.max(...channels) - Math.min(...channels);
}

/** What the browser paints each kind of piece of the drawing with. */
async function paints(page: Page): Promise<{ stones: string[]; hull: string[] }> {
  return page.evaluate(() => {
    const fill = (selector: string) => [...document.querySelectorAll(selector)].map((piece) => getComputedStyle(piece).fill);
    return {
      stones: fill("[data-slot='identity'] svg polygon"),
      hull: fill("[data-slot='identity'] svg path[class*='fill-accent']"),
    };
  });
}

/** What the page asked a server other than its own for. */
function collectForeignRequests(page: Page, origin: string): string[] {
  const foreign: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (url.startsWith("data:") || url.startsWith("blob:")) return;
    if (new URL(url).origin !== origin) foreign.push(url);
  });
  return foreign;
}

async function focused(page: Page): Promise<string> {
  return page.evaluate(() => {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement)) return "";
    const label = element.getAttribute("aria-label") ?? (element as HTMLInputElement).labels?.[0]?.textContent ?? element.textContent;
    return `${element.tagName.toLowerCase()}:${label.trim()}`;
  });
}

for (const size of SIZES) {
  test.describe(`at ${size.name} px`, () => {
    test.use({ viewport: { width: size.width, height: size.height } });

    test("the sign-in is the slab, in this theme, with nothing from outside and nothing of the server", async ({ page, consoleServer }, testInfo) => {
      const project = testInfo.project.name;
      const foreign = collectForeignRequests(page, new URL(consoleServer.url).origin);
      await page.goto("/login");
      await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
      await settle(page);
      await expectSlab(page, size.width);

      // The logo, the sentence (the wide slab's), the licence under the slab.
      await expect(page.locator("[data-slot='identity']").getByRole("img", { name: "Noust" })).toBeVisible();
      const tagline = page.getByText("Your apps and servers, kept safe.");
      if (size.width >= 1024) await expect(tagline).toBeVisible();
      else await expect(tagline).toBeHidden();
      await expect(page.getByRole("contentinfo")).toContainText("Yago López Prado");
      const legal = await page.getByRole("contentinfo").boundingBox();
      const slab = await page.locator("[data-template='auth']").boundingBox();
      expect(legal?.y ?? 0).toBeGreaterThan((slab?.y ?? 0) + (slab?.height ?? 0));

      // The drawing is grey stone with a violet boat, in this theme, and it is the only violet in the hollow.
      const colours = await paints(page);
      expect(colours.stones.length).toBeGreaterThanOrEqual(20);
      for (const fill of colours.stones) expect(chroma(fill), `a stone is ${fill}`).toBeLessThanOrEqual(14);
      expect(colours.hull).toHaveLength(1);
      expect(chroma(colours.hull[0] ?? "")).toBeGreaterThan(60);

      // Nothing of the server before sign-in: not its name, not its version, not the product line of before.
      const text = (await page.locator("body").innerText()).toLowerCase();
      expect(text).not.toContain("noust console");
      expect(text).not.toMatch(/\b\d+\.\d+\.\d+\b/);
      // A strict policy allows no style attribute in the markup, and nothing is fetched from another origin.
      expect(await page.locator("[data-template='auth'] [style]").count()).toBe(0);
      expect(foreign).toEqual([]);

      await capture(page, project, size.name, "login");
      await expectNoA11yViolations(page, `the sign-in at ${size.name}px`);
    });

    test("the doors share the width, open in the form's place and come back, with the keyboard", async ({ page }, testInfo) => {
      await page.goto("/login");
      await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
      const doors = page.getByRole("navigation", { name: "Other ways to sign in" }).getByRole("button");
      await expect(doors.first()).toBeVisible();
      const boxes = await doors.evaluateAll((all) => all.map((door) => door.getBoundingClientRect().width));
      expect(boxes.length).toBeGreaterThanOrEqual(1);
      // Equal doors: none is the main one and none a footnote.
      for (const width of boxes) expect(Math.abs(width - (boxes[0] ?? 0))).toBeLessThanOrEqual(1);
      const form = page.locator("[data-slot='form']");
      const formBox = await form.boundingBox();
      const doorsBox = await page.getByRole("navigation", { name: "Other ways to sign in" }).boundingBox();
      expect((doorsBox?.x ?? 0) + (doorsBox?.width ?? 0)).toBeLessThanOrEqual((formBox?.x ?? 0) + (formBox?.width ?? 0) + 1);

      // Tab reaches every control in the order a person reads them: the name, the password, its help, the button, the doors.
      await page.keyboard.press("Tab");
      expect(await focused(page)).toBe("input:Username or email");
      await page.keyboard.press("Tab");
      expect(await focused(page)).toBe("input:Password");
      await page.keyboard.press("Tab");
      expect(await focused(page)).toBe("button:Forgot your password?");
      await page.keyboard.press("Tab");
      expect(await focused(page)).toBe("button:Sign in");
      // One Tab for each door: the last of them is the access token.
      for (const width of boxes) {
        expect(width).toBeGreaterThan(0);
        await page.keyboard.press("Tab");
      }
      expect(await focused(page)).toBe("button:Access token");

      // Enter opens the token in the form's place, with the way back above the title.
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeVisible();
      await expect(page.getByLabel("Access token")).toBeFocused();
      await expectSlab(page, size.width);
      const back = await page.getByRole("button", { name: "With a password" }).boundingBox();
      const title = await page.getByRole("heading", { level: 1 }).boundingBox();
      expect(back?.y ?? 1e9).toBeLessThan(title?.y ?? 0);
      await capture(page, testInfo.project.name, size.name, "token");

      // And back to the password with the keyboard; the title takes focus, as it does on every screen.
      await page.getByRole("button", { name: "With a password" }).focus();
      await page.keyboard.press("Enter");
      await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeFocused();
      await expect(page.getByLabel("Username or email")).toBeVisible();
    });

    test("a company's door and an account's second step are the same slab", async ({ page }, testInfo) => {
      const project = testInfo.project.name;
      await page.route("**/api/auth/oidc/providers", (route) => route.fulfill({ json: { providers: [{ name: "corp", label: "Acme SSO", protocol: "oidc" }] } }));
      await page.route("**/api/auth/login", (route) => {
        if (route.request().method() !== "POST") return route.continue();
        return route.fulfill({ json: { success: false, expires_in: 0, csrf_token: "", second_factor: { challenge: "c1", expires_in: 300, methods: ["totp", "backup_code"] } } });
      });
      await page.goto("/login");
      const doors = page.getByRole("navigation", { name: "Other ways to sign in" });
      await expect(doors.getByRole("button", { name: "Your company" })).toBeVisible();
      await capture(page, project, size.name, "login-company");
      await doors.getByRole("button", { name: "Your company" }).click();
      await expect(page.getByRole("heading", { level: 1, name: "Sign in with your company" })).toBeVisible();
      await expect(page.getByRole("link", { name: "Sign in with Acme SSO" })).toBeVisible();
      await expectSlab(page, size.width);
      await capture(page, project, size.name, "company");
      await expectNoA11yViolations(page, `the company's way at ${size.name}px`);

      await page.getByRole("button", { name: "With a password" }).click();
      await page.getByLabel("Username or email").fill("marta");
      await page.getByLabel("Password").fill("a long password");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.getByRole("heading", { level: 1, name: "One more step" })).toBeVisible();
      await expect(page.getByLabel("Two-factor code")).toBeFocused();
      await expectSlab(page, size.width);
      await capture(page, project, size.name, "second-factor");
      await expectNoA11yViolations(page, `an account's second step at ${size.name}px`);
    });

    test("the invitation, the new password and the first account are the same slab", async ({ page, consoleServer }, testInfo) => {
      const project = testInfo.project.name;
      await page.goto("/invite");
      await expect(page.getByRole("heading", { level: 1, name: "Accept an invitation" })).toBeVisible();
      await expectSlab(page, size.width);
      await capture(page, project, size.name, "invite");
      await expectNoA11yViolations(page, `the invitation at ${size.name}px`);

      await page.goto("/reset-password");
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectSlab(page, size.width);
      await capture(page, project, size.name, "reset-password");
      await expectNoA11yViolations(page, `the password reset at ${size.name}px`);

      // The first account: reached the way the installer's link reaches it, with the token and its second factor.
      await signInWithToken(page, consoleServer);
      if (new URL(page.url()).pathname !== "/setup") await page.goto("/setup");
      await expect(page.getByRole("heading", { level: 1, name: "Create the administrator" })).toBeVisible();
      await settle(page);
      await expectSlab(page, size.width);
      await capture(page, project, size.name, "first-account");
      await expectNoA11yViolations(page, `the first account at ${size.name}px`);
    });
  });
}

/**
 * The mark of the logo as the browser drew it, compared with its own mirror about its axis, in device pixels.
 * The axis is read off the picture, not off the layout: the browser puts a replaced element on whole device
 * pixels, so a box at 261.5px is painted at 262px, and the middle of the ground line's ink (it runs the whole
 * width of the mark, with round ends) is where the mark really is. `onGrid` says whether that middle is on a
 * pixel's edge or a pixel's centre, which is what lets a pixel have a mirror pixel at all.
 *
 * `stacks` is the largest difference between any pixel of the left stack of stones and its mirror pixel (straight
 * edges, so an exact mirror is 0); `mark` is the same over the whole mark, hull and ground line included (curves,
 * which anti-aliasing never draws twice the same to the last level of grey).
 */
async function markMirror(page: Page, dpr: number): Promise<{ axis: number; onGrid: boolean; stacks: number; mark: number }> {
  const box = await page.locator("[data-slot='identity']").getByRole("img", { name: "Noust" }).boundingBox();
  if (box === null) throw new Error("the logo has no box");
  const png = (await page.screenshot()).toString("base64");
  return page.evaluate(
    async ({ data, at, scale }) => {
      // Decoded without a request: the policy lets a page ask nobody for anything but its own files.
      const bytes = Uint8Array.from(atob(data), (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
      const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
      const context = canvas.getContext("2d");
      if (context === null) throw new Error("no canvas");
      context.drawImage(bitmap, 0, 0);
      const pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
      const at4 = (x: number, y: number): number => (y * bitmap.width + x) * 4;
      const lum = (x: number, y: number): number => 0.299 * (pixels[at4(x, y)] ?? 0) + 0.587 * (pixels[at4(x, y) + 1] ?? 0) + 0.114 * (pixels[at4(x, y) + 2] ?? 0);
      // The ground line is at unit 26 of 36: that fraction of the logo's height down from its top, whatever its size.
      const row = Math.floor((at.y + (26 / 36) * at.height) * scale);
      const from = Math.floor(at.x * scale);
      const background = lum(from - 4, row);
      // The mark is the first 140 of the 254 units of the wordmark; the letters begin 14 units after it.
      const limit = Math.floor((at.x + (146 / 254) * at.width) * scale);
      let first = -1;
      let last = -1;
      for (let x = from - 2; x < limit; x += 1) {
        if (Math.abs(lum(x, row) - background) > 40) {
          if (first < 0) first = x;
          last = x;
        }
      }
      if (first < 0) throw new Error("no ink where the ground line is");
      const axis = (first + last + 1) / 2;
      const onGrid = Number.isInteger(2 * axis);
      let stacks = 0;
      let mark = 0;
      if (onGrid) {
        // The stacks are 2.5 to 19.5 CSS px from the mark's left ink: units 5 to 39, at half a pixel to the unit.
        const stackFrom = first + 2 * scale;
        const stackTo = first + 20 * scale;
        for (let y = Math.floor(at.y * scale); y < Math.ceil((at.y + at.height) * scale); y += 1) {
          for (let x = first; x < Math.floor(axis); x += 1) {
            const difference = Math.abs(lum(x, y) - lum(2 * axis - 1 - x, y));
            mark = Math.max(mark, difference);
            if (x >= stackFrom && x < stackTo) stacks = Math.max(stacks, difference);
          }
        }
      }
      return { axis, onGrid, stacks, mark };
    },
    { data: png, at: box, scale: dpr },
  );
}

for (const dpr of [1, 2]) {
  test.describe(`the logo at ${String(dpr)}x`, () => {
    test.use({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: dpr });

    test("is the same on both sides of its axis, stone for stone", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("heading", { level: 1, name: "Sign in" })).toBeVisible();
      await settle(page);
      const found = await markMirror(page, dpr);
      test.info().annotations.push({
        type: "mirror",
        description: `${String(dpr)}x: axis at ${String(found.axis)} device px, stacks differ by ${String(found.stacks)}, whole mark by ${String(found.mark)}`,
      });
      // The axis is on a pixel's edge or its centre, so every pixel has a mirror pixel...
      expect(found.onGrid, `the mark's axis is at ${String(found.axis)}, between pixels`).toBe(true);
      // ...and the two stacks are drawn alike, pixel for pixel, but for the round ends of the bars, which are curves and
      // which the browser's anti-aliasing never draws twice the same (a few levels of grey of 255); the hull and the ground
      // line, curves too, are held to a little more.
      expect(found.stacks, "the stacks of stones are not drawn alike").toBeLessThanOrEqual(8);
      expect(found.mark, "the mark is not drawn alike on both sides").toBeLessThanOrEqual(30);
    });
  });
}

/** The access token and, when the server asks for it, its second factor: the real sign-in, up to the page it opens. */
async function signInWithToken(page: Page, server: ConsoleServer): Promise<void> {
  await page.goto("/login?with=token");
  await page.getByLabel("Access token").fill(server.token);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  if (server.totpSecret !== null) {
    const code = page.getByLabel("Two-factor code");
    await expect(code).toBeFocused();
    await code.fill(server.secondFactor());
    await page.getByRole("button", { name: "Verify" }).click();
  }
  await expect(page).not.toHaveURL(/\/login/);
}

test("the access token's second factor, with the real server, is asked for in the same slab", async ({ page, consoleServer }, testInfo) => {
  test.skip(consoleServer.totpSecret === null, "this server has no second factor");
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/login?with=token");
  await expect(page.getByLabel("Access token")).toBeFocused();
  const before = await expectSlab(page, 1440);
  await page.getByLabel("Access token").fill(consoleServer.token);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  const code = page.getByLabel("Two-factor code");
  await expect(code).toBeFocused();
  await expect(page.getByText("accepted")).toBeVisible();
  const after = await expectSlab(page, 1440);
  // The step changed what is in the form and nothing else: the slab is where it was, as wide as it was.
  expect(after.slab.x).toBeCloseTo(before.slab.x, 0);
  expect(after.slab.width).toBeCloseTo(before.slab.width, 0);
  await capture(page, testInfo.project.name, "1440", "token-second-factor");
  await expectNoA11yViolations(page, "the access token's second factor");

  await code.fill(consoleServer.secondFactor());
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page).not.toHaveURL(/\/login/);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
