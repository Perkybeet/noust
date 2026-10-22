/**
 * The design contract, checked in the browser (docs/DESIGN.md, "Enforcement"). What no static
 * rule can see - inline styles, inherited values, a library's own CSS, an alpha mixed with
 * what is under it - is caught here on the computed style of every visible element:
 *
 * - text is set in one of the seven sizes (or a system value's 0.92em of one), in Mona Sans
 *   or JetBrains Mono, at 400, 500 or 600;
 * - corners are 0, 4, 6, 10 or a pill; borders are 0, 1 or 2 pixels;
 * - every opaque colour is grey or one of the tokens of tokens.css, in either theme;
 * - a view has at most one primary button outside a dialog, and at most one banner;
 * - and that last, on every route of e2e/routes.ts, as the page loads and again with unsaved changes
 *   (the header's primary yields to the save bar's, so there is still one), on a desk and on a phone.
 *
 * The routes are a sample of every template in use; the design gallery (/__design) is audited
 * too when the build under test includes it, which only a development build does:
 *
 *   NODE_ENV=development npx vite build --mode development --outDir /tmp/noust-dev-build
 *   NOUST_E2E_STATIC=/tmp/noust-dev-build npx playwright test design-contract
 *
 * Specimens that show a mistake on purpose (the gallery's "Don't" column) carry
 * `data-design-exception` and are skipped.
 */

import type { Page } from "@playwright/test";

import type { ConsoleServer } from "./fixtures";
import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import { ROUTES as EVERY_ROUTE, routePath } from "./routes";

const ROUTES = ["/", "/apps", "/apps/shop.example.net", "/apps/shop.example.net/settings", "/apps/shop.example.net/settings/build", "/backups", "/catalog", "/catalog/vaultwarden", "/domains", "/settings"];

/**
 * Views that still show more than one primary button, and how many: the debt wave 2 pays by
 * moving them onto their template. A count may only go down, and a view leaves this list when
 * it reaches one.
 */
const PRIMARY_DEBT: Readonly<Record<string, number>> = {};

const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
];

interface Violation {
  rule: string;
  value: string;
  element: string;
}

/**
 * Walks every visible element of the page and returns what breaks the contract. Runs in the
 * page: it resolves the tokens from the stylesheet the page actually loaded, in both themes.
 */
