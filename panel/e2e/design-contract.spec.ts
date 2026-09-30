/**
 * The design contract, checked in the browser (docs/DESIGN.md, "Enforcement"). What no static
 * rule can see - inline styles, inherited values, a library's own CSS, an alpha mixed with
 * what is under it - is caught here on the computed style of every visible element:
 *
 * - text is set in one of the seven sizes (or a system value's 0.92em of one), in Mona Sans
 *   or JetBrains Mono, at 400, 500 or 600;
 * - corners are 0, 4, 6, 10 or a pill; borders are 0, 1 or 2 pixels;
 * - every opaque colour is grey or one of the tokens of tokens.css, in either theme;
 * - a view has at most one primary button outside a dialog.
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

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const ROUTES = ["/", "/apps", "/apps/shop.example.net", "/apps/shop.example.net/settings", "/backups", "/domains", "/settings"];

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
      .map((button) => button.textContent.trim()),
  );
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

test.describe("the design gallery", () => {
  test("keeps to its own contract and passes axe, when the build includes it", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer);
    await page.goto("/__design");
    const gallery = page.getByRole("navigation", { name: "Design system" });
    const present = await gallery
      .waitFor({ state: "attached", timeout: 5_000 })
      .then(() => true)
      .catch(() => false);
    test.skip(!present, "The gallery is only in a development build (see the header of this file).");
    await settle(page);
    const broken = await audit(page);
    expect(broken, `The gallery breaks the design contract:\n${broken.slice(0, 40).join("\n")}`).toEqual([]);
    await expectNoA11yViolations(page, "the design gallery");
  });
});
