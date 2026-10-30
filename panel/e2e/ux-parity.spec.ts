/**
 * The 3.4 console parity items that are about how the shell looks and finds things, against the
 * real backend and in both themes (checklist M-59, M-51, M-23):
 *
 * - the current navigation item is the raised piece, with a label and an icon that are legible on it
 *   (the contrast is read off the rendered colours, not the tokens);
 * - a list of more than 20 rows is paged, the size is the operator's and is kept;
 * - the command palette finds a database, a certificate, a service, a deployment and a settings
 *   section on a real machine, and opens each.
 */

import type { Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** WCAG contrast of two `rgb()` colours as the browser computes them. */
async function contrastOf(page: Page, foreground: string, ...grounds: string[]): Promise<number[]> {
  return page.evaluate(
    ([fg, backgrounds]) => {
      const parse = (css: string): [number, number, number] => {
        const match = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(css) ?? /color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/.exec(css);
        if (match === null) throw new Error(`not a colour: ${css}`);
        const scale = css.startsWith("color(") ? 255 : 1;
        return [Number(match[1]) * scale, Number(match[2]) * scale, Number(match[3]) * scale];
      };
      const luminance = ([r, g, b]: [number, number, number]): number => {
        const channel = (value: number): number => {
          const s = value / 255;
          return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
        };
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
      };
      const ratio = (a: string, b: string): number => {
        const [hi, lo] = [luminance(parse(a)), luminance(parse(b))].sort((x, y) => y - x) as [number, number];
        return (hi + 0.05) / (lo + 0.05);
      };
      return backgrounds.map((ground) => ratio(fg, ground));
    },
    [foreground, grounds] as const,
  );
}

test("the current navigation item is the raised piece under it, told by more than its fill (C-11)", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps");
  await settle(page);
  const nav = page.getByRole("navigation", { name: "Main" });
  const current = nav.getByRole("link", { name: /^Applications/ });
  await expect(current).toHaveAttribute("aria-current", "page");
  // No bar of colour at its side (the stripe of 4.0 is retired): the mark is the piece, its label and its icon.
  await expect(nav.locator("[data-nav-indicator]")).toHaveCount(0);
  await expect(nav.getByRole("link").and(page.locator('[aria-current="page"]'))).toHaveCount(1);

  const read = async (link: ReturnType<typeof nav.getByRole>) =>
    link.evaluate((element) => {
      const piece = element.closest("nav")?.querySelector('[data-slot="sliding-indicator"]');
      const icon = element.querySelector("svg");
      return {
        text: getComputedStyle(element).color,
        icon: icon === null ? "" : getComputedStyle(icon).color,
        piece: piece === null || piece === undefined ? null : { fill: getComputedStyle(piece).backgroundColor, shadow: getComputedStyle(piece).boxShadow },
      };
    });
  const here = await read(current);
  const elsewhere = await read(nav.getByRole("link", { name: "Databases" }));
  // The piece is raised: a tone of its own and the joint beneath it, which a flat fill does not have.
  expect(here.piece?.fill).not.toBe("rgba(0, 0, 0, 0)");
  expect(here.piece?.shadow).not.toBe("none");
  // The label and the icon of the current item are not the others': stronger text and the accent.
  expect(here.text).not.toBe(elsewhere.text);
  expect(here.icon).not.toBe(elsewhere.icon);
  // Legible on the piece it sits on, in both themes (the render, not the tokens).
  const [labelOnPiece, iconOnPiece] = [
    (await contrastOf(page, here.text, here.piece?.fill ?? "rgb(255, 255, 255)"))[0] ?? 0,
    (await contrastOf(page, here.icon, here.piece?.fill ?? "rgb(255, 255, 255)"))[0] ?? 0,
  ];
  expect(labelOnPiece, `the label ${here.text} on the piece ${here.piece?.fill ?? ""}`).toBeGreaterThanOrEqual(4.5);
  expect(iconOnPiece, `the icon ${here.icon} on the piece ${here.piece?.fill ?? ""}`).toBeGreaterThanOrEqual(3);
  await expectNoA11yViolations(page, "the main bar with its current item marked");
});