function auditPage(): Violation[] {
  const SIZES = [12, 13, 14, 16, 18, 24, 32];
  const FAMILIES = ["Mona Sans Variable", "JetBrains Mono Variable"];
  const WEIGHTS = ["400", "500", "600"];
  const RADII = [0, 4, 6, 10];
  const BORDERS = [0, 1, 2];

  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("no 2d context");
  /** Any CSS colour as sRGB bytes and alpha, however the browser serialises it (rgb, oklab...). */
  const bytes = (colour: string): [number, number, number, number] => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = "rgba(0, 0, 0, 0)";
    context.fillStyle = colour;
    context.fillRect(0, 0, 1, 1);
    const [r = 0, g = 0, b = 0, a = 0] = context.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a];
  };

  // The colour tokens, from the :root rule of the stylesheet itself.
  const names = new Set<string>();
  for (const sheet of document.styleSheets) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    const visit = (list: CSSRuleList): void => {
      for (const rule of list) {
        if (rule instanceof CSSStyleRule && rule.selectorText === ":root") {
          for (const property of rule.style) {
            const value = rule.style.getPropertyValue(property);
            // light-dark(#a, #b) as written, or as Lightning CSS lowers it for older browsers:
            // var(--lightningcss-light, #a) var(--lightningcss-dark, #b).
            if (property.startsWith("--") && /#[0-9a-fA-F]{3,8}\b/.test(value)) names.add(property);
          }
        }
        if ("cssRules" in rule) visit((rule as CSSGroupingRule).cssRules);
      }
    };
    visit(rules);
  }
  const palette: [number, number, number][] = [];
  for (const theme of ["light", "dark"]) {
    const host = document.createElement("div");
    host.dataset.theme = theme;
    document.body.append(host);
    for (const name of names) {
      const probe = document.createElement("span");
      probe.style.color = `var(${name})`;
      host.append(probe);
      const [r, g, b] = bytes(getComputedStyle(probe).color);
      palette.push([r, g, b]);
    }
    host.remove();
  }
  const near = (a: number, b: number): boolean => Math.abs(a - b) <= 1;
  const allowedColour = (colour: string): boolean => {
    const [r, g, b, a] = bytes(colour);
    // Translucent colours (a veil, a blurred bar) depend on what is under them: the static
    // ratchet (alpha-on-token) watches those.
    if (a < 255) return true;
    if (Math.max(r, g, b) - Math.min(r, g, b) <= 2) return true;
    return palette.some(([pr, pg, pb]) => near(r, pr) && near(g, pg) && near(b, pb));
  };

  const describe = (element: Element): string => {
    const id = element.id ? `#${element.id}` : "";
    const classes = typeof element.className === "string" ? element.className.trim().split(/\s+/).slice(0, 6).join(".") : "";
    const text = element.textContent.trim().slice(0, 40);
    return `<${element.tagName.toLowerCase()}${id}${classes ? `.${classes}` : ""}> "${text}"`;
  };

  const violations: Violation[] = [];
  const seen = new Set<string>();
  const report = (rule: string, value: string, element: Element): void => {
    const key = `${rule}|${value}|${describe(element)}`;
    if (seen.has(key)) return;
    seen.add(key);
    violations.push({ rule, value, element: describe(element) });
  };

  for (const element of document.body.querySelectorAll("*")) {
    if (element.closest("[data-design-exception], svg, canvas, .uplot, noscript, script, style")) continue;
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") continue;
    if (element.getClientRects().length === 0) continue;

    const hasText = [...element.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? "").trim() !== "");
    if (hasText && !element.closest(".sr-only")) {
      const size = Number.parseFloat(style.fontSize);
      const ok = SIZES.some((role) => Math.abs(size - role) < 0.05 || Math.abs(size - role * 0.92) < 0.05);
      if (!ok) report("font-size", style.fontSize, element);
      const family = (style.fontFamily.split(",")[0] ?? "").trim().replace(/^["']|["']$/g, "");
      if (!FAMILIES.includes(family)) report("font-family", family, element);
      if (!WEIGHTS.includes(style.fontWeight)) report("font-weight", style.fontWeight, element);
      if (!allowedColour(style.color)) report("color", style.color, element);
    }

    for (const corner of ["borderTopLeftRadius", "borderTopRightRadius", "borderBottomLeftRadius", "borderBottomRightRadius"] as const) {
      const value = style[corner];
      if (value.endsWith("%")) continue;
      const radius = Number.parseFloat(value);
      if (!(RADII.includes(Math.round(radius * 100) / 100) || radius >= 999)) report("border-radius", value, element);
    }

    for (const side of ["Top", "Right", "Bottom", "Left"] as const) {
      const width = Number.parseFloat(style[`border${side}Width`]);
      if (style[`border${side}Style`] === "none" || width === 0) continue;
      if (!BORDERS.includes(Math.round(width))) report("border-width", `${String(width)}px`, element);
      if (!allowedColour(style[`border${side}Color`])) report("border-color", style[`border${side}Color`], element);
    }

    if (!allowedColour(style.backgroundColor)) report("background-color", style.backgroundColor, element);
  }
  return violations;
}

async function audit(page: Page): Promise<string[]> {
  const violations = await page.evaluate(auditPage);
  return violations.map((violation) => `${violation.rule} ${violation.value} on ${violation.element}`);
}

/** Visible primary buttons outside a dialog: the accent marks the one thing a view is for. */
async function primaries(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('main [data-variant="primary"]')]
      .filter((button) => !button.closest('[role="dialog"], [role="alertdialog"], [data-design-exception]'))
      .filter((button) => button.getClientRects().length > 0 && getComputedStyle(button).visibility !== "hidden")
      // A split button is one control of two buttons: its arrow is part of the primary beside it, not another.
      .filter((button) => button.textContent.trim() !== "" || button.closest('[data-slot="split-button"]') === null)
      .map((button) => button.textContent.trim()),
  );
}

/**
 * Visible banners outside a dialog, by the title they carry: a page states the condition of the page or the
 * server once (NT-3), and with several conditions shows the worst or an aggregate.
 */
async function banners(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll('[data-variant="banner"]')]
      .filter((banner) => !banner.closest('[role="dialog"], [role="alertdialog"], [data-design-exception]'))
      .filter((banner) => banner.getClientRects().length > 0 && getComputedStyle(banner).visibility !== "hidden")
      .map((banner) => banner.textContent.trim().slice(0, 60)),
  );
}

/**
 * Pages whose bar counts something other than a field typed in: a variable staged from its dialog.
 * The route's name in e2e/routes.ts, and what an operator does to leave a change on the bar.
 */
const STAGERS: Readonly<Record<string, (page: Page) => Promise<string>>> = {
  "app-environment": async (page) => {
    await page.getByRole("button", { name: "Add variable" }).click();
    const dialog = page.getByRole("dialog", { name: "Add a variable" });
    await dialog.getByLabel("Name").fill("BUDGET_PROBE");
    await dialog.getByLabel("Value").fill("1");
    await dialog.getByRole("button", { name: /^(Add variable|Add|Update variable)$/ }).click();
    return "a variable staged";
  },
};

/**
 * Makes the page's save bar hold a change, the way an operator does: by typing in a field of the page, and
 * failing back to a checkbox (or by what the page stages, `STAGERS`). Returns what it touched, or null when the
 * page has a bar and nothing in it changes the bar's count (a page that says so in the failure).
 */
async function makeDirty(page: Page, route: string): Promise<string | null> {
  const bar = page.getByRole("region", { name: "Unsaved changes" });
  const counted = bar.getByText(/^\d+ unsaved changes?$/);
  const stage = STAGERS[route];
  if (stage !== undefined) {
    const touched = await stage(page);
    return (await counted.waitFor({ timeout: 3_000 }).then(() => true, () => false)) ? touched : null;
  }
  const fields = page.locator(
    'main :is(input:not([type]), input[type="text"], input[type="number"], input[type="email"], input[type="url"], textarea):not([disabled]):not([readonly]):not([aria-hidden="true"]):not([tabindex="-1"])',
  );
  const candidates = [
    ...(await fields.all()),
    ...(await page.locator('main input[type="checkbox"]:not([disabled]):not([aria-hidden="true"])').all()),
    // A choice among a few (a segmented control): the one that is not chosen.
    ...(await page.locator('main [role="radio"][aria-checked="false"]:not([aria-disabled="true"])').all()),
  ].slice(0, 16);
  for (const field of candidates) {
    if (!(await field.isVisible()) || (await field.evaluate((node) => node.closest('[role="region"][aria-label="Unsaved changes"], [role="dialog"], [data-slot="filter-bar"]') !== null))) continue;
    const kind = (await field.getAttribute("role")) ?? (await field.evaluate((node) => (node instanceof HTMLInputElement ? node.type : node.tagName.toLowerCase())));
    const pressed = kind === "checkbox" || kind === "radio";
    if (pressed) await field.click({ timeout: 3_000 });
    else {
      await field.click({ timeout: 3_000 });
      await page.keyboard.press("End");
      await page.keyboard.type("1");
    }
    if (await counted.waitFor({ timeout: 1_000 }).then(() => true, () => false)) return (await field.getAttribute("aria-label")) ?? kind;
    // Put it back: a field that does not count is not what the page saves.
    if (pressed) await field.click({ timeout: 3_000 }).catch(() => undefined);
    else await page.keyboard.press("Backspace");
  }
  return null;
}

/** Counts what a view must hold to one, now: primaries and banners. */
async function oneEach(page: Page, where: string): Promise<string[]> {
  const found: string[] = [];
  const buttons = await primaries(page);
  if (buttons.length > 1) found.push(`${where}: ${String(buttons.length)} primary buttons (${buttons.join(", ")})`);
  const notices = await banners(page);
  if (notices.length > 1) found.push(`${where}: ${String(notices.length)} banners (${notices.join(" | ")})`);
  return found;
}

/**
 * Routes that still break the rule, and why: the debt the closing work pays. A route is on this list only with its
 * reason, it must still break the rule to stay (a route that is clean leaves the list, so it only gets shorter), and
 * the list is empty when the 4.0 work is whole.
 */