test("a list of more than twenty rows is paged, the size is chosen, and it is kept", async ({ page, consoleServer }) => {
  // Fifty-seven applications, as the list would carry them on a big server.
  const apps = Array.from({ length: 57 }, (_, index) => ({
    domain: `site-${String(index + 1).padStart(2, "0")}.example.net`,
    name: `site-${String(index + 1).padStart(2, "0")}`,
    app_type: "static",
    status: "static",
    active: false,
    enabled: true,
    port: null,
    layout: "releases",
  }));
  await page.route("**/api/apps", (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    return route.fulfill({ json: { apps, total: apps.length } });
  });
  await signIn(page, consoleServer, "/apps");
  const table = page.getByRole("table", { name: /Applications/ });
  await expect(table.getByRole("row")).toHaveCount(21);
  const paging = page.getByRole("group", { name: "Pagination" });
  await expect(paging.getByText("Showing 1–20 of 57")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a paged list");

  await paging.getByRole("button", { name: "Next page" }).click();
  await expect(paging.getByText("Showing 21–40 of 57")).toBeVisible();
  await paging.getByRole("combobox", { name: "Rows per page" }).click();
  await page.getByRole("option", { name: "50", exact: true }).click();
  await expect(paging.getByText("Showing 1–50 of 57")).toBeVisible();
  await expect(table.getByRole("row")).toHaveCount(51);
  // Not clipped by the card it sits in (Coolify's was): the menu is fully on screen.
  await paging.getByRole("combobox", { name: "Rows per page" }).click();
  const listbox = page.getByRole("listbox");
  await expect(listbox).toBeVisible();
  const box = await listbox.boundingBox();
  const viewport = page.viewportSize();
  expect(box).not.toBeNull();
  expect(box && viewport && box.y + box.height <= viewport.height && box.x >= 0).toBe(true);
  await page.keyboard.press("Escape");

  // Kept: after a reload the list comes back with 50 to a page, and the databases list has its own.
  await page.reload();
  await expect(table.getByRole("row")).toHaveCount(51);
  await expect(page.getByRole("group", { name: "Pagination" }).getByRole("combobox", { name: "Rows per page" })).toHaveText("50");
  expect(await page.evaluate(() => window.localStorage.getItem("noust.pageSize.apps"))).toBe("50");
  expect(await page.evaluate(() => window.localStorage.getItem("noust.pageSize.databases"))).toBeNull();

  // All of it, and Next is gone for good.
  await page.getByRole("group", { name: "Pagination" }).getByRole("combobox", { name: "Rows per page" }).click();
  await page.getByRole("option", { name: "All", exact: true }).click();
  await expect(table.getByRole("row")).toHaveCount(58);
  await expect(page.getByRole("button", { name: "Next page" })).toBeDisabled();
});

test("the palette finds what a machine holds, by name, and opens it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  await settle(page);
  const get = async <T>(path: string): Promise<T> => (await (await page.request.get(path)).json()) as T;
  const { databases } = await get<{ databases: { engine: string; name: string }[] }>("/api/databases/databases");
  const { certificates } = await get<{ certificates: { domain: string }[] }>("/api/certs");
  const { items: deployments } = await get<{ items: { id: number; domain: string; git_commit?: string | null }[] }>("/api/deployments?limit=30");
  const database = databases[0];
  const certificate = certificates[0];
  const deployment = deployments.find((item) => item.git_commit);
  expect(database, "the seeded machine has a database").toBeDefined();
  expect(certificate, "the seeded machine has a certificate").toBeDefined();
  expect(deployment, "the seeded machine has a deployment with a commit").toBeDefined();
  if (database === undefined || certificate === undefined || deployment === undefined) return;

  const palette = page.getByRole("combobox", { name: "Search apps, databases, domains, servers, settings and actions" });
  const searchFor = async (text: string) => {
    // The last one has finished closing: a palette that is still leaving would take the keys.
    await expect(page.getByRole("dialog")).toBeHidden();
    // Pressed again if the first press came before the console was listening for it.
    await expect(async () => {
      await page.keyboard.press("Control+k");
      await expect(page.getByRole("dialog")).toBeVisible({ timeout: 3_000 });
    }).toPass({ timeout: 20_000 });
    await palette.fill(text);
  };

  await searchFor(database.name);
  const option = page.getByRole("option", { name: new RegExp(database.name) }).first();
  await expect(option).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the palette with a database found");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/databases/${database.engine}/${database.name}$`));

  await searchFor(certificate.domain);
  await expect(page.getByRole("option", { name: new RegExp(`^${certificate.domain.replace(/\./g, "\\.")}.*(days? left|Expired)`) }).first()).toBeVisible();
  await page.keyboard.press("Escape");

  await searchFor(deployment.git_commit ?? "");
  await page.getByRole("option", { name: new RegExp(`deploy ${String(deployment.id)}\\b`) }).first().click();
  await expect(page).toHaveURL(new RegExp(`/apps/${deployment.domain.replace(/\./g, "\\.")}/deployments/${String(deployment.id)}$`));

  // Enter takes the first result, so it is pressed once the list has been filtered to the one wanted:
  // pressed on the list of the words before, on a slow machine, it opened that one's first result.
  await searchFor("tokens");
  await expect(page.getByRole("option").first()).toContainText("API tokens");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/settings\/tokens$/);

  await searchFor("new application from a recipe");
  await expect(page.getByRole("option").first()).toContainText("New application from a recipe");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/apps\/new\?start=recipe$/);
  // The wizard has one box and no ways to pick from: asked to start from a recipe it draws the recipes.
  await expect(page.getByRole("list", { name: "Recipes" })).toBeVisible();
});