const ONE_EACH_DEBT: Readonly<Record<string, string>> = {
  "app-settings-mounts": "an empty state draws a second primary beside the header's (FM-2)",
  "backups-noust": "an empty state draws a second primary beside the header's (FM-2)",
  "backups-volumes": "an empty state draws a second primary beside the header's (FM-2)",
  "settings-alerts": "the header and the empty state both draw New rule, and the form's Save is a third",
  "settings-probes": "the header and the empty state both draw New probe",
};

// Every route of the console, clean and dirty, on a desk and on a phone, in both themes: a view has one primary
// action (the save bar's takes it over from the header's while it holds changes, FM-2) and states the condition of
// the page once. A split button counts as one primary.
for (const route of EVERY_ROUTE) {
  test(`${route.name} has one primary and at most one banner, clean and with unsaved changes`, async ({ page, consoleServer }) => {
    test.setTimeout(120_000);
    await signIn(page, consoleServer);
    const target = await routePath(page, route);
    const problems: string[] = [];
    for (const viewport of VIEWPORTS) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto(target);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      const where = `${target} at ${viewport.name}`;
      problems.push(...(await oneEach(page, `${where}, as it loads`)));

      // A page with a save bar is asked again while the bar holds a change.
      const bar = page.getByRole("region", { name: "Unsaved changes" });
      if ((await bar.count()) === 0 || !(await bar.first().isVisible())) continue;
      const touched = await makeDirty(page, route.name);
      if (touched === null) {
        problems.push(`${where}: the page has a save bar and no field on it changes the bar's count`);
        continue;
      }
      await stillness(page);
      problems.push(...(await oneEach(page, `${where}, with unsaved changes (${touched})`)));
      await bar.getByRole("button", { name: "Discard" }).click();
    }
    const debt = ONE_EACH_DEBT[route.name];
    if (debt === undefined) {
      expect(problems, problems.join("\n")).toEqual([]);
    } else {
      // Held to its list: it still breaks the rule, and it is told why; when it stops, it leaves the list.
      expect(problems.length, `${route.name} is clean now: take it off ONE_EACH_DEBT (${debt})`).toBeGreaterThan(0);
    }
  });
}

for (const viewport of VIEWPORTS) {
  for (const route of ROUTES) {
    test(`${route} at ${viewport.name} keeps to the design contract`, async ({ page, consoleServer }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await signIn(page, consoleServer);
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      const broken = await audit(page);
      expect(broken, `${route} breaks the design contract:\n${broken.slice(0, 40).join("\n")}`).toEqual([]);
      const buttons = await primaries(page);
      const allowed = PRIMARY_DEBT[route] ?? 1;
      expect(buttons.length, `${route} has ${String(buttons.length)} primary buttons: ${buttons.join(", ")}`).toBeLessThanOrEqual(allowed);
      if (allowed > 1 && viewport.name === "1440") {
        expect(buttons.length, `${route} is down to ${String(buttons.length)} primary buttons: lower PRIMARY_DEBT`).toBe(allowed);
      }
    });
  }
}

/**
 * Opens the gallery in a theme and width, or skips the test when the build has none (only a
 * development build includes it, see the header of this file).
 */
async function openGallery(page: Page, server: ConsoleServer, theme: "light" | "dark" | null, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await signIn(page, server);
  await page.goto(theme === null ? "/__design" : `/__design?theme=${theme}`);
  // By CSS and not by role: below 1024px the gallery's own navigation is hidden, and a role
  // locator does not see what is hidden, which would skip every phone-width test.
  const gallery = page.locator('nav[aria-label="Design system"]');
  const present = await gallery
    .waitFor({ state: "attached", timeout: 5_000 })
    .then(() => true)
    .catch(() => false);
  test.skip(!present, "The gallery is only in a development build (see the header of this file).");
  await settle(page);
}

test.describe("the design gallery", () => {
  test("keeps to its own contract and passes axe, when the build includes it", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer, null, 1440);
    const broken = await audit(page);
    expect(broken, `The gallery breaks the design contract:\n${broken.slice(0, 40).join("\n")}`).toEqual([]);
    await expectNoA11yViolations(page, "the design gallery");
  });

  // The 4.0 kit, in both themes at both widths: every specimen is audited, the mistakes it
  // prevents (the "Don't" column) are not.
  for (const theme of ["light", "dark"] as const) {
    for (const width of [1440, 390]) {
      test(`keeps the 4.0 kit to the contract and to axe in the ${theme} theme at ${String(width)}`, async ({ page, consoleServer }) => {
        await openGallery(page, consoleServer, theme, width);
        const kit = page.locator('[data-gallery-section^="k40-"]');
        expect(await kit.count()).toBeGreaterThanOrEqual(13);
        const broken = await audit(page);
        expect(broken, `The gallery breaks the design contract in ${theme} at ${String(width)}:\n${broken.slice(0, 40).join("\n")}`).toEqual([]);
        await expectNoA11yViolations(page, `the 4.0 kit, ${theme} at ${String(width)}`);
      });
    }
  }

  test.describe("a setting row", () => {
    for (const width of [1440, 390]) {
      test(`is 44px without help and 60px with one line of help at ${String(width)}`, async ({ page, consoleServer }) => {
        await openGallery(page, consoleServer, null, width);
        const height = async (specimen: string): Promise<number> => {
          const row = page.locator(`[data-gallery-specimen="${specimen}"] [data-slot="setting"]`);
          await row.scrollIntoViewIfNeeded();
          const box = await row.boundingBox();
          if (box === null) throw new Error(`${specimen} has no box`);
          return Math.round(box.height);
        };
        expect(await height("setting-plain")).toBe(44);
        expect(await height("setting-help")).toBe(60);
      });
    }

    test("keeps a small button from making its row taller than 60px", async ({ page, consoleServer }) => {
      await openGallery(page, consoleServer, null, 1440);
      const group = page.locator('[data-gallery-section="k40-setting"] [data-theme="light"] [data-slot="settings-group"]').last();
      const row = group.locator('[data-slot="setting"]', { hasText: /^Instant rollback/ });
      await row.scrollIntoViewIfNeeded();
      const box = await row.boundingBox();
      expect(Math.round(box?.height ?? 0)).toBe(60);
    });
  });

  test("keeps one primary in the view while the form is dirty: the header's yields, Save takes it", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer, null, 1440);
    const specimen = page.locator('[data-gallery-specimen="primary-yield"]');
    await specimen.scrollIntoViewIfNeeded();
    const primaries = async (): Promise<string[]> => specimen.locator('[data-variant="primary"]').evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
    expect(await primaries()).toEqual(["Update"]);
    const update = specimen.getByRole("button", { name: "Update" });
    const before = await update.boundingBox();
    await specimen.getByRole("button", { name: "Edit a field" }).click();
    await expect.poll(primaries).toEqual(["Save"]);
    // It changes colour and nothing else: the label is the same and so is the box.
    await expect(update).toHaveAttribute("data-variant", "secondary");
    expect(await update.boundingBox()).toEqual(before);
    await specimen.getByRole("button", { name: "Discard" }).click();
    await expect.poll(primaries).toEqual(["Update"]);
  });

  test("draws a split button as one control: the same height, one ring round the pair", async ({ page, consoleServer }) => {
    await openGallery(page, consoleServer, "light", 1440);
    const group = page.locator('[data-gallery-section="k40-split"] [data-theme="light"] [data-slot="split-button"]').first();
    await group.scrollIntoViewIfNeeded();
    const [main, arrow] = await group.locator("button").all();
    if (main === undefined || arrow === undefined) throw new Error("a split button has two buttons");
    const heights = await Promise.all([main.boundingBox(), arrow.boundingBox()]).then(([a, b]) => [a?.height, b?.height]);
    expect(heights[0]).toBe(heights[1]);
    expect(heights[0]).toBe(32);
    // By keyboard, so the ring is the focus-visible one: out of the pair and back in at its first part.
    await main.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    await expect(main).toBeFocused();
    const outline = await Promise.all([
      group.evaluate((node) => getComputedStyle(node).outlineStyle),
      main.evaluate((node) => getComputedStyle(node).outlineStyle),
    ]);
    // The pair draws the ring; the focused part draws none of its own.
    expect(outline).toEqual(["solid", "none"]);
  });
});
